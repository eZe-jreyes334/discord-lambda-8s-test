"""Validate an AWS describe-table response before any Lambda deployment changes.

No AWS connection or mutation: the workflow supplies the read-only CLI response.
"""
import argparse
import json
from pathlib import Path


def validate_table_description(description):
    table = description.get("Table", {})
    if table.get("TableStatus") != "ACTIVE":
        raise ValueError("Rating table must exist and be ACTIVE before deployment")
    keys = {key["AttributeName"]: key["KeyType"] for key in table.get("KeySchema", [])}
    if keys != {"guild_id": "HASH", "record_id": "RANGE"}:
        raise ValueError("Rating table needs guild_id HASH and record_id RANGE keys")
    types = {item["AttributeName"]: item["AttributeType"] for item in table.get("AttributeDefinitions", [])}
    if any(types.get(key) != "S" for key in keys):
        raise ValueError("Both rating table keys must have String (S) type")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("description", help="AWS dynamodb describe-table JSON file")
    args = parser.parse_args()
    try:
        validate_table_description(json.loads(Path(args.description).read_text(encoding="utf-8")))
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    print("Rating table is ready: ACTIVE, guild_id (S), record_id (S).")


if __name__ == "__main__":
    main()
