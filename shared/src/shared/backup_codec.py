from __future__ import annotations

import gzip

import msgpack

_GZIP_MAGIC = b"\x1f\x8b"


def encode_payload(entries: list[dict]) -> bytes:
    packed = msgpack.packb(entries, use_bin_type=True)
    compressed = gzip.compress(packed, compresslevel=6)
    return compressed if len(compressed) < len(packed) else packed


def decode_payload(data: bytes) -> list[dict]:
    if data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
    return msgpack.unpackb(data, raw=False)
