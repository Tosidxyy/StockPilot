"""Local evidence retrieval, independent of model credentials and network access."""
from datetime import date
from typing import Annotated, Literal
from fastapi import APIRouter, Path, Query, Request, HTTPException

router = APIRouter(prefix="/api/stocks", tags=["documents"])


@router.get("/{code}/documents/search")
async def search_documents(request: Request, code: Annotated[str, Path(pattern=r"^[03468][0-9]{5}$")],
    query: Annotated[str, Query(min_length=1, max_length=100)], kind: Literal["all", "news", "announcement"] = "all",
    start: date | None = None, end: date | None = None, limit: Annotated[int, Query(ge=1,le=10)] = 6):
    try:
        return {"data": await request.app.state.document_service.search(code,query,kind=kind,start=start,end=end,limit=limit)}
    except ValueError as error:
        raise HTTPException(status_code=422,detail=str(error)) from None
