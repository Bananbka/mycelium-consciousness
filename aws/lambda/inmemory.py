"""In-memory stand-ins for DynamoDB and S3.

Used by the unit tests and by ``scripts/local_server.py`` (run the API without
LocalStack). Not packaged into the Lambda zip.
"""

from __future__ import annotations


class FakeDb:
    def __init__(self) -> None:
        self.clones: dict[str, dict] = {}
        self.frames: dict[str, dict[str, dict]] = {}
        self.backups: dict[str, dict[str, dict]] = {}

    def ping(self) -> None:
        pass

    def create_clone(self, item: dict) -> bool:
        if item["clone_id"] in self.clones:
            return False
        self.clones[item["clone_id"]] = dict(item)
        return True

    def get_clone(self, clone_id: str):
        return self.clones.get(clone_id)

    def delete_clone(self, clone_id: str) -> None:
        self.clones.pop(clone_id, None)

    def add_frame(self, clone_id: str, frame_id: str, item: dict) -> None:
        self.frames.setdefault(clone_id, {})[frame_id] = {
            "clone_id": clone_id,
            "frame_id": frame_id,
            **item,
        }

    def list_frames(self, clone_id: str) -> list[dict]:
        rows = self.frames.get(clone_id, {})
        return [rows[k] for k in sorted(rows)]  # DynamoDB: ascending range key

    def count_frames(self, clone_id: str) -> int:
        return len(self.frames.get(clone_id, {}))

    def delete_frames(self, clone_id: str, frame_ids: list[str]) -> None:
        for frame_id in frame_ids:
            self.frames.get(clone_id, {}).pop(frame_id, None)

    def put_backup(self, item: dict) -> None:
        self.backups.setdefault(item["clone_id"], {})[item["backup_id"]] = dict(item)

    def get_backup(self, clone_id: str, backup_id: str):
        return self.backups.get(clone_id, {}).get(backup_id)

    def list_backups(self, clone_id: str) -> list[dict]:
        rows = self.backups.get(clone_id, {})
        return [rows[k] for k in sorted(rows, reverse=True)]  # newest first

    def delete_backup(self, clone_id: str, backup_id: str) -> None:
        self.backups.get(clone_id, {}).pop(backup_id, None)


class FakeBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def ping(self) -> None:
        pass

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class Clock:
    """Strictly increasing fake time so generated ids sort in call order."""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now
