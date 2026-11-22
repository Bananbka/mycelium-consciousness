"""boto3-backed implementations of ``service.Db`` and ``service.Blobs``.

The endpoint is resolved so that the *same* code runs against LocalStack and
against real AWS: no endpoint override means real AWS.
"""

from __future__ import annotations

import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

CLONES_TABLE = os.getenv("CLONES_TABLE", "mycelium-clones")
FRAMES_TABLE = os.getenv("FRAMES_TABLE", "mycelium-frames")
BACKUPS_TABLE = os.getenv("BACKUPS_TABLE", "mycelium-backups")
BUCKET = os.getenv("BACKUP_BUCKET", "mycelium-backups")


def endpoint_url() -> str | None:
    """Explicit override > LocalStack's injected hostname > real AWS (None)."""
    explicit = os.getenv("AWS_ENDPOINT_URL") or os.getenv("LOCALSTACK_ENDPOINT")
    if explicit:
        return explicit
    host = os.getenv("LOCALSTACK_HOSTNAME")
    if host:
        return f"http://{host}:{os.getenv('EDGE_PORT', '4566')}"
    return None


def _kwargs() -> dict:
    url = endpoint_url()
    return {"endpoint_url": url} if url else {}


class DynamoDb:
    def __init__(self) -> None:
        dynamodb = boto3.resource("dynamodb", **_kwargs())
        self.clones = dynamodb.Table(CLONES_TABLE)
        self.frames = dynamodb.Table(FRAMES_TABLE)
        self.backups = dynamodb.Table(BACKUPS_TABLE)

    def ping(self) -> None:
        self.clones.load()  # DescribeTable: cheap, proves the endpoint answers

    # clones -------------------------------------------------------------
    def create_clone(self, item: dict) -> bool:
        try:
            self.clones.put_item(
                Item=item, ConditionExpression="attribute_not_exists(clone_id)"
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def get_clone(self, clone_id: str) -> dict | None:
        return self.clones.get_item(Key={"clone_id": clone_id}).get("Item")

    def delete_clone(self, clone_id: str) -> None:
        self.clones.delete_item(Key={"clone_id": clone_id})

    # frames -------------------------------------------------------------
    def add_frame(self, clone_id: str, frame_id: str, item: dict) -> None:
        self.frames.put_item(Item={"clone_id": clone_id, "frame_id": frame_id, **item})

    def _query_all(self, table, clone_id: str, **extra) -> list[dict]:
        items: list[dict] = []
        kwargs = {"KeyConditionExpression": Key("clone_id").eq(clone_id), **extra}
        while True:
            page = table.query(**kwargs)
            items.extend(page["Items"])
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def list_frames(self, clone_id: str) -> list[dict]:
        return self._query_all(self.frames, clone_id)  # range key = time order

    def count_frames(self, clone_id: str) -> int:
        total = 0
        kwargs = {
            "KeyConditionExpression": Key("clone_id").eq(clone_id),
            "Select": "COUNT",
        }
        while True:
            page = self.frames.query(**kwargs)
            total += page["Count"]
            if "LastEvaluatedKey" not in page:
                return total
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def delete_frames(self, clone_id: str, frame_ids: list[str]) -> None:
        with self.frames.batch_writer() as batch:
            for frame_id in frame_ids:
                batch.delete_item(Key={"clone_id": clone_id, "frame_id": frame_id})

    # backups ------------------------------------------------------------
    def put_backup(self, item: dict) -> None:
        self.backups.put_item(Item=item)

    def get_backup(self, clone_id: str, backup_id: str) -> dict | None:
        key = {"clone_id": clone_id, "backup_id": backup_id}
        return self.backups.get_item(Key=key).get("Item")

    def list_backups(self, clone_id: str) -> list[dict]:
        return self._query_all(self.backups, clone_id, ScanIndexForward=False)

    def delete_backup(self, clone_id: str, backup_id: str) -> None:
        self.backups.delete_item(Key={"clone_id": clone_id, "backup_id": backup_id})


class S3Blobs:
    def __init__(self) -> None:
        # path-style: LocalStack has no wildcard DNS for <bucket>.<host>
        self.client = boto3.client(
            "s3", config=Config(s3={"addressing_style": "path"}), **_kwargs()
        )

    def ping(self) -> None:
        self.client.head_bucket(Bucket=BUCKET)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=BUCKET, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=BUCKET, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=BUCKET, Key=key)
