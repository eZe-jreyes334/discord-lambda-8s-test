"""Atomic v2 result path shared by solo queues, premade teams and brackets."""
from copy import deepcopy
import logging

from dao.RatingDao import RatingDao, RatingConflict
from dao.QueueDao import table_name as queue_table_name
from rating.model import SkillState

log=logging.getLogger(__name__)


def idle_team_writes(record,store,team_dao=None):
    from dao.TeamDao import TeamDao,table_name as team_table_name
    team_dao=team_dao or TeamDao()
    writes=[]
    for team_id in (record.team_1_id,record.team_2_id):
        team=team_dao.get_team(record.guild_id,team_id)
        if team is None:
            raise ValueError("A participating premade team is missing")
        writes.append({"Update":{"TableName":team_table_name,
            "Key":store._encode(dict(guild_id=record.guild_id,team_id=team_id)),
            "UpdateExpression":"SET #s = :idle, #v = :next",
            "ConditionExpression":"#v = :v AND #s = :playing",
            "ExpressionAttributeNames":{"#s":"status","#v":"version"},
            "ExpressionAttributeValues":store._encode({":idle":"idle",":playing":"in_match",
                ":v":int(team.version),":next":int(team.version)+1})}})
    return writes


def cancel_vote(inter,queue_id,queue_dao):
    """Cancel and release premade teams under the same queue version guard."""
    import core.QueueManager as qm
    from botocore.exceptions import ClientError
    from dao import set_default
    import json
    expected_id=inter.custom_id.split('#')[-1]
    for _ in range(4):
        record=queue_dao.get_queue(inter.guild_id,queue_id)
        if not record.match_id or record.match_id!=expected_id:
            return None
        participants=set(record.team_1+record.team_2+record.queue)
        if inter.user_id not in participants:
            return None
        if inter.user_id not in record.cancel_votes:
            record.cancel_votes.append(inter.user_id)
        for votes in (record.team1_votes,record.team2_votes):
            if inter.user_id in votes:
                votes.remove(inter.user_id)
        if queue_dao.put_queue(record) is None:
            continue
        votes=set(record.cancel_votes)&participants
        # A majority is required in both draft and ready phases.
        if len(votes)<=len(participants)//2 and not (record.is_team_queue and qm._both_captains_voted(record,list(votes))):
            return qm.update_queue_embed(record)
        after=deepcopy(record)
        after.clear_queue(reset_expiry=False)
        if not record.is_team_queue:
            after.queue=list(after.waitlist)[:8]
            after.waitlist=[]
        store=RatingDao()
        item=json.loads(json.dumps(after.__dict__,default=set_default))
        item['version']=record.version+1
        writes=[{'Put':{'TableName':queue_table_name,'Item':store._encode(item),
            'ConditionExpression':'#v = :v AND match_id = :m',
            'ExpressionAttributeNames':{'#v':'version'},
            'ExpressionAttributeValues':store._encode({':v':record.version,':m':record.match_id})}}]
        if record.is_team_queue:
            writes.extend(idle_team_writes(record,store))
        try:
            store.client.transact_write_items(TransactItems=writes)
        except ClientError as exc:
            if exc.response['Error']['Code']=='TransactionCanceledException':
                continue
            raise
        if record.is_team_queue:
            for channel,message in (record.channel_config or {}).items():
                inter.delete_message(channel_id=channel,message_id=message)
            return None
        return qm.update_queue_embed(after)
    return None


