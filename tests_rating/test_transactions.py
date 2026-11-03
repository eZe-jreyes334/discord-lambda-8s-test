from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from core.RatingService import commit_result,vote_result
from dao.RatingDao import RatingConflict,ResultMismatch,RatingDao


def test_one_transaction_and_duplicate_noop(database,match):
    _,store,qd=database
    changed,after=commit_result(match,'A',store)
    assert changed and after.queue==['next'] and after.team_1==[]
    assert commit_result(match,'A',store)[0] is False
    state,version=store.load('g')
    assert version==1 and all(n==1 for n in state.games)
    assert qd.get_queue('g','q').match_id is None
    assert store.receipt('g',match.match_id)['notification_status']=='pending'


def test_opposite_retry_rejected(database,match):
    store=database[1]
    commit_result(match,'A',store)
    with pytest.raises(ResultMismatch):
        commit_result(match,'B',store)


def test_stale_queue_rolls_back_snapshot_and_receipt(database,match):
    _,store,qd=database
    newer=qd.get_queue('g','q')
    newer.waitlist.append('later')
    qd.put_queue(newer)
    with pytest.raises(RatingConflict):
        commit_result(match,'A',store)
    assert store.load('g')[1]==0
    assert not store.receipt('g',match.match_id)
    assert qd.get_queue('g','q').team_1==match.team_1
    assert commit_result(newer,'A',store)[0]
    assert qd.get_queue('g','q').queue==['next','later']


def test_conflicting_snapshot_has_no_partial_queue_clear(database,match,monkeypatch):
    _,store,qd=database
    original=store.load
    state,version=store.load('g')
    store.client.put_item(TableName=store.table_name,Item=store._encode(dict(guild_id='g',
        record_id=store.snapshot_key('solo'),version=1,payload=json.dumps(state.to_dict()))))
    monkeypatch.setattr(store,'load',lambda *args:(deepcopy(state),version))
    with pytest.raises(RatingConflict):
        commit_result(match,'A',store)
    assert qd.get_queue('g','q').team_1
    assert not store.receipt('g',match.match_id)


def test_lost_response_retry_does_not_apply_again(database,match,monkeypatch):
    _,store,_=database
    original=store.client.transact_write_items
    def timeout(**kwargs):
        original(**kwargs)
        raise TimeoutError('Response lost after commit')
    monkeypatch.setattr(store.client,'transact_write_items',timeout)
    with pytest.raises(TimeoutError):
        commit_result(match,'A',store)
    assert commit_result(match,'A',store)[0] is False
    assert store.load('g')[1]==1


def test_no_receipt_expiry_or_season_replay(database,match):
    _,store,_=database
    commit_result(match,'A',store)
    assert 'ttl' not in store.receipt('g',match.match_id)
    store.season='new-season'
    assert commit_result(match,'A',store)[0] is False
    assert store.load('g')[1]==0


def test_legacy_player_data_unchanged(database,match):
    from dao.PlayerDao import PlayerDao,PlayerRecord
    pd=PlayerDao()
    p=PlayerRecord('g','0','Name',sr=777,elo=37,sigma=2,mw=200,ml=50)
    pd.put_player(p)
    before=pd.table.get_item(Key={'guild_id':'g','player_id':'0'})['Item']
    commit_result(match,'A',database[1])
    after=pd.table.get_item(Key={'guild_id':'g','player_id':'0'})['Item']
    assert before==after
    view=pd.get_player('g','0')
    assert view.sr==777 and view.rating_v2['games']==1
    assert view.get_effective_sr()!=777
    pd.put_player(view)
    assert 'rating_v2' not in pd.table.get_item(Key={'guild_id':'g','player_id':'0'})['Item']


