"""Read the shared announcement cache and public extracted text."""

from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from app.api.routes import Code
from app.api.schemas import StockDataResponse
from app.models.announcements import AnnouncementItem, AnnouncementPage
from app.services.announcements import AnnouncementService

router = APIRouter(prefix="/api/stocks")


def service(request: Request):
    return request.app.state.announcement_service


Announcements = Annotated[AnnouncementService, Depends(service)]


@router.get("/{code}/announcements", response_model=StockDataResponse[AnnouncementPage])
async def announcements(announcements: Announcements, code: Code,
    page: Annotated[int, Query(ge=1, le=1000)] = 1, page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    start: date | None = None, end: date | None = None,
    keyword: Annotated[str, Query(max_length=100)] = "", category: Annotated[str, Query(max_length=40)] = ""):
    try:
        result = await announcements.get(code, page=page, page_size=page_size, start=start, end=end, keyword=keyword, category=category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at, collection_state=result.state)


@router.post("/{code}/announcements/refresh", status_code=202)
async def refresh(announcements: Announcements, code: Code):
    if announcements.collector and any(item.symbol == code for item in await run_in_threadpool(announcements.watchlist.list_entries)):
        announcements.collector.request_refresh({code})
    else:
        await announcements.fetch(code, force=True)
    return {"requested": [code]}


@router.get("/{code}/announcements/{doc_id}", response_model=StockDataResponse[AnnouncementItem])
async def document(announcements: Announcements, code: Code,
    doc_id: Annotated[str, Path(pattern=r"^AN[0-9]{18}$")]):
    item = await announcements.document(code, doc_id)
    if item is None:
        raise HTTPException(404, "公告未在该股票最近90天的已采集范围内")
    state = "stale" if item.text_stale else {"pending": "warming", "ready": "ready", "partial": "partial", "unavailable": "unavailable"}[item.text_status]
    return StockDataResponse(data=item, stale=item.text_stale, cached_at=item.text_fetched_at, collection_state=state)
