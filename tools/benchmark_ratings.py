"""Reproducible chronological, pre-update synthetic rating comparison.

Run: python -m tools.benchmark_ratings --seeds 8 --matches 400 --output docs/benchmark.json
Evaluation seeds start at 100; --start-seed 0 is the separate development set.
No AWS access. Every algorithm sees the SAME realized rosters and outcomes.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import json
import math
from pathlib import Path
import random
from statistics import mean, stdev
import time

from rating.model import Config, SkillState, cdf


class Independent:
    def __init__(self, ids, model="trueskill"):
        self.kind = model
        if model in ("trueskill", "legacy"):
            from trueskill import TrueSkill
            self.model = TrueSkill(draw_probability=0,tau=0.1 if model == "legacy" else 25/300)
            self.ratings = {p:self.model.create_rating() for p in ids}
            if model == 'legacy':
                self.ratings = {p:self.model.create_rating(25,8.33) for p in ids}
        else:
            from openskill.models import PlackettLuce, BradleyTerryFull
            self.model = (PlackettLuce if model == "openskill_pl" else BradleyTerryFull)()
            self.ratings = {p:self.model.rating() for p in ids}
        self.ids = ids
        self.counts = dict.fromkeys(ids,0)
        self.sr = dict.fromkeys(ids,0.0)
        self.forgiven_day = dict.fromkeys(ids,-1)
        self.last = dict.fromkeys(ids,0)
        self.ranks = dict.fromkeys(ids,0)

    def predict(self,a,b,timestamp):
        ratings = [self.ratings[p] for p in a+b]
        if self.kind.startswith("openskill"):
            return self.model.predict_win([[self.ratings[p] for p in a],[self.ratings[p] for p in b]])[0]
        d = sum(self.ratings[p].mu for p in a)-sum(self.ratings[p].mu for p in b)
        return cdf(d/math.sqrt(sum(r.sigma**2 for r in ratings)+len(ratings)*self.model.beta**2))

    def update(self,a,b,timestamp):
        old = self.ratings.copy()
        rated = self.model.rate([[old[p] for p in a],[old[p] for p in b]],ranks=[0,1])
        avg = mean(old[p].mu-2*old[p].sigma for p in a+b)
        for side,team in enumerate((a,b)):
            for i,p in enumerate(team):
                self.counts[p] += 1
                r = rated[side][i]
                if self.kind == "legacy":
                    count = self.counts[p]
                    k = 1.5 if count < 10 else .5 if old[p].mu > 2100 else .7 if old[p].mu > 1800 else 1.2 if count < 30 else 1 if count < 100 else .8
                    r = self.model.create_rating(old[p].mu+(r.mu-old[p].mu)*k,max(.5,r.sigma))
                    expectation = 1/(1+10**((avg-(old[p].mu-2*old[p].sigma))/20))
                    day = timestamp//86400
                    if self.last[p]:
                        self.sr[p] = max(0,self.sr[p]-max(0,(timestamp-self.last[p])/86400-7)*10)
                    if side == 0:
                        self.sr[p] += max(1,round(20*(1-expectation)))
                        self.ranks[p] = next((i for i in range(8) if i*100 <= self.sr[p] <= (i+1)*100-1),7)
                    elif count >= 10 and self.forgiven_day[p] != day:
                        self.forgiven_day[p] = day
                    else:
                        self.sr[p] = max(0,self.sr[p]-max(1,round(20*expectation)))
                        self.ranks[p] = next((i for i in range(8) if i*100 <= self.sr[p] <= (i+1)*100-1),7)
                    if count == 10:
                        hidden = (r.mu-2*r.sigma)*100
                        bands = [(-9999,399),(400,699),(700,1099),(1100,1399),(1400,1699),(1700,1899),(1900,2099),(2100,9000)]
                        proj = next((i for i,(low,high) in enumerate(bands) if low <= hidden <= high),7)
                        self.ranks[p] = min(3,max(self.ranks[p],proj))
                        self.sr[p] = self.ranks[p]*100+50
                    self.last[p] = timestamp
                self.ratings[p] = r

    def estimates(self):
        return [self.ratings[p].mu for p in self.ids],[self.ratings[p].sigma for p in self.ids]


class Joint:
    def __init__(self,ids,drift=.25):
        self.state = SkillState(replace(Config(),drift_per_day=drift))
        self.state.ensure(ids)

    def predict(self,a,b,timestamp):
        return self.state.predict(a,b,timestamp)

    def update(self,a,b,timestamp):
        self.state.update(a,b,timestamp)

    def estimates(self):
        return self.state.mu,[math.sqrt(self.state.cov[i][i]) for i in range(len(self.state.ids))]


def correlation(a,b):
    ma,mb = mean(a),mean(b)
    den = math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/den if den else 0


def ranking(values):
    # Midranks, so equal estimates are not silently broken by player ID.
    result = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        for i in ordered[start:end]:
            result[i] = (start + end - 1) / 2
        start = end
    return result


def generate(n,scenario,link,seed,matches):
    rng = random.Random(seed)
    ids = [str(i) for i in range(n)]
    truth = [rng.gauss(25,6) for _ in ids]
    offset = mean(truth)-25
    truth = [x-offset for x in truth]
    truth[0],truth[1] = 39,25  # rare strong vs regular average
    weights = [0.2,3]+[rng.uniform(.6,1.8) for _ in ids[2:]]
    records=[]
    for m in range(matches):
        current = list(truth)
        if scenario == "changing":
            current[2] += 12*min(1,max(0,(m-matches*.4)/(matches*.3)))
            current[3] -= 10*min(1,max(0,(m-matches*.4)/(matches*.3)))
        population = list(range(n))
        if scenario == "returning" and matches*.2 <= m < matches*.65:
            population.remove(0)
        # Weighted sampling without replacement, independent of the candidate model.
        selected=sorted(population,key=lambda p:rng.random()**(1/weights[p]),reverse=True)[:8]
        if scenario == "fixed_pods":
            selected=list(range(8)) if rng.random()<.9 else selected
        elif scenario == "carry":
            selected=[0,2,3,4]+rng.sample([i for i in population if i not in (0,2,3,4)],4)
        elif scenario == "lucky" and m < 10:
            selected=[5]+rng.sample([i for i in population if i!=5],7)
        if scenario in ("balanced","draft"):
            perceived={p:current[p]+rng.gauss(0,4) for p in selected}
            ordered=sorted(selected,key=lambda p:perceived[p],reverse=True)
            a=[ordered[i] for i in (0,3,4,7)]
            b=[p for p in selected if p not in a]
        elif scenario == "farming":
            selected.sort(key=lambda p:current[p],reverse=True)
            a,b=selected[:4],selected[4:]
        else:
            a,b=selected[:4],selected[4:]
        d=sum(current[p] for p in a)-sum(current[p] for p in b)
        scale=math.sqrt(8)*(25/6)
        probability=cdf(d/scale) if link == "probit" else 1/(1+math.exp(-d/(scale*math.sqrt(3)/math.pi)))
        if scenario == "draft":
            probability=.9*probability+.1*.5 # unmodelled session/strategy noise
        won=rng.random()<probability
        if scenario == "lucky" and m<10:
            won=5 in a
        timestamp=1700000000+m*10800
        if scenario == "returning" and m>=matches*.65:
            timestamp+=28*86400
        records.append(([str(p) for p in a],[str(p) for p in b],won,timestamp,current))
    return ids,records


def evaluate(ids,records,name):
    model=Joint(ids,float(name.split(":")[1])) if name.startswith("joint") else Independent(ids,name)
    predictions=[]
    counts=dict.fromkeys(ids,0)
    movement=[]
    previous=ranking([25]*len(ids))
    for a,b,won,timestamp,truth in records:
        p=min(1-1e-12,max(1e-12,model.predict(a,b,timestamp)))
        predictions.append((p,int(won)))
        model.update(a if won else b,b if won else a,timestamp)
        for pid in a+b:
            counts[pid]+=1
        mu,sigma=model.estimates()
        current=ranking(mu)
        movement.append(mean(abs(a-b) for a,b in zip(previous,current)))
        previous=current
    mu,sigma=model.estimates()
    bins=[]
    for i in range(10):
        batch=[(p,y) for p,y in predictions if i/10<=p<(i+1)/10]
        if batch:
            bins.append(dict(n=len(batch),pred=mean(p for p,y in batch),actual=mean(y for p,y in batch)))
    lambdas={}
    for weight in (0,.25,.5,1,2,3):
        scores=[m-weight*s for m,s in zip(mu,sigma)]
        if name == "legacy":
            scores=[max(0,model.sr[p]-max(0,(records[-1][3]-model.last[p])/86400-7)*10) if model.last[p] else model.sr[p] for p in ids]
        lambdas[str(weight)]=dict(spearman=correlation(ranking(scores),ranking(truth)),
            rare_above_grinder=int(scores[0]>scores[1]),
            lucky_player_top=int(scores[5]==max(scores)),
            residual_activity=correlation([s-t for s,t in zip(scores,truth)],[counts[p] for p in ids]))
    return dict(log_loss=mean(-y*math.log(p)-(1-y)*math.log(1-p) for p,y in predictions),
        brier=mean((p-y)**2 for p,y in predictions),accuracy=mean((p>=.5)==bool(y) for p,y in predictions),
        ece=sum(b['n']*abs(b['pred']-b['actual']) for b in bins)/len(predictions),
        coverage95=mean(abs(m-t)<=1.96*s for m,t,s in zip(mu,truth,sigma)),
        movement=mean(movement[len(movement)//2:]), lambdas=lambdas,
        rare_games=counts['0'],grinder_games=counts['1'],mean_sigma=mean(sigma),calibration=bins)


def evaluate_job(job):
    seed,n,link,scenario,name,matches=job
    ids,records=generate(n,scenario,link,seed,matches)
    return dict(seed=seed,n=n,link=link,scenario=scenario,model=name,**evaluate(ids,records,name))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds',type=int,default=8)
    parser.add_argument('--start-seed',type=int,default=100)
    parser.add_argument('--matches',type=int,default=400)
    parser.add_argument('--output',default='docs/benchmark.json')
    parser.add_argument('--models',nargs='+',default=None)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    models=['legacy','trueskill','openskill_pl','openskill_bt','joint:0.0','joint:0.05','joint:0.25','joint:1.0']
    if args.models:
        if any(model not in models for model in args.models):
            parser.error('Unknown model')
        models=args.models
    scenarios=['random','balanced','draft','fixed_pods','carry','farming','lucky','returning','changing']
    rows=[]
    start=time.perf_counter()
    if args.workers < 1:
        parser.error('--workers must be positive')
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    checkpoint=path.with_suffix('.checkpoint.jsonl')
    protocol=dict(seeds=args.seeds,start_seed=args.start_seed,matches=args.matches,models=models)
    if args.resume and checkpoint.exists():
        lines=checkpoint.read_text(encoding='utf-8').splitlines()
        if json.loads(lines[0]) != protocol:
            parser.error('Checkpoint protocol differs; choose a new output path')
        for line in lines[1:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                parser.error('Incomplete checkpoint row; review before resuming')
    else:
        checkpoint.write_text(json.dumps(protocol)+'\n',encoding='utf-8')
    key=lambda row:(row['seed'],row['n'],row['link'],row['scenario'],row['model'])
    completed={key(row) for row in rows}
    jobs=[(seed,n,link,scenario,name,args.matches)
          for seed in range(args.start_seed,args.start_seed+args.seeds)
          for n in (15,20,25) for link in ('probit','logistic')
          for scenario in scenarios for name in models
          if (seed,n,link,scenario,name) not in completed]
    with ProcessPoolExecutor(max_workers=args.workers) as pool, checkpoint.open('a',encoding='utf-8') as saved:
        for row in pool.map(evaluate_job,jobs,chunksize=1):
            rows.append(row)
            saved.write(json.dumps(row)+'\n')
            saved.flush()
            if len(rows)%54 == 0:
                print(f'Completed {len(rows)} model/scenario runs',flush=True)
    summary={}
    for name in models:
        selected=[r for r in rows if r['model']==name]
        summary[name]={k:mean(r[k] for r in selected) for k in ('log_loss','brier','accuracy','ece','coverage95','movement','mean_sigma')}
        seed_losses=[mean(r['log_loss'] for r in selected if r['seed']==s) for s in range(args.start_seed,args.start_seed+args.seeds)]
        summary[name]['log_loss_seed_se']=stdev(seed_losses)/math.sqrt(len(seed_losses)) if len(seed_losses)>1 else None
        summary[name]['ranking']={w:{k:mean(r['lambdas'][w][k] for r in selected) for k in selected[0]['lambdas'][w]} for w in selected[0]['lambdas']}
    output=dict(protocol=vars(args),models=models,scenarios=scenarios,seconds=time.perf_counter()-start,
                limitations='Synthetic schedules/outcomes; not measured CoD accuracy. No real history was available. Models see identical events; predictions precede updates. Report all scenarios including model mismatch.',summary=summary,rows=rows)
    path.write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
