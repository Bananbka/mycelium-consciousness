from __future__ import annotations

import hashlib
import logging
import math
import os
from functools import lru_cache
from typing import Protocol

from shared.db.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """The provider returned nothing usable."""


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic local embedder used when no provider is configured.

    Produces a unit-norm bag-of-tokens vector by hashing each token into a
    dimension. Semantically crude, but it keeps write and search paths
    exercisable offline and makes tests reproducible without a network call.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = [token for token in text.lower().split() if token]

        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class GeminiEmbedder:
    """Embeds via the Google Gemini API.

    Requires the optional dependency: `uv sync --extra gemini`. The client is
    built in __init__ so a missing package surfaces when the embedder is
    selected, not as a 500 on the first write.
    """

    def __init__(self, api_key: str, model: str, dim: int = EMBEDDING_DIM) -> None:
        from google import genai

        self.model = model
        self.dim = dim
        self._client = genai.Client(api_key=api_key)

    async def embed(self, text: str) -> list[float]:
        from google.genai import types

        response = await self._client.aio.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self.dim),
        )

        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            raise EmbeddingError(
                f"{self.model} returned no embeddings (quota, safety filter, "
                "or empty input)"
            )

        values = list(embeddings[0].values or [])
        if len(values) != self.dim:
            # Caught here rather than as an opaque INSERT failure against the
            # Vector(dim) column.
            raise EmbeddingError(
                f"{self.model} returned {len(values)} dimensions, expected {self.dim}"
            )
        return values


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    placeholders = {"", "replace-me", "...", "your-api-key"}

    if api_key in placeholders:
        return HashingEmbedder()

    model = os.getenv("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    try:
        return GeminiEmbedder(api_key=api_key, model=model)
    except ImportError:
        # A configured key must not take the write path down when the optional
        # provider package is absent.
        logger.warning(
            "GEMINI_API_KEY is set but google-genai is not installed; "
            "falling back to the local HashingEmbedder. "
            "Install it with: uv sync --extra gemini"
        )
        return HashingEmbedder()