def test_guild_isolation(database,match):
    from dao.PlayerDao import PlayerDao,PlayerRecord
    pd=PlayerDao()
    for guild in ('g','other'):
        pd.put_player(PlayerRecord(guild,'0',guild))
    commit_result(match,'A',database[1])
    assert pd.get_player('g','0').rating_v2['games']==1
    assert pd.get_player('other','0').rating_v2['games']==0


def test_failed_conditional_write_does_not_advance_local_version(database,match):
    qd=database[2]
    stale=deepcopy(match)
    qd.put_queue(match)
    assert qd.put_queue(stale) is None
    assert stale.version==1


def test_premade_ratings_separate_and_statuses_atomic(database,match):
    from dao.TeamDao import TeamDao,TeamRecord
    td=TeamDao()
    match.is_team_queue=True
    match.team_1_id='team-a'
    match.team_2_id='team-b'
    database[2].put_queue(match)
    for pid,players in [('team-a',match.team_1),('team-b',match.team_2)]:
        td.put_team(TeamRecord('g',pid,pid,players[0],players=players,status='in_match'))
    assert commit_result(match,'B',database[1],td)[0]
    assert database[1].load('g','solo')[1]==0
    state,version=database[1].load('g','team')
    assert version==1 and state.summary('team-b')['wins']==1
    assert td.get_team('g','team-a').status=='idle'
    assert td.get_team('g','team-b').status=='idle'


def test_bracket_retry_keeps_rosters_and_does_not_rate_twice(database,match):
    match.is_bracket=True
    database[2].put_queue(match)
    assert commit_result(match,'A',database[1])[0]
    saved=database[2].get_queue('g','q')
    assert saved.team_1==match.team_1 and saved.rating_result=='A'
    assert not commit_result(saved,'A',database[1])[0]
    assert database[1].load('g')[1]==1


def test_stale_button_cannot_vote_new_match(database,match):
    inter=MagicMock(guild_id='g',user_id='0',custom_id='team_1_won#q#old-id')
    assert vote_result(inter,'q','A',database[2]) is None
    assert database[2].get_queue('g','q').team1_votes==[]


def test_nonparticipant_cannot_vote(database,match):
    inter=MagicMock(guild_id='g',user_id='outsider',custom_id='team_1_won#q#'+match.match_id)
    assert vote_result(inter,'q','A',database[2]) is None
    assert database[1].load('g')[1]==0


def test_discord_failure_does_not_undo_result(database,match,monkeypatch):
    import core.QueueManager as qm
    match.team1_votes=['0','1','2','3']
    database[2].put_queue(match)
    monkeypatch.setattr(qm,'generate_match_done_embed',lambda *args:object())
    monkeypatch.setattr(qm,'update_queue_embed',lambda r:('queue','buttons'))
    inter=MagicMock(guild_id='g',user_id='4',custom_id='team_1_won#q#'+match.match_id)
    inter.send_message.side_effect=RuntimeError('Discord unavailable')
    assert vote_result(inter,'q','A',database[2])==('queue','buttons')
    assert database[1].load('g')[1]==1
    assert database[1].receipt('g',match.match_id)['notification_status']=='pending'


def test_invalid_roster_never_writes(database,match):
    match.team_2[0]=match.team_1[0]
    with pytest.raises(ValueError):
        commit_result(match,'A',database[1])
    assert database[1].load('g')[1]==0


def test_old_record_optional_fields_and_unknown_data_preserved(database):
    from dao.PlayerDao import PlayerDao
    from decimal import Decimal
    pd=PlayerDao()
    pd.table.put_item(Item=dict(guild_id='g',player_id='old',version=1,note='retain',other_number=Decimal('2.5')))
    old=pd.get_player('g','old')
    assert old.mw==0 and old.rating_v2['games']==0
    assert pd.put_player(old)
    saved=pd.table.get_item(Key=dict(guild_id='g',player_id='old'))['Item']
    assert saved['note']=='retain' and saved['other_number']==Decimal('2.5')
    assert isinstance(saved['mw'],Decimal)
