"""History endpoints — query task run history."""

from __future__ import annotations

from fastapi import APIRouter, Query

import db

router = APIRouter()


@router.get("")
async def list_history(limit: int = Query(200, ge=1, le=1000)):
    return db.recent_runs(limit)
