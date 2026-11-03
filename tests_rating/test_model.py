"""Real math tests; intentionally outside the legacy tests' global module mocks."""
from dataclasses import replace
import json
import math
import random

import pytest
from trueskill import TrueSkill

from rating.model import Config,SkillState,rank_for_sr,correction

A=['0','1','2','3']
B=['4','5','6','7']


def test_first_update_matches_original_trueskill_moments():
    state=SkillState(replace(Config(),drift_per_day=0))
    ts=TrueSkill(draw_probability=0,tau=0)
    expected=ts.rate([[ts.create_rating() for _ in A],[ts.create_rating() for _ in B]],ranks=[0,1])
    assert state.predict(A,B)==.5
    state.update(A,B,1000)
    for pid,rating in zip(A+B,expected[0]+expected[1]):
        actual=state.summary(pid)
        assert actual['mu']==pytest.approx(rating.mu,abs=1e-6)
        assert actual['sigma']==pytest.approx(rating.sigma,abs=1e-6)


def test_always_paired_players_remain_indistinguishable():
    state=SkillState(replace(Config(),drift_per_day=0))
    state.ensure(A+B)
    initial=state.cov[0][0]+state.cov[1][1]-2*state.cov[0][1]
    for t in range(250):
        state.update(A if t%2 else B,B if t%2 else A,1000+t)
    assert state.pair_probability('0','1')==pytest.approx(.5)
    assert state.cov[0][0]+state.cov[1][1]-2*state.cov[0][1]==pytest.approx(initial)
    assert not state.summary('0')['eligible']
    assert state.summary('0')['status']=='Limited evidence'


def test_reading_inactive_rating_does_not_decay_or_mutate_it():
    state=SkillState()
    state.update(A,B,1700000000)
    before=json.dumps(state.to_dict())
    now=state.summary('0',1700000000+100*86400)
    assert now['inactive']
    assert now['sr']==state.summary('0')['sr']
    assert before==json.dumps(state.to_dict())


def test_drift_depends_on_elapsed_time_not_number_of_games():
    state=SkillState()
    state.ensure(A+B+['idle'])
    state.update(A,B,1000)
    before=state.mu[-1]
    variance=state.cov[-1][-1]
    for _ in range(50):
        state.update(A,B,1000)
    assert state.mu[-1]==before
    assert state.cov[-1][-1]==variance
    state.update(A,B,1000+86400)
    assert state.cov[-1][-1]==pytest.approx(variance+.25)


def test_underlying_score_has_no_sigma_or_games_bonus():
    state=SkillState()
    state.ensure(['a','b'])
    state.mu=[30,30]
    state.games=[25,200]
    state.cov=[[25,0],[0,4]]
    assert state.summary('a')['score']==state.summary('b')['score']
    assert state.summary('a')['sr']==state.summary('b')['sr']


def test_ten_lucky_fixed_team_wins_not_qualified():
    state=SkillState()
    for t in range(10):
        state.update(A,B,1000+t)
    assert state.summary('0')['games']==10
    assert not state.summary('0')['eligible']


@pytest.mark.parametrize('sr,rank',[(-10,0),(0,0),(99.9,0),(100,1),(199.9,1),(700,7),(1e6,7)])
def test_continuous_rank_boundaries(sr,rank):
    assert rank_for_sr(sr)==rank


@pytest.mark.parametrize('a,b',[(A,A),(A,B[:3]),([],[]),(['0','0'],['1','2'])])
def test_invalid_teams_rejected(a,b):
    with pytest.raises(ValueError):
        SkillState().update(a,b,1000)


def test_full_covariance_stays_positive_definite_and_roundtrips():
    import numpy as np
    rng=random.Random(3)
    state=SkillState()
    ids=list(map(str,range(25)))
    for t in range(500):
        roster=rng.sample(ids,8)
        state.update(roster[:4],roster[4:],1700000000+t*3600)
    assert np.linalg.eigvalsh(state.cov).min()>0
    loaded=SkillState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert loaded.to_dict()==state.to_dict()


def test_balance_has_no_duplicates_and_stays_near_best_probability():
    state=SkillState()
    state.ensure(A+B)
    state.mu=[40,32,30,27,25,22,19,12]
    a,b=state.balanced_teams(A+B,random.Random(8))
    assert sorted(a+b)==sorted(A+B)
    assert abs(state.predict(a,b)-.5)<.05


def test_extreme_upset_correction_finite():
    for t in (-1e4,-30,-11,-10,0,20,1e4):
        v,w=correction(t)
        assert math.isfinite(v) and 0<=w<1


def test_upset_moves_mean_more_than_expected_win():
    strong=SkillState()
    strong.ensure(A+B)
    strong.mu=[35]*4+[15]*4
    upset=SkillState.from_dict(json.loads(json.dumps(strong.to_dict())))
    strong.update(A,B,1000)
    upset.update(B,A,1000)
    assert upset.mu[4]-15 > strong.mu[0]-35


def test_non_chronological_match_rejected():
    state=SkillState()
    state.update(A,B,2000)
    with pytest.raises(ValueError):
        state.update(A,B,1000)


def test_qualification_does_not_change_ability_or_sr():
    state=SkillState()
    state.ensure(['a'])
    state.mu[0]=37
    state.cov[0][0]=16
    state.games[0]=19
    before=state.summary('a')
    state.games[0]=20
    after=state.summary('a')
    assert not before['eligible'] and after['eligible']
    assert before['sr']==after['sr']


def test_winning_team_estimated_advantage_increases_with_correlations():
    rng=random.Random(10)
    state=SkillState()
    ids=list(map(str,range(20)))
    for t in range(100):
        roster=rng.sample(ids,8)
        state.ensure(roster)
        a,b=roster[:4],roster[4:]
        advantage=lambda:sum(state.summary(p)['mu'] for p in a)-sum(state.summary(p)['mu'] for p in b)
        old=advantage()
        state.update(a,b,1000+t)
        assert advantage()>old
