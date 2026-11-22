"""Fixtures: the service wired to in-memory stores, no AWS endpoint needed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import handler  # noqa: E402
from inmemory import Clock, FakeBlobs, FakeDb  # noqa: E402
from service import Service  # noqa: E402


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def blobs() -> FakeBlobs:
    return FakeBlobs()


@pytest.fixture
def service(db: FakeDb, blobs: FakeBlobs) -> Service:
    return Service(db, blobs, clock=Clock())


@pytest.fixture
def wired(service: Service):
    """The Lambda handler wired to the fake service."""
    handler.set_service(service)
    yield handler
    handler.set_service(None)
