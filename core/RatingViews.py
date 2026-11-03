"""One authoritative snapshot per leaderboard; round only for display."""
import time

from dao.RatingDao import RatingDao


def leaderboard_entries(guild_id,records,namespace="solo"):
    state,_=RatingDao().load(guild_id,namespace)
    attr="player_id" if namespace=="solo" else "team_id"
    names={getattr(p,attr):p for p in records}
    views=[(pid,state.summary(pid,int(time.time()))) for pid in state.ids]
    views.sort(key=lambda p:(not p[1]["eligible"],-p[1]["score"],p[0]))
    entries=[]
    rank=0
    previous_score=None
    for index,(pid,s) in enumerate(views,1):
        if not s['games']:
            continue
        record=names.get(pid)
        if record:
            record.rating_v2=s
        if s['eligible'] and (previous_score is None or abs(s['score']-previous_score)>1e-8):
            rank=index
        previous_score=s['score'] if s['eligible'] else None
        status=s['status']+(' • Inactive' if s['inactive'] else '')
        if s['eligible']:
            # Do not manufacture certainty between close rivals.
            others=[p for p,v in views if v['eligible'] and p!=pid]
            close=any(.2 < state.pair_probability(pid,p) < .8 for p in others)
            if close:
                status+=' • Order uncertain'
        entries.append(dict(medal=f'`#{rank}`' if s['eligible'] else '`—`',
            rank_emoji=record.get_rank_emoji() if record else '',streak_emoji=record.get_streak() if record and namespace=='solo' else '',
            name=(record.player_name if namespace=='solo' else record.team_name) if record else f'Player {pid}',
            sr=s['sr'],delta=f'{s["delta"]:+d}',wins=s['wins'],losses=s['losses'],total=s['games'],
            championships=int(getattr(record,'championships',0)),status=status,mu=s['mu'],sigma=s['sigma']))
    return entries
