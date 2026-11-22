"""Mycelium Consciousness, serverless edition: the business logic.

The same domain as the main project, re-hosted on AWS building blocks:

========================  =====================================
main project              this lab (LocalStack)
========================  =====================================
Redis stream + buffer     DynamoDB table ``frames``
``clone_profiles``        DynamoDB table ``clones``
``memory_backups``        DynamoDB table ``backups``
MinIO (S3 API)            S3 bucket (payload only)
FastAPI + uvicorn         Lambda behind API Gateway
========================  =====================================

Nothing here imports boto3: the stores are injected (see ``stores.py``), so the
logic is unit-tested against in-memory fakes without any AWS endpoint.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from shared import backup_codec
from shared.subscriptions import SubscriptionTier, policy_for

DESIGNATION_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MAX_CONTENT_CHARS = 4096


class ApiError(Exception):
    """A request problem that maps to an HTTP status (never a 500)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Db(Protocol):
    def create_clone(self, item: dict) -> bool: ...
    def get_clone(self, clone_id: str) -> dict | None: ...
    def delete_clone(self, clone_id: str) -> None: ...
    def add_frame(self, clone_id: str, frame_id: str, item: dict) -> None: ...
    def list_frames(self, clone_id: str) -> list[dict]: ...
    def count_frames(self, clone_id: str) -> int: ...
    def delete_frames(self, clone_id: str, frame_ids: list[str]) -> None: ...
    def put_backup(self, item: dict) -> None: ...
    def get_backup(self, clone_id: str, backup_id: str) -> dict | None: ...
    def list_backups(self, clone_id: str) -> list[dict]: ...
    def delete_backup(self, clone_id: str, backup_id: str) -> None: ...


class Blobs(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


def _sortable_id(now: float) -> str:
    """Time-ordered, unique: DynamoDB sorts the range key lexicographically."""
    return f"{int(now * 1000):013d}-{uuid.uuid4().hex[:8]}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


class Service:
    def __init__(
        self, db: Db, blobs: Blobs, clock: Callable[[], float] = time.time
    ) -> None:
        self.db = db
        self.blobs = blobs
        self.clock = clock

    # ---- clones ---------------------------------------------------------

    def create_clone(self, body: dict) -> dict:
        designation = body.get("designation")
        if not isinstance(designation, str) or not DESIGNATION_RE.match(designation):
            raise ApiError(
                422, "designation must match ^[a-z0-9][a-z0-9_-]{0,62}$ (lowercase)"
            )
        tier = body.get("tier", SubscriptionTier.FREE.value)
        try:
            SubscriptionTier(tier)
        except ValueError:
            raise ApiError(
                422, f"tier must be one of {[t.value for t in SubscriptionTier]}"
            ) from None
        item = {
            "clone_id": designation,
            "designation": designation,
            "tier": tier,
            "status": "active",
            "created_at": _iso(self.clock()),
        }
        if not self.db.create_clone(item):
            raise ApiError(409, f"clone {designation!r} already exists")
        return item

    def get_clone(self, clone_id: str) -> dict:
        clone = self.db.get_clone(clone_id)
        if clone is None:
            raise ApiError(404, "clone not found")
        return clone

    def delete_clone(self, clone_id: str) -> None:
        """Remove the clone with everything it owns (frames, backups, objects)."""
        self.get_clone(clone_id)
        for backup in self.db.list_backups(clone_id):
            self.blobs.delete(backup["storage_key"])
            self.db.delete_backup(clone_id, backup["backup_id"])
        frames = self.db.list_frames(clone_id)
        self.db.delete_frames(clone_id, [f["frame_id"] for f in frames])
        self.db.delete_clone(clone_id)

    # ---- memory frames (the write path) -----------------------------------

    def write_memory(self, clone_id: str, body: dict) -> dict:
        self.get_clone(clone_id)
        content = body.get("content")
        if not isinstance(content, str) or not content:
            raise ApiError(422, "content must be a non-empty string")
        if len(content) > MAX_CONTENT_CHARS:
            raise ApiError(422, f"content is limited to {MAX_CONTENT_CHARS} chars")
        now = self.clock()
        frame_id = _sortable_id(now)
        self.db.add_frame(
            clone_id,
            frame_id,
            {"content": content, "captured_at": body.get("captured_at") or _iso(now)},
        )
        return {"frame_id": frame_id}

    def stream_status(self, clone_id: str) -> dict:
        self.get_clone(clone_id)
        return {"buffered_frames": self.db.count_frames(clone_id)}

    # ---- backups (rollup, list, get, delete) ----------------------------------

    def rollup(self, clone_id: str) -> dict:
        """Fold the buffered frames into one backup object, like ``rollup_clone``.

        Order matters: the object is uploaded first, the metadata row second and
        the frames are removed last, so a failure in between can only leave
        extra frames for the next rollup, never lose any.
        """
        clone = self.get_clone(clone_id)
        frames = self.db.list_frames(clone_id)
        if not frames:
            raise ApiError(409, "nothing to back up: no buffered frames")

        entries = [
            {"content": f["content"], "captured_at": f["captured_at"]} for f in frames
        ]
        now = self.clock()
        backup_id = _sortable_id(now)
        storage_key = f"backups/{clone_id}/{backup_id}.bin"
        self.blobs.put(storage_key, backup_codec.encode_payload(entries))

        item = {
            "clone_id": clone_id,
            "backup_id": backup_id,
            "storage_key": storage_key,
            "entry_count": len(entries),
            "period_start": entries[0]["captured_at"],
            "period_end": entries[-1]["captured_at"],
            "tier": clone["tier"],
            "created_at": _iso(now),
        }
        self.db.put_backup(item)
        self.db.delete_frames(clone_id, [f["frame_id"] for f in frames])
        pruned = self._prune(clone_id, SubscriptionTier(clone["tier"]))
        return {**item, "pruned": pruned}

    def _prune(self, clone_id: str, tier: SubscriptionTier) -> int:
        """Keep only ``retain_count`` newest backups of the tier (lab 1 policy)."""
        keep = policy_for(tier).retain_count
        stale = self.db.list_backups(clone_id)[keep:]  # newest first
        for backup in stale:
            self.blobs.delete(backup["storage_key"])
            self.db.delete_backup(clone_id, backup["backup_id"])
        return len(stale)

    def list_backups(self, clone_id: str) -> list[dict]:
        self.get_clone(clone_id)
        return self.db.list_backups(clone_id)

    def get_backup(self, clone_id: str, backup_id: str) -> dict:
        """Metadata plus the decoded payload (the restore view)."""
        backup = self.db.get_backup(clone_id, backup_id)
        if backup is None:
            raise ApiError(404, "backup not found")
        payload = backup_codec.decode_payload(self.blobs.get(backup["storage_key"]))
        return {**backup, "payload": payload}

    def delete_backup(self, clone_id: str, backup_id: str) -> None:
        backup = self.db.get_backup(clone_id, backup_id)
        if backup is None:
            raise ApiError(404, "backup not found")
        self.blobs.delete(backup["storage_key"])
        self.db.delete_backup(clone_id, backup_id)


Route = Callable[[Service, dict, dict], tuple[int, Any]]
