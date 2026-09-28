"""P0 market, stock and watchlist endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_market_service, get_stock_service, get_watchlist_service
from app.api.schemas import DataResponse, MarketOverviewResponse, WatchlistAddRequest
from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistEntry, WatchlistService

router = APIRouter(prefix="/api")

Stock = Annotated[StockService, Depends(get_stock_service)]
Market = Annotated[MarketService, Depends(get_market_service)]
Watchlist = Annotated[WatchlistService, Depends(get_watchlist_service)]
Code = Annotated[str, Path(pattern=r"^[03468][0-9]{5}$")]


@router.get("/market/indices", response_model=DataResponse[list[MarketIndex]])
async def get_indices(market: Market) -> DataResponse[list[MarketIndex]]:
    result = await market.get_indices()
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/market/indices/{code}/intraday", response_model=DataResponse[list[IntradayPoint]])
async def get_index_intraday(
    market: Market, code: Annotated[str, Path(pattern=r"^(000001|399001|399006)$")]
) -> DataResponse[list[IntradayPoint]]:
    result = await market.get_index_intraday(code)
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/market/overview", response_model=MarketOverviewResponse)
async def get_market_overview(market: Market, watchlist: Watchlist) -> MarketOverviewResponse:
    result = await market.get_indices()
    entries = await run_in_threadpool(watchlist.list_entries)
    return MarketOverviewResponse(
        indices=result.data, watchlist_count=len(entries), stale=result.stale
    )


@router.get("/stocks/search", response_model=DataResponse[list[SymbolSearchResult]])
async def search_stocks(
    stock: Stock,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DataResponse[list[SymbolSearchResult]]:
    result = await stock.search(q, limit)
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/stocks/quotes", response_model=DataResponse[list[StockQuote]])
async def get_batch_quotes(
    stock: Stock,
    codes: Annotated[str, Query(min_length=1)],
) -> DataResponse[list[StockQuote]]:
    symbols = [part.strip() for part in codes.split(",")]
    if len(symbols) > 50 or any(
        len(symbol) != 6 or symbol[0] not in "03468" or not symbol.isascii() or not symbol.isdigit()
        for symbol in symbols
    ):
        raise HTTPException(status_code=422, detail="codes must contain 1-50 valid A-share symbols")
    result = await stock.get_quotes(symbols)
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/stocks/{code}/quote", response_model=DataResponse[StockQuote])
async def get_stock_quote(stock: Stock, code: Code) -> DataResponse[StockQuote]:
    result = await stock.get_quote(code)
    if result.data is None:
        raise HTTPException(status_code=404, detail="Stock quote not found")
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/stocks/{code}/kline", response_model=DataResponse[list[KlineItem]])
async def get_stock_kline(
    stock: Stock,
    code: Code,
    period: Literal["daily", "weekly"] = "daily",
    limit: Annotated[int, Query(ge=1, le=1000)] = 120,
) -> DataResponse[list[KlineItem]]:
    result = await stock.get_kline(code, period, limit)
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/stocks/{code}/intraday", response_model=DataResponse[list[IntradayPoint]])
async def get_stock_intraday(stock: Stock, code: Code) -> DataResponse[list[IntradayPoint]]:
    result = await stock.get_intraday(code)
    return DataResponse(data=result.data, stale=result.stale)


@router.get("/watchlist", response_model=DataResponse[list[WatchlistEntry]])
def get_watchlist(watchlist: Watchlist) -> DataResponse[list[WatchlistEntry]]:
    return DataResponse(data=watchlist.list_entries())


@router.post("/watchlist", response_model=WatchlistEntry, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(body: WatchlistAddRequest, watchlist: Watchlist) -> WatchlistEntry:
    return watchlist.add(body.symbol)


@router.delete("/watchlist/{code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(watchlist: Watchlist, code: Code) -> Response:
    if not watchlist.remove(code):
        raise HTTPException(status_code=404, detail="Watchlist symbol not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
