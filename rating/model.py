"""Joint Gaussian assumed-density filtering for two-team probit outcomes.

Unlike independent marginal TrueSkill, retain covariance between players.
One outcome gives one linear observation, not eight independent observations.
This is a Gaussian approximation after each outcome, not an exact posterior.
"""
from dataclasses import asdict, dataclass
from itertools import combinations
from math import erfc, exp, isfinite, log, pi, sqrt
import random


@dataclass(frozen=True)
class Config:
    mu: float = 25.0
    sigma: float = 25.0 / 3
    beta: float = 25.0 / 6
    drift_per_day: float = 0.25  # variance / day, not points / match
    uncertainty_weight: float = 0.0
    minimum_games: int = 20
    maximum_sigma: float = 6.0
    sr_center: float = 350.0
    sr_scale: float = 20.0
    inactive_days: int = 28
    max_players: int = 64

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not isfinite(value):
                raise ValueError(f"Non-finite config: {name}")
        if min(self.sigma, self.beta, self.sr_scale, self.maximum_sigma) <= 0:
            raise ValueError("Rating scales must be positive")
        if min(self.drift_per_day, self.uncertainty_weight) < 0:
            raise ValueError("Drift and uncertainty penalty cannot be negative")
        if self.minimum_games < 1 or not 8 <= self.max_players <= 64:
            raise ValueError("Invalid qualification or state-size limit")


def cdf(x):
    return 0.5 * erfc(-x / sqrt(2))


def correction(t):
    """Inverse Mills ratio and curvature, including the extreme upset tail."""
    if t < -10:
        z = -t
        # Asymptotic expansions avoid 0/0 and cancellation in v*(v+t).
        v = z + 1/z - 2/z**3 + 10/z**5 - 74/z**7
        w = 1 - 1/z**2 + 6/z**4 - 50/z**6 + 518/z**8
    else:
        v = exp(-t*t/2) / sqrt(2*pi) / max(cdf(t), 1e-300)
        w = v * (v + t)
    return v, min(1 - 1e-12, max(0.0, w))


