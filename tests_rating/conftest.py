"""Use real boto3/TrueSkill and an in-memory AWS emulator, never live AWS."""
import os

for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'):
    os.environ[key]='testing'
os.environ['AWS_EC2_METADATA_DISABLED']='true'
os.environ['AWS_DEFAULT_REGION']='us-east-1'
os.environ['RATING_SYSTEM']='v2'
os.environ['RATING_TABLE']='RatingTest'
os.environ['BOT_ENV']='TEST'

import boto3
from moto import mock_aws
import pytest


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setenv('RATING_SYSTEM','v2')
    monkeypatch.setenv('RATING_TABLE','RatingTest')
    with mock_aws():
        client=boto3.client('dynamodb',region_name='us-east-1')
        for table,key in [('RatingTest','record_id'),('QueueTable','queue_id'),('TeamTable','team_id'),('PlayerTable','player_id')]:
            options={}
            if table in ('TeamTable','PlayerTable'):
                options['GlobalSecondaryIndexes']=[dict(IndexName='guild_id-index',KeySchema=[dict(AttributeName='guild_id',KeyType='HASH')],Projection={'ProjectionType':'ALL'})]
            client.create_table(TableName=table,
                KeySchema=[dict(AttributeName='guild_id',KeyType='HASH'),dict(AttributeName=key,KeyType='RANGE')],
                AttributeDefinitions=[dict(AttributeName='guild_id',AttributeType='S'),dict(AttributeName=key,AttributeType='S')],
                BillingMode='PAY_PER_REQUEST',**options)
        from dao.RatingDao import RatingDao
        from dao.QueueDao import QueueDao
        yield client,RatingDao(client=client),QueueDao()


@pytest.fixture
def match(database):
    from dao.QueueDao import QueueRecord
    record=QueueRecord(guild_id='g',money_queue=False,queue_id='q',
        team_1=['0','1','2','3'],team_2=['4','5','6','7'],queue=[],
        cancel_votes=[],team1_votes=[],team2_votes=[],maps=['map'],map_set=['map'],
        version=0,expiry=0,result_channel_id='results',team_1_channel_id='a',team_2_channel_id='b',
        channel_config={},waitlist=['next'])
    database[2].put_queue(record)
    return record
