"""Cached public forum opinion summaries."""

from typing import Annotated
from fastapi import APIRouter, Depends, Request
from app.api.routes import Code
from app.api.schemas import StockDataResponse
from app.models.sentiment import SentimentReport

router = APIRouter(prefix="/api")


def service(request: Request): return request.app.state.sentiment_service


@router.get("/stocks/{code}/sentiment", response_model=StockDataResponse[SentimentReport])
async def sentiment(code: Code, opinion: Annotated[object, Depends(service)]):
    result = await opinion.get(code)
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
        collection_state="stale" if result.stale else "partial" if result.data.partial else "ready")
