"""Create the S3 bucket and DynamoDB tables in LocalStack (idempotent).

Runs once as the ``provision`` compose service; the app containers start after it
finishes. Same schema as ``aws/scripts/deploy.ps1``.
"""

from __future__ import annotations

import os
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localstack:4566")
BUCKET = os.getenv("BACKUP_BUCKET", "mycelium-backups")
TABLES = {
    os.getenv("CLONES_TABLE", "mycelium-clones"): None,
    os.getenv("FRAMES_TABLE", "mycelium-frames"): "frame_id",
    os.getenv("BACKUPS_TABLE", "mycelium-backups"): "backup_id",
}


def wait_for_localstack() -> None:
    dynamodb = boto3.client("dynamodb", endpoint_url=ENDPOINT)
    for _ in range(60):
        try:
            dynamodb.list_tables()
            return
        except (EndpointConnectionError, ClientError, OSError):
            time.sleep(2)
    sys.exit(f"LocalStack at {ENDPOINT} did not become ready")


def create_table(client, name: str, range_key: str | None) -> None:
    attrs = [{"AttributeName": "clone_id", "AttributeType": "S"}]
    keys = [{"AttributeName": "clone_id", "KeyType": "HASH"}]
    if range_key:
        attrs.append({"AttributeName": range_key, "AttributeType": "S"})
        keys.append({"AttributeName": range_key, "KeyType": "RANGE"})
    try:
        client.create_table(
            TableName=name,
            AttributeDefinitions=attrs,
            KeySchema=keys,
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"created table {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"table {name} already exists")


def main() -> None:
    wait_for_localstack()
    dynamodb = boto3.client("dynamodb", endpoint_url=ENDPOINT)
    for name, range_key in TABLES.items():
        create_table(dynamodb, name, range_key)
    s3 = boto3.client(
        "s3", endpoint_url=ENDPOINT, config=Config(s3={"addressing_style": "path"})
    )
    try:
        s3.create_bucket(Bucket=BUCKET)
        print(f"created bucket {BUCKET}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise
        print(f"bucket {BUCKET} already exists")


if __name__ == "__main__":
    main()
