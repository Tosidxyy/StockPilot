"""Latest available daily net flows, independently of quote availability."""

from typing import Annotated
from fastapi import APIRouter, Depends, Query, Request
from app.api.routes import Code
from app.api.schemas import StockDataResponse
from app.models.money_flow import MoneyFlowSeries
from app.services.money_flow import MoneyFlowService

router = APIRouter(prefix="/api")


def service(request: Request) -> MoneyFlowService:
    return request.app.state.money_flow_service


Flows = Annotated[MoneyFlowService, Depends(service)]


@router.get("/stocks/{code}/money-flow", response_model=StockDataResponse[MoneyFlowSeries])
async def money_flow(flows: Flows, code: Code, limit: Annotated[int, Query(ge=1, le=30)] = 30):
    result = await flows.get(code, limit)
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at, collection_state=result.state)


@router.post("/stocks/{code}/money-flow/refresh", status_code=202)
async def refresh_money_flow(flows: Flows, code: Code):
    if flows.collector and await flows.is_background(code):
        flows.collector.request_refresh({code})
    else:
        await flows.fetch(code)
    return {"requested": [code]}
