from __future__ import annotations

import pytest
from service import ApiError, Service

from shared import backup_codec


def _clone(service: Service, name: str = "alpha", tier: str = "standard") -> str:
    return service.create_clone({"designation": name, "tier": tier})["clone_id"]


def test_create_clone_and_conflict(service: Service) -> None:
    clone = service.create_clone({"designation": "alpha"})
    assert clone["tier"] == "free" and clone["status"] == "active"
    with pytest.raises(ApiError) as exc:
        service.create_clone({"designation": "alpha"})
    assert exc.value.status == 409


@pytest.mark.parametrize(
    "body", [{}, {"designation": ""}, {"designation": "Bad Name"}, {"designation": 7}]
)
def test_create_clone_validates_designation(service: Service, body: dict) -> None:
    with pytest.raises(ApiError) as exc:
        service.create_clone(body)
    assert exc.value.status == 422


def test_create_clone_validates_tier(service: Service) -> None:
    with pytest.raises(ApiError) as exc:
        service.create_clone({"designation": "alpha", "tier": "gold"})
    assert exc.value.status == 422


def test_write_and_status(service: Service) -> None:
    cid = _clone(service)
    for text in ("a", "b", "c"):
        service.write_memory(cid, {"content": text})
    assert service.stream_status(cid) == {"buffered_frames": 3}


def test_write_rejects_bad_input_and_unknown_clone(service: Service) -> None:
    cid = _clone(service)
    for body in ({}, {"content": ""}, {"content": "x" * 5000}):
        with pytest.raises(ApiError) as exc:
            service.write_memory(cid, body)
        assert exc.value.status == 422
    with pytest.raises(ApiError) as exc:
        service.write_memory("ghost", {"content": "x"})
    assert exc.value.status == 404


def test_rollup_moves_frames_into_a_decodable_backup(service, db, blobs) -> None:
    cid = _clone(service)
    for text in ("first", "second", "third"):
        service.write_memory(cid, {"content": text})

    backup = service.rollup(cid)

    assert backup["entry_count"] == 3
    assert db.count_frames(cid) == 0  # the buffer was drained
    stored = backup_codec.decode_payload(blobs.get(backup["storage_key"]))
    assert [e["content"] for e in stored] == ["first", "second", "third"]
    restored = service.get_backup(cid, backup["backup_id"])
    assert [e["content"] for e in restored["payload"]] == ["first", "second", "third"]


def test_rollup_without_frames_is_a_conflict(service: Service) -> None:
    cid = _clone(service)
    with pytest.raises(ApiError) as exc:
        service.rollup(cid)
    assert exc.value.status == 409


def test_retention_follows_the_tier_policy(service, db, blobs) -> None:
    free = _clone(service, "free-clone", "free")  # retain_count == 1
    std = _clone(service, "std-clone", "standard")  # retain_count == 4
    for cid in (free, std):
        for round_ in range(6):
            service.write_memory(cid, {"content": f"r{round_}"})
            service.rollup(cid)
    assert len(service.list_backups(free)) == 1
    assert len(service.list_backups(std)) == 4
    # pruning removes the objects too, not just the rows
    assert len(blobs.objects) == 1 + 4


def test_list_backups_is_newest_first(service: Service) -> None:
    cid = _clone(service, "premium-clone", "premium")
    ids = []
    for i in range(3):
        service.write_memory(cid, {"content": str(i)})
        ids.append(service.rollup(cid)["backup_id"])
    assert [b["backup_id"] for b in service.list_backups(cid)] == ids[::-1]


def test_delete_backup_removes_row_and_object(service, blobs) -> None:
    cid = _clone(service, "premium-clone", "premium")
    service.write_memory(cid, {"content": "x"})
    backup = service.rollup(cid)
    service.delete_backup(cid, backup["backup_id"])
    assert blobs.objects == {}
    with pytest.raises(ApiError) as exc:
        service.get_backup(cid, backup["backup_id"])
    assert exc.value.status == 404


def test_delete_clone_cascades(service, db, blobs) -> None:
    cid = _clone(service, "premium-clone", "premium")
    service.write_memory(cid, {"content": "x"})
    service.rollup(cid)
    service.write_memory(cid, {"content": "y"})  # still buffered
    service.delete_clone(cid)
    assert db.clones == {} and db.count_frames(cid) == 0 and blobs.objects == {}
    with pytest.raises(ApiError):
        service.get_clone(cid)