def commit_result(record,winning_side,store=None,team_dao=None,guard_writes=()):
    """No Discord calls before commit. False means an identical durable receipt exists."""
    if winning_side not in ("A","B"):
        raise ValueError("Unknown winning side")
    SkillState.validate_teams(record.team_1,record.team_2,4)
    if not record.match_id:
        raise ValueError("This match predates v2; cancel and start a new lobby")
    store=store or RatingDao()
    winner=record.team_1 if winning_side=="A" else record.team_2
    loser=record.team_2 if winning_side=="A" else record.team_1
    after=deepcopy(record)
    extra=[]
    namespace="solo"
    if record.is_team_queue:
        from dao.TeamDao import TeamDao, table_name as team_table_name
        team_dao=team_dao or TeamDao()
        namespace="team"
        winner=[record.team_1_id if winning_side=="A" else record.team_2_id]
        loser=[record.team_2_id if winning_side=="A" else record.team_1_id]
        if not all(winner+loser):
            raise ValueError("Premade team identity is missing")
        for team_id in winner+loser:
            team=team_dao.get_team(record.guild_id,team_id)
            if team is None:
                raise ValueError("A participating premade team is missing")
            expected_roster=record.team_1 if team_id==record.team_1_id else record.team_2
            if set(team.players)!=set(expected_roster):
                raise ValueError("Premade roster changed during the match")
            extra.append({"Update":{"TableName":team_table_name,
                "Key":store._encode(dict(guild_id=record.guild_id,team_id=team_id)),
                "UpdateExpression":"SET #s = :idle, #v = :next",
                "ConditionExpression":"#v = :v AND #s = :playing",
                "ExpressionAttributeNames":{"#s":"status","#v":"version"},
                "ExpressionAttributeValues":store._encode({":idle":"idle",":playing":"in_match",
                    ":v":int(team.version),":next":int(team.version)+1})}})
    if record.is_bracket:
        after.rating_result=winning_side
    else:
        after.clear_queue(reset_expiry=False)
        if not record.is_team_queue:
            after.queue=list(after.waitlist)[:8]
            after.waitlist=[]
    captains=(record.team_1[0],record.team_2[0]) if record.draft_mode=="captains" else ()
    committed=store.commit(record.guild_id,record.match_id,namespace,winner,loser,
        queue_before=record,queue_after=after,queue_table=queue_table_name,
        captains=captains,extra_writes=extra,guard_writes=guard_writes)
    return committed,after


def vote_result(inter,queue_id,side,queue_dao):
    """Reload on conflicts; never re-use a stale calculation or clear first."""
    import core.QueueManager as qm
    expected_id=inter.custom_id.split("#")[-1]
    for _ in range(4):
        record=queue_dao.get_queue(inter.guild_id,queue_id)
        if record.match_id != expected_id:
            return None  # stale button, including a button from a previous lobby
        if len(record.team_1)!=4 or len(record.team_2)!=4:
            return None
        if inter.user_id not in record.team_1+record.team_2:
            return None
        target=record.team1_votes if side=="A" else record.team2_votes
        other=record.team2_votes if side=="A" else record.team1_votes
        if inter.user_id not in target:
            target.append(inter.user_id)
        for votes in (other,record.cancel_votes):
            if inter.user_id in votes:
                votes.remove(inter.user_id)
        if queue_dao.put_queue(record) is None:
            continue
        accepted=set(target)&set(record.team_1+record.team_2)
        agreed=len(accepted)>=5 or (record.is_team_queue and qm._both_captains_voted(record,list(accepted)))
        if not agreed:
            return qm.update_queue_embed(record)
        try:
            committed,after=commit_result(record,side)
        except RatingConflict:
            continue
        if not committed:
            return None
        # Delivery can fail independently. Receipt remains pending for reconciliation.
        try:
            if record.is_team_queue:
                from core import TeamManager,TeamLeaderboardManager
                win_id=record.team_1_id if side=="A" else record.team_2_id
                lose_id=record.team_2_id if side=="A" else record.team_1_id
                embed=TeamManager.generate_team_match_done_embed(win_id,lose_id,record.guild_id)
            else:
                embed=qm.generate_match_done_embed(record.team_1,record.team_2,record.guild_id,record)
            inter.send_message(channel_id=record.result_channel_id,embeds=[embed])
            if record.is_team_queue:
                for channel,message in (record.channel_config or {}).items():
                    inter.delete_message(channel_id=channel,message_id=message)
                TeamLeaderboardManager.post_team_leaderboard(record.guild_id,inter)
            else:
                qm.LeaderboardManager.post_leaderboard(after,inter)
            RatingDao().mark_notified(record.guild_id,record.match_id)
        except Exception:
            log.exception("Result committed; notification pending for match %s",record.match_id)
        return None if record.is_team_queue else qm.update_queue_embed(after)
    inter.send_followup(content="The match changed while saving. Please press the result button again.",ephemeral=True)
    return None
