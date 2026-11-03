from copy import deepcopy

import pytest

from rating import enabled
from tools.validate_rating_storage import validate_table_description


def test_skill_system_is_active_without_opt_in(monkeypatch):
    monkeypatch.delenv('RATING_SYSTEM',raising=False)
    assert enabled()


@pytest.mark.parametrize('value',['legacy','unknown'])
def test_old_or_unknown_mode_cannot_restore_accumulated_sr(monkeypatch,value):
    monkeypatch.setenv('RATING_SYSTEM',value)
    with pytest.raises(ValueError,match='Legacy SR has been replaced'):
        enabled()


def test_existing_v2_configuration_still_uses_skill_system(monkeypatch):
    monkeypatch.setenv('RATING_SYSTEM','v2')
    assert enabled()


def test_missing_storage_fails_instead_of_falling_back(monkeypatch):
    from dao.RatingDao import RatingDao
    monkeypatch.delenv('RATING_TABLE',raising=False)
    with pytest.raises(ValueError,match='RATING_TABLE is required'):
        RatingDao()


VALID_TABLE={'Table':{'TableStatus':'ACTIVE',
    'KeySchema':[{'AttributeName':'guild_id','KeyType':'HASH'},
                 {'AttributeName':'record_id','KeyType':'RANGE'}],
    'AttributeDefinitions':[{'AttributeName':'guild_id','AttributeType':'S'},
                            {'AttributeName':'record_id','AttributeType':'S'}]}}


def test_deployment_accepts_expected_rating_table():
    validate_table_description(VALID_TABLE)


@pytest.mark.parametrize('problem',['missing','creating','wrong_key','numeric_key'])
def test_deployment_rejects_unusable_rating_table(problem):
    description=deepcopy(VALID_TABLE)
    if problem=='missing':
        description={}
    elif problem=='creating':
        description['Table']['TableStatus']='CREATING'
    elif problem=='wrong_key':
        description['Table']['KeySchema'][1]['AttributeName']='queue_id'
    else:
        description['Table']['AttributeDefinitions'][1]['AttributeType']='N'
    with pytest.raises(ValueError):
        validate_table_description(description)
