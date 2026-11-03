import pytest
from tools.replay_ratings import replay


def event(**kwargs):
    return dict(match_id='m1',timestamp=1000,winners=['a','b','c','d'],losers=['e','f','g','h'],**kwargs)


def test_replay_duplicate_identical_skipped():
    result=replay([event(),event()],'g','new')
    assert result['metrics']['matches']==1
    assert result['receipts'][0]['state_version']==1
    assert result['snapshot']['record_id']=='STATE#new#solo'


def test_replay_conflicting_duplicate_rejected():
    other=event()
    other['winners'],other['losers']=other['losers'],other['winners']
    with pytest.raises(ValueError,match='conflicting'):
        replay([event(),other],'g','new')


def test_replay_no_future_leakage_and_no_timestamp_sorting():
    result=replay([event()],'g','new')
    assert float(result['receipts'][0]['predicted_winner_probability'])==.5
    other=event()
    other.update(match_id='m2',timestamp=999)
    with pytest.raises(ValueError,match='chronological'):
        replay([event(),other],'g','new')


def test_empty_or_sr_only_history_cannot_seed_skill():
    with pytest.raises(ValueError):
        replay([],'g','new')
    with pytest.raises(KeyError):
        replay([{'player_id':'a','sr':800}],'g','new')
