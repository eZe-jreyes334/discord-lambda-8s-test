"""Versioned snapshots and permanent result receipts, committed atomically.

The dedicated rating table is required. Missing configuration never falls back
to legacy SR. No infrastructure or migration is created at import/startup.
Legacy player/team fields remain archival data for a previous-release rollback.
"""
import hashlib
import json
import os
import time

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from rating.model import SkillState


class RatingConflict(RuntimeError):
    pass


class ResultMismatch(ValueError):
    pass


class RatingDao:
    def __init__(self, client=None, table_name=None, season=None):
        self.table_name = table_name or os.environ.get("RATING_TABLE")
        self.season = season or os.environ.get("RATING_SEASON", "v2-1")
        if not self.table_name:
            raise ValueError("RATING_TABLE is required for skill ratings; configure the table before deployment. Legacy SR fallback is disabled.")
        if not self.season or len(self.season)>64 or "#" in self.season:
            raise ValueError("RATING_SEASON must be 1-64 characters without #")
        # Match the existing queue/team DAOs: all transaction tables share a region.
        self.client = client or boto3.client("dynamodb",region_name="us-east-1")
        self.serializer = TypeSerializer()

    def _encode(self, item):
        return {k:self.serializer.serialize(v) for k,v in item.items()}

    def _get(self,guild_id,record_id):
        from boto3.dynamodb.types import TypeDeserializer
        response=self.client.get_item(TableName=self.table_name,
            Key=self._encode(dict(guild_id=guild_id,record_id=record_id)),ConsistentRead=True)
        return {k:TypeDeserializer().deserialize(v) for k,v in response.get("Item",{}).items()}

    def snapshot_key(self, namespace):
        if namespace not in ("solo","team"):
            raise ValueError("Unknown rating namespace")
        return f"STATE#{self.season}#{namespace}"

    def load(self,guild_id,namespace="solo"):
        item=self._get(guild_id,self.snapshot_key(namespace))
        if not item:
            return SkillState(),0
        return SkillState.from_dict(json.loads(item["payload"])),int(item["version"])

    @staticmethod
    def fingerprint(namespace,winners,losers):
        data=json.dumps([namespace,sorted(winners),sorted(losers)],separators=(",",":"))
        return hashlib.sha256(data.encode()).hexdigest()

    def receipt(self,guild_id,match_id):
        # Deliberately independent of season: changing season cannot re-rate an old match.
        return self._get(guild_id,"MATCH#"+match_id)

    def commit(self,guild_id,match_id,namespace,winners,losers,*,queue_before,queue_after,
               queue_table,timestamp=None,captains=(),extra_writes=(),guard_writes=()):
        SkillState.validate_teams(winners,losers,4 if namespace=="solo" else 1)
        if not match_id or not isinstance(match_id,str) or len(match_id)>128:
            raise ValueError("A stable match ID is required")
        fingerprint=self.fingerprint(namespace,winners,losers)
        existing=self.receipt(guild_id,match_id)
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise ResultMismatch("This match already has a different recorded result")
            return False
        if queue_before.guild_id != guild_id or queue_after.guild_id != guild_id:
            raise ValueError("Cross-guild result rejected")
        state,version=self.load(guild_id,namespace)
        timestamp=int(time.time()) if timestamp is None else int(timestamp)
        # Live requests may race by seconds. The state version defines serialization.
        timestamp=max(timestamp,state.timestamp)
        probability=state.update(winners,losers,timestamp,captains=captains)
        snapshot=dict(guild_id=guild_id,record_id=self.snapshot_key(namespace),version=version+1,
                      payload=json.dumps(state.to_dict(),separators=(",",":"),allow_nan=False))
        receipt=dict(guild_id=guild_id,record_id="MATCH#"+match_id,match_id=match_id,
                     schema_version=2,season=self.season,namespace=namespace,
                     model="joint-probit-adf-v1",maps=list(queue_before.maps),
                     draft_mode=queue_before.draft_mode or "unknown",captains=list(captains),
                     rosters={"A":list(queue_before.team_1),"B":list(queue_before.team_2)},
                     fingerprint=fingerprint,winners=winners,losers=losers,
                     timestamp=timestamp,queue_id=queue_before.queue_id,state_version=version+1,
                     predicted_winner_probability=str(probability),
                     notification_status="pending")
        from dao import set_default
        after=json.loads(json.dumps(queue_after.__dict__,default=set_default))
        after["version"]=int(queue_before.version)+1
        # Queue objects contain only integers/strings/lists; rating floats are in JSON payload.
        writes=[{"Put":{"TableName":self.table_name,"Item":self._encode(receipt),
                         "ConditionExpression":"attribute_not_exists(record_id)"}},
                {"Put":{"TableName":self.table_name,"Item":self._encode(snapshot),
                         "ConditionExpression":"#v = :v" if version else "attribute_not_exists(record_id)",
                         **({"ExpressionAttributeNames":{"#v":"version"},"ExpressionAttributeValues":{":v":{"N":str(version)}}} if version else {})}},
                {"Put":{"TableName":queue_table,"Item":self._encode(after),
                         "ConditionExpression":"#v = :v AND match_id = :m",
                         "ExpressionAttributeNames":{"#v":"version"},
                         "ExpressionAttributeValues":{":v":{"N":str(queue_before.version)},":m":{"S":match_id}}}}]
        writes.extend(extra_writes)
        writes.extend(guard_writes)
        try:
            self.client.transact_write_items(TransactItems=writes)
        except ClientError as exc:
            code=exc.response["Error"]["Code"]
            if code in ("TransactionCanceledException","TransactionConflictException"):
                existing=self.receipt(guild_id,match_id)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise ResultMismatch("Conflicting match result") from exc
                    return False
                reasons=exc.response.get("CancellationReasons",[])
                if any(r.get("Code") not in (None,"None","ConditionalCheckFailed","TransactionConflict") for r in reasons):
                    raise
                raise RatingConflict("Queue or ratings changed; reload and retry the result") from exc
            raise
        queue_after.version=after["version"]
        return True

    def mark_notified(self,guild_id,match_id):
        self.client.update_item(TableName=self.table_name,
            Key=self._encode(dict(guild_id=guild_id,record_id="MATCH#"+match_id)),
            UpdateExpression="SET notification_status = :s",
            ConditionExpression="attribute_exists(record_id)",
            ExpressionAttributeValues={":s":{"S":"sent"}})
