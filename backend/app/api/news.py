"""Local news pagination with explicit collection state and coverage."""

from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from app.api.schemas import StockDataResponse
from app.api.routes import Code
from app.models.news import NewsPage
from app.services.news import NewsService

router = APIRouter(prefix="/api")


def service(request: Request) -> NewsService:
    return request.app.state.news_service


News = Annotated[NewsService, Depends(service)]


async def read(news, symbol, page, page_size, start, end, keyword):
    try:
        result = await news.get(symbol, page=page, page_size=page_size, start=start, end=end, keyword=keyword)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at, collection_state=result.state)


@router.get("/stocks/{code}/news", response_model=StockDataResponse[NewsPage])
async def stock_news(news: News, code: Code, page: Annotated[int, Query(ge=1, le=1000)] = 1,
                     page_size: Annotated[int, Query(ge=1, le=50)] = 10,
                     start: date | None = None, end: date | None = None,
                     keyword: Annotated[str, Query(max_length=100)] = ""):
    return await read(news, code, page, page_size, start, end, keyword)


@router.get("/market/news", response_model=StockDataResponse[NewsPage])
async def market_news(news: News, page: Annotated[int, Query(ge=1, le=1000)] = 1,
                      page_size: Annotated[int, Query(ge=1, le=50)] = 10,
                      start: date | None = None, end: date | None = None,
                      keyword: Annotated[str, Query(max_length=100)] = ""):
    return await read(news, None, page, page_size, start, end, keyword)


@router.post("/stocks/{code}/news/refresh", status_code=202)
async def refresh_news(news: News, code: Code):
    if news.collector and any(item.symbol == code for item in await run_in_threadpool(news.watchlist.list_entries)):
        news.collector.request_refresh({code})
        return {"requested": [code]}
    await news.fetch(code, force=True)
    return {"requested": [code]}


@router.post("/market/news/refresh", status_code=202)
async def refresh_market_news(news: News):
    await news.fetch("market", force=True)
    return {"requested": ["market"]}
