"""Explicit unequal-participation and lucky-entry stress tests; no AWS."""
import json
import math
from pathlib import Path
import random
from statistics import mean

from rating.model import SkillState,cdf


def run(seeds=100):
    rows=[]
    for n in (15,20,25):
        for seed in range(1000,1000+seeds):
            rng=random.Random(seed)
            state=SkillState()
            ids=list(map(str,range(n)))
            skill={p:rng.gauss(25,5) for p in ids}
            skill['0'],skill['1']=39,25
            rare_matches=set(rng.sample(range(200),25))
            for m in range(200):
                roster=['1']+(['0'] if m in rare_matches else [])
                roster+=rng.sample(ids[2:],8-len(roster))
                rng.shuffle(roster)
                a,b=roster[:4],roster[4:]
                p=cdf((sum(skill[x] for x in a)-sum(skill[x] for x in b))/(math.sqrt(8)*25/6))
                won=rng.random()<p
                state.update(a if won else b,b if won else a,1700000000+m*3600)
            rare,grinder=state.summary('0'),state.summary('1')
            # After an established pool, an average newcomer gets 10 forced wins.
            lucky=SkillState.from_dict(json.loads(json.dumps(state.to_dict())))
            for m in range(10):
                roster=rng.sample(ids,7)
                lucky.update(['new']+roster[:3],roster[3:],1700000000+(200+m)*3600)
            newcomer=lucky.summary('new')
            rows.append(dict(n=n,seed=seed,rare_games=rare['games'],grinder_games=grinder['games'],
                rare_above_grinder=rare['sr']>grinder['sr'],rare_eligible=rare['eligible'],
                rare_mu=rare['mu'],rare_sigma=rare['sigma'],grinder_mu=grinder['mu'],
                lucky_eligible=newcomer['eligible'],lucky_mu=newcomer['mu'],lucky_sigma=newcomer['sigma']))
    summary={str(n):{k:mean(float(r[k]) for r in rows if r['n']==n)
        for k in ('rare_above_grinder','rare_eligible','rare_mu','rare_sigma','grinder_mu','lucky_eligible','lucky_mu','lucky_sigma')}
        for n in (15,20,25)}
    return dict(seeds=seeds,summary=summary,rows=rows,
        limitations='Synthetic 39-vs-25 latent skill; outcome-only inference cannot guarantee correct ordering in every run. Lucky wins are deliberately forced, not sampled from the model.')


if __name__=='__main__':
    result=run()
    Path('docs/stress-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['summary'],indent=2))
