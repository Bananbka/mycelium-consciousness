from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.config import MEMORY_SEARCH_DEFAULT_LIMIT, MEMORY_SEARCH_MAX_LIMIT
from api.deps import CurrentUser, DatabaseSession, OwnProfile
from api.schemas import MemoryCreateRequest, MemoryResponse, MemorySearchResult
from shared.db.models import MemoryChunk, UserRole
from shared.embeddings import get_embedder

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreateRequest,
    profile: OwnProfile,
    db: DatabaseSession,
) -> MemoryChunk:
    """Store a memory against the caller's own clone.

    clone_id comes from the authenticated profile, never from the request body,
    so a clone cannot write into another clone's memory stream.
    """
    embedding = await get_embedder().embed(payload.content)
    memory = MemoryChunk(
        clone_id=profile.id,
        content=payload.content,
        embedding=embedding,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


@router.get("", response_model=list[MemoryResponse])
async def list_own_memories(
    profile: OwnProfile,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryChunk]:
    result = await db.execute(
        select(MemoryChunk)
        .where(MemoryChunk.clone_id == profile.id)
        # id breaks ties: func.now() is transaction start time, so a whole
        # micro-batch shares one timestamp and the sort is otherwise unstable.
        .order_by(MemoryChunk.timestamp.desc(), MemoryChunk.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/search", response_model=list[MemorySearchResult])
async def search_own_memories(
    profile: OwnProfile,
    db: DatabaseSession,
    q: str = Query(min_length=1, max_length=1024),
    limit: int = Query(default=MEMORY_SEARCH_DEFAULT_LIMIT, ge=1),
) -> list[MemorySearchResult]:
    """Semantic search over the caller's own memories.

    The clone_id filter is applied inside the query, so the ANN scan can never
    surface another clone's memories regardless of ranking.
    """
    limit = min(limit, MEMORY_SEARCH_MAX_LIMIT)
    query_vector = await get_embedder().embed(q)

    # A zero vector has no direction, so cosine distance against it is NaN and
    # would serialise as a bare NaN token that strict JSON parsers reject.
    if not any(query_vector):
        return []

    distance = MemoryChunk.embedding.cosine_distance(query_vector)

    result = await db.execute(
        select(MemoryChunk, distance.label("distance"))
        .where(
            MemoryChunk.clone_id == profile.id,
            MemoryChunk.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(limit)
    )

    return [
        MemorySearchResult(
            id=memory.id,
            clone_id=memory.clone_id,
            content=memory.content,
            timestamp=memory.timestamp,
            similarity=1.0 - float(dist),
        )
        for memory, dist in result.all()
    ]


@router.get("/{memory_id}", response_model=MemoryResponse)
async def read_memory(
    memory_id: int,
    user: CurrentUser,
    db: DatabaseSession,
) -> MemoryChunk:
    memory = await db.get(MemoryChunk, memory_id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if user.role is not UserRole.ADMIN:
        profile = user.profile
        if profile is None or memory.clone_id != profile.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not own this memory",
            )

    return memory
