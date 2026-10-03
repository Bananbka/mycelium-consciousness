from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from shared.settings import settings

MINIO_ENDPOINT = settings.minio_endpoint
MINIO_ACCESS_KEY = settings.minio_access_key.get_secret_value()
MINIO_SECRET_KEY = settings.minio_secret_key.get_secret_value()
MINIO_BUCKET = settings.minio_bucket

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )
    return _client


_BUCKET_ALREADY_EXISTS = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}


def _ensure_bucket_sync() -> None:
    client = _get_client()
    try:
        client.head_bucket(Bucket=MINIO_BUCKET)
    except ClientError:
        try:
            client.create_bucket(Bucket=MINIO_BUCKET)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in _BUCKET_ALREADY_EXISTS:
                raise


def _put_sync(key: str, data: bytes) -> None:
    _get_client().put_object(Bucket=MINIO_BUCKET, Key=key, Body=data)


def _get_sync(key: str) -> bytes:
    response = _get_client().get_object(Bucket=MINIO_BUCKET, Key=key)
    return response["Body"].read()


def _delete_sync(key: str) -> None:
    _get_client().delete_object(Bucket=MINIO_BUCKET, Key=key)


def _list_keys_sync(prefix: str = "") -> list[str]:
    client = _get_client()
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


async def ensure_bucket() -> None:
    await asyncio.to_thread(_ensure_bucket_sync)


async def put_object(key: str, data: bytes) -> None:
    await asyncio.to_thread(_put_sync, key, data)


async def get_object(key: str) -> bytes:
    return await asyncio.to_thread(_get_sync, key)


async def delete_object(key: str) -> None:
    await asyncio.to_thread(_delete_sync, key)


async def list_keys(prefix: str = "") -> list[str]:
    return await asyncio.to_thread(_list_keys_sync, prefix)


def backup_key(clone_id: int, period_start: datetime, period_end: datetime) -> str:
    stamp = f"{period_start:%Y%m%dT%H%M%S}_{period_end:%Y%m%dT%H%M%S}"
    return f"clone-{clone_id}/{stamp}-{uuid.uuid4().hex[:8]}.mp.gz"
