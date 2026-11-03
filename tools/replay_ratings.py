"""Validate and replay authorized JSONL results OFFLINE. Never calls AWS.

Each line: {"match_id":"...","timestamp":1700000000,"winners":[four IDs],
            "losers":[four IDs],"namespace":"solo"}
For premade ratings use namespace=team and one team ID on each side.
Produce a new-season snapshot and permanent receipt items for operator review.
Do not install a snapshot without ALL its receipts; see docs/RATING_V2.md.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from rating.model import SkillState


def replay(events,guild_id,season,namespace='solo'):
    if not guild_id or not season or '#' in season:
        raise ValueError('Explicit guild and valid new season required')
    state=SkillState()
    seen={}
    receipts=[]
    scores=[]
    for index,event in enumerate(events,1):
        if event.get('namespace',namespace)!=namespace:
            raise ValueError(f'Line {index}: mixed rating namespaces')
        match_id=event['match_id']
        timestamp=event['timestamp']
        if not isinstance(match_id,str) or not match_id or len(match_id)>128:
            raise ValueError(f'Line {index}: invalid match_id')
        if not isinstance(timestamp,int) or timestamp<=0:
            raise ValueError(f'Line {index}: timestamp must be positive UTC Unix seconds')
        a,b=event['winners'],event['losers']
        SkillState.validate_teams(a,b,4 if namespace=='solo' else 1)
        canonical=json.dumps([namespace,sorted(a),sorted(b)],separators=(',',':'))
        fingerprint=hashlib.sha256(canonical.encode()).hexdigest()
        if match_id in seen:
            if seen[match_id]!=(fingerprint,timestamp):
                raise ValueError(f'Line {index}: conflicting duplicate match_id')
            continue
        seen[match_id]=(fingerprint,timestamp)
        if timestamp<state.timestamp:
            raise ValueError(f'Line {index}: history is out of chronological order')
        probability=state.predict(a,b,timestamp)
        scores.append(probability)
        state.update(a,b,timestamp,captains=event.get('captains',()))
        receipts.append(dict(guild_id=guild_id,record_id='MATCH#'+match_id,match_id=match_id,
            schema_version=2,season=season,namespace=namespace,fingerprint=fingerprint,
            winners=a,losers=b,timestamp=timestamp,queue_id=event.get('queue_id','import'),
            state_version=len(receipts)+1,predicted_winner_probability=str(probability),
            notification_status='historical-import'))
    if not receipts:
        raise ValueError('No results provided; SR-only records cannot reconstruct skill')
    snapshot=dict(guild_id=guild_id,record_id=f'STATE#{season}#{namespace}',version=len(receipts),
                  payload=json.dumps(state.to_dict(),separators=(',',':'),allow_nan=False))
    return dict(schema_version=2,snapshot=snapshot,receipts=receipts,
        metrics=dict(matches=len(receipts),prequential_log_loss=sum(-math.log(max(p,1e-12)) for p in scores)/len(scores),
                     brier=sum((1-p)**2 for p in scores)/len(scores)),
        warning='Offline review bundle, not an applied migration. Import into an empty reviewed season while the bot is paused. Preserve every receipt.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--guild',required=True)
    p.add_argument('--season',required=True)
    p.add_argument('--namespace',choices=('solo','team'),default='solo')
    args=p.parse_args()
    events=[json.loads(line) for line in Path(args.input).read_text(encoding='utf-8').splitlines() if line.strip()]
    result=replay(events,args.guild,args.season,args.namespace)
    # Never overwrite an existing reviewed bundle by accident.
    with Path(args.output).open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(result['metrics']))


if __name__=='__main__':
    main()