def rank_for_sr(sr):
    if not isfinite(float(sr)):
        raise ValueError("Non-finite SR")
    return min(7, max(0, int(float(sr) // 100)))


class SkillState:
    schema_version = 2

    def __init__(self, config=None):
        self.config = config or Config()
        self.ids = []
        self.mu = []
        self.cov = []
        self.games = []
        self.wins = []
        self.last_played = []
        self.last_delta = []
        self.captain_counts = []
        self.streaks = []
        self.recent_teams = []
        self.timestamp = 0

    def ensure(self, ids):
        for pid in ids:
            if not isinstance(pid, str) or not pid or len(pid) > 128:
                raise ValueError("Player IDs must be nonempty strings <=128 characters")
            if pid in self.ids:
                continue
            if len(self.ids) >= self.config.max_players:
                raise ValueError("Rating pool is full; archive/start a reviewed new season")
            self.ids.append(pid)
            self.mu.append(self.config.mu)
            for row in self.cov:
                row.append(0.0)
            self.cov.append([0.0] * (len(self.ids)-1) + [self.config.sigma**2])
            self.games.append(0)
            self.wins.append(0)
            self.last_played.append(0)
            self.last_delta.append(0)
            self.captain_counts.append(0)
            self.streaks.append(0)

    @staticmethod
    def validate_teams(a, b, team_size=None):
        if not a or len(a) != len(b) or (team_size and len(a) != team_size):
            raise ValueError("Two equal, nonempty teams are required")
        if len(set(a+b)) != len(a+b):
            raise ValueError("Players cannot appear twice in a match")

    def _terms(self, a, b, timestamp=None):
        self.validate_teams(a, b)
        self.ensure(a+b)
        if timestamp is not None and (not isfinite(timestamp) or timestamp < self.timestamp):
            raise ValueError("Matches must be chronological")
        elapsed = max(0, (timestamp or self.timestamp) - self.timestamp) / 86400 if self.timestamp else 0
        drift = self.config.drift_per_day * elapsed
        x = [1 if pid in a else -1 if pid in b else 0 for pid in self.ids]
        cx = [sum(v*s for v,s in zip(row,x)) + drift*x[i] for i,row in enumerate(self.cov)]
        variance = sum(s*v for s,v in zip(x,cx)) + len(a+b)*self.config.beta**2
        if not isfinite(variance) or variance <= 0:
            raise ValueError("Invalid predictive variance")
        c = sqrt(variance)
        t = sum(s*m for s,m in zip(x,self.mu)) / c
        return x, cx, c, t, drift

    def predict(self, a, b, timestamp=None):
        return cdf(self._terms(a,b,timestamp)[3])

    def update(self, winners, losers, timestamp, captains=()):
        _, cx, c, t, drift = self._terms(winners, losers, timestamp)
        before = {pid:self.summary(pid)["sr"] for pid in winners+losers}
        v,w = correction(t)
        n = len(self.ids)
        for i in range(n):
            self.mu[i] += cx[i] / c * v
            for j in range(i+1):
                value = self.cov[i][j] + (drift if i == j else 0) - cx[i]*cx[j]/(c*c)*w
                if i == j and value <= 0:
                    raise ArithmeticError("Covariance lost positive variance")
                self.cov[i][j] = self.cov[j][i] = value
        self.timestamp = int(timestamp)
        for pid in winners+losers:
            i = self.ids.index(pid)
            self.games[i] += 1
            self.wins[i] += int(pid in winners)
            self.last_played[i] = int(timestamp)
            self.streaks[i] = max(0,self.streaks[i])+1 if pid in winners else min(0,self.streaks[i])-1
            self.last_delta[i] = self.summary(pid)["sr"] - before[pid]
        for pid in captains:
            if pid in winners+losers:
                self.captain_counts[self.ids.index(pid)] += 1
        self.recent_teams = (self.recent_teams + [list(winners),list(losers)])[-40:]
        return cdf(t)

    def summary(self, pid, now=None):
        if pid not in self.ids:
            mu, sigma, games, wins, last, delta = self.config.mu,self.config.sigma,0,0,0,0
        else:
            i = self.ids.index(pid)
            mu,sigma = self.mu[i],sqrt(self.cov[i][i])
            games,wins,last,delta = self.games[i],self.wins[i],self.last_played[i],self.last_delta[i]
        score = mu - self.config.uncertainty_weight*sigma
        sr = max(0, int(round(self.config.sr_center+self.config.sr_scale*(score-self.config.mu))))
        eligible = games >= self.config.minimum_games and sigma <= self.config.maximum_sigma
        status = "Ranked" if eligible else f"Provisional {games}/{self.config.minimum_games}" if games < self.config.minimum_games else "Limited evidence"
        inactive = bool(last and now and now-last >= self.config.inactive_days*86400)
        return dict(mu=mu,sigma=sigma,score=score,sr=sr,rank=rank_for_sr(sr),games=games,wins=wins,
                    losses=games-wins,last_played=last,delta=delta,eligible=eligible,status=status,inactive=inactive,
                    streak=self.streaks[self.ids.index(pid)] if pid in self.ids else 0)

    def pair_probability(self, a, b):
        self.ensure([a,b])
        i,j = self.ids.index(a),self.ids.index(b)
        variance = self.cov[i][i]+self.cov[j][j]-2*self.cov[i][j]
        return cdf((self.mu[i]-self.mu[j])/sqrt(max(variance,1e-12)))

    def balanced_teams(self, players, rng=None):
        if len(players) != 8 or len(set(players)) != 8:
            raise ValueError("Automatic balancing needs exactly eight unique players")
        self.ensure(players)
        candidates = []
        for others in combinations(players[1:],3):
            a = [players[0],*others]
            b = [p for p in players if p not in a]
            imbalance = abs(self.predict(a,b)-0.5)
            repeat = sum(len(set(team)&set(old))*(len(set(team)&set(old))-1)//2
                         for team in (a,b) for old in self.recent_teams)
            candidates.append((imbalance,repeat,a,b))
        best = min(c[0] for c in candidates)
        near = [c for c in candidates if c[0] <= best+0.02]
        repetition = min(c[1] for c in near)
        chosen = (rng or random).choice([c for c in near if c[1] == repetition])
        teams = [chosen[2],chosen[3]]
        (rng or random).shuffle(teams)
        return teams

    def captains(self, players, rng=None):
        self.ensure(players)
        pairs = list(combinations(players,2))
        distance = lambda pair: abs(self.mu[self.ids.index(pair[0])]-self.mu[self.ids.index(pair[1])])
        best = min(map(distance,pairs))
        near = [p for p in pairs if distance(p) <= best+1.0]
        count = lambda pair: sum(self.captain_counts[self.ids.index(p)] for p in pair)
        minimum = min(map(count,near))
        result = list((rng or random).choice([p for p in near if count(p) == minimum]))
        (rng or random).shuffle(result)
        return result

    def to_dict(self):
        return dict(schema_version=self.schema_version, config=asdict(self.config), ids=self.ids,
                    mu=self.mu,cov=self.cov,games=self.games,wins=self.wins,last_played=self.last_played,
                    last_delta=self.last_delta,captain_counts=self.captain_counts,
                    streaks=self.streaks,recent_teams=self.recent_teams,timestamp=self.timestamp)

    @classmethod
    def from_dict(cls, data):
        if int(data["schema_version"]) != cls.schema_version:
            raise ValueError("Unsupported rating schema; no automatic reset")
        result = cls(Config(**data["config"]))
        for key in ("ids","mu","cov","games","wins","last_played","last_delta","captain_counts","recent_teams","timestamp"):
            setattr(result,key,data[key])
        n = len(result.ids)
        result.streaks=data.get('streaks',[0]*n)
        if n > result.config.max_players or len(set(result.ids)) != n:
            raise ValueError("Invalid rating player index")
        if any(len(getattr(result,k)) != n for k in ("mu","cov","games","wins","last_played","last_delta","captain_counts","streaks")):
            raise ValueError("Invalid rating state dimensions")
        if any(len(row) != n for row in result.cov):
            raise ValueError("Invalid covariance dimensions")
        if not all(isfinite(v) for v in result.mu+[v for row in result.cov for v in row]):
            raise ValueError("Non-finite rating state")
        if any(result.cov[i][i] <= 0 for i in range(n)):
            raise ValueError("Non-positive rating variance")
        # Validate loaded covariance, including off-diagonal corruption, without numpy.
        lower=[[0.0]*n for _ in range(n)]
        for i in range(n):
            for j in range(i+1):
                if abs(result.cov[i][j]-result.cov[j][i])>1e-8:
                    raise ValueError("Asymmetric covariance")
                value=result.cov[i][j]-sum(lower[i][k]*lower[j][k] for k in range(j))
                if i==j:
                    if value<=0:
                        raise ValueError("Covariance is not positive definite")
                    lower[i][j]=sqrt(value)
                else:
                    lower[i][j]=value/lower[j][j]
        return result
