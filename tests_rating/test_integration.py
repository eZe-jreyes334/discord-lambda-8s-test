from copy import deepcopy
from unittest.mock import MagicMock

import pytest


def test_cancel_is_atomic_and_cannot_later_rate(database,match,monkeypatch):
    import core.QueueManager as qm
    from core.RatingService import cancel_vote,commit_result
    from dao.RatingDao import RatingConflict
    match.cancel_votes=['0','1','2','3']
    database[2].put_queue(match)
    inter=MagicMock(guild_id='g',user_id='4',custom_id='cancel_match#q#'+match.match_id)
    monkeypatch.setattr(qm,'update_queue_embed',lambda record:record)
    after=cancel_vote(inter,'q',database[2])
    assert after.queue==['next']
    assert database[1].load('g')[1]==0
    with pytest.raises(RatingConflict):
        commit_result(match,'A',database[1])


def test_lobby_reservation_does_not_double_book_teams(database,match):
    from dao.TeamDao import TeamDao,TeamRecord
    from core.TeamManager import reserve_match
    td=TeamDao()
    a=TeamRecord('g','a','A','0',players=match.team_1,status='queued')
    b=TeamRecord('g','b','B','4',players=match.team_2,status='queued')
    td.put_team(a)
    td.put_team(b)
    match.queue_id='unique-lobby'
    match.version=0
    assert reserve_match(match,a,b)
    second=deepcopy(match)
    second.queue_id='another-lobby'
    second.version=0
    assert not reserve_match(second,a,b)
    assert database[2].get_queue_or_none('g','another-lobby') is None
    assert td.get_team('g','a').status=='in_match'


def test_leaderboard_uses_skill_not_legacy_points_and_keeps_provisionals(database):
    import json
    from dao.PlayerDao import PlayerRecord
    from rating.model import SkillState
    from core.RatingViews import leaderboard_entries
    state=SkillState()
    state.ensure(['skilled','grinder','new'])
    state.mu=[36,25,45]
    state.cov=[[16,0,0],[0,4,0],[0,0,30]]
    state.games=[25,200,10]
    state.wins=[17,100,10]
    state.last_played=[1000,2000,2000]
    store=database[1]
    store.client.put_item(TableName=store.table_name,Item=store._encode(dict(guild_id='g',
        record_id=store.snapshot_key('solo'),version=1,payload=json.dumps(state.to_dict()))))
    records=[PlayerRecord('g','skilled','Skilled',sr=50),PlayerRecord('g','grinder','Grinder',sr=900),PlayerRecord('g','new','New')]
    entries=leaderboard_entries('g',records)
    assert [e['name'] for e in entries]==['Skilled','Grinder','New']
    assert entries[0]['medal']=='`#1`'
    assert entries[2]['medal']=='`—`'
    assert 'Provisional 10/20' in entries[2]['status']


def test_stale_and_duplicate_draft_pick_rejected(database,match,monkeypatch):
    import core.QueueManager as qm
    match.team_1=['0']
    match.team_2=['4']
    match.queue=[str(i) for i in range(8)]
    database[2].put_queue(match)
    monkeypatch.setattr(qm,'queue_dao',database[2])
    inter=MagicMock(guild_id='g',user_id='0',custom_id='player_pick#0#q#'+match.match_id)
    assert qm.player_pick(inter,'q') is None
    inter.custom_id='player_pick#1#q#previous-match'
    assert qm.player_pick(inter,'q') is None
    assert database[2].get_queue('g','q').team_1==['0']


def test_pagination_and_rank_icons_preserved(database,monkeypatch):
    import core.LeaderboardManager as lm
    monkeypatch.setattr(lm,'build_leaderboard_entries',lambda guild:[dict(medal=f'#{i}',rank_emoji='<:Gold:1367281171729354812>',
        streak_emoji='',name=f'Player {i}',sr=350,delta='+0',wins=20,losses=20,status='Ranked',mu=25,sigma=3)
        for i in range(25)])
    embed,components=lm.build_leaderboard_page('g',99)
    assert 'Player 24' in embed.to_dict()['description']
    assert 'Player 19' not in embed.to_dict()['description']


def test_old_update_path_fails_closed_in_v2(database):
    from trueskillapi import TrueSkillAccessor
    with pytest.raises(RuntimeError,match='atomic'):
        TrueSkillAccessor().post_match(['a'],['b'],'g')


def test_v2_does_not_forgive_first_daily_loss(database,match):
    from core.RatingService import commit_result
    commit_result(match,'B',database[1])
    state,_=database[1].load('g')
    assert state.summary('0')['mu']<25
    assert state.summary('0')['losses']==1


def test_read_corrupt_covariance_rejected():
    from rating.model import SkillState
    state=SkillState()
    state.ensure(['a','b'])
    data=state.to_dict()
    data['cov']=[[1,2],[2,1]]
    with pytest.raises(ValueError,match='positive definite'):
        SkillState.from_dict(data)


def test_nonparticipant_cannot_resume_bracket_result(database,match,monkeypatch):
    import core.BracketManager as bm
    match.rating_result='A'
    monkeypatch.setattr(bm.queue_dao,'get_queue_or_none',lambda *args:match)
    advance=MagicMock()
    monkeypatch.setattr(bm,'_on_match_decided',advance)
    inter=MagicMock(guild_id='g',user_id='outsider')
    bm.report_winner(inter,'tournament','match','A')
    advance.assert_not_called()
    inter.send_followup.assert_called_once()
    inter.user_id='0'
    bm.report_winner(inter,'tournament','match','B')
    advance.assert_called_once_with(inter,'tournament','match','A')


def test_team_lobby_and_pool_use_v2_stats(database,monkeypatch):
    from dao.TeamDao import TeamRecord
    import core.TeamManager as tm
    from rating.model import SkillState
    team=TeamRecord('g','a','Team A','0',players=['0','1','2','3'],tmw=999,team_sr=999)
    state=SkillState()
    state.ensure(['a'])
    state.games[0]=25
    state.wins[0]=15
    state.cov[0][0]=16
    team.rating_v2=state.summary('a')
    assert '15W / 10L' in tm.build_team_lobby_embed(team).to_dict()['description']
    monkeypatch.setattr(tm.team_dao,'get_queued_teams',lambda guild:[team])
    embeds,_=tm.build_team_pool_embed('g','q')
    assert 'SR 350' in embeds[0].to_dict()['description']
    state.games[0]=10
    state.wins[0]=8
    team.rating_v2=state.summary('a')
    assert 'Provisional 10/20' in tm.build_team_lobby_embed(team).to_dict()['description']
