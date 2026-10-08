"""Cached public forum opinion summaries."""

from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from app.api.routes import Code
from app.api.schemas import StockDataResponse
from app.models.sentiment import SentimentReport
from app.models.sentiment_analysis import AnalysisView
from app.api.schemas import DataResponse
from app.services.sentiment_analysis import NoSentimentSamples, SentimentNotConfigured

router = APIRouter(prefix="/api")


def service(request: Request): return request.app.state.sentiment_service


@router.get("/stocks/{code}/sentiment", response_model=StockDataResponse[SentimentReport])
async def sentiment(code: Code, opinion: Annotated[object, Depends(service)]):
    result = await opinion.get(code)
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
        collection_state="stale" if result.stale else "partial" if result.data.partial else "ready")


@router.get("/stocks/{code}/sentiment/analysis", response_model=DataResponse[AnalysisView])
async def sentiment_analysis(code: Code, request: Request):
    return DataResponse(data=await request.app.state.sentiment_analysis_service.view(code))


@router.post("/stocks/{code}/sentiment/analysis", response_model=DataResponse[AnalysisView], status_code=202)
async def start_sentiment_analysis(code: Code, request: Request):
    try:
        result = await request.app.state.sentiment_analysis_service.start(code)
    except SentimentNotConfigured:
        raise HTTPException(status_code=503, detail="请先配置模型 API Key。")
    except NoSentimentSamples:
        raise HTTPException(status_code=409, detail="当前没有可用的近24小时股吧样本。")
    except RuntimeError:
        raise HTTPException(status_code=429, detail="已有较多统计任务，请稍后再试。")
    return DataResponse(data=result)
