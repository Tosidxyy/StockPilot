"""P0 market, stock and watchlist endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_market_service, get_stock_service, get_watchlist_service, get_stock_reader
from app.api.schemas import DataResponse, StockDataResponse, MarketOverviewResponse, WatchlistAddRequest
from app.models.collection import CollectionOverview
from app.services.reads import StockReadService
from app.models.market import IntradayPoint, KlineItem, MarketIndex, StockQuote, SymbolSearchResult
from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistEntry, WatchlistService

router = APIRouter(prefix="/api")

Stock = Annotated[StockService, Depends(get_stock_service)]
Market = Annotated[MarketService, Depends(get_market_service)]
Watchlist = Annotated[WatchlistService, Depends(get_watchlist_service)]
Reader = Annotated[StockReadService, Depends(get_stock_reader)]
Code = Annotated[str, Path(pattern=r"^[03468][0-9]{5}$")]


@router.get("/market/indices", response_model=DataResponse[list[MarketIndex]])
async def get_indices(market: Market) -> DataResponse[list[MarketIndex]]:
    result = await market.get_indices()
    return DataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at)


@router.get("/market/indices/{code}/intraday", response_model=DataResponse[list[IntradayPoint]])
async def get_index_intraday(
    market: Market, code: Annotated[str, Path(pattern=r"^(000001|399001|399006)$")]
) -> DataResponse[list[IntradayPoint]]:
    result = await market.get_index_intraday(code)
    return DataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at)


@router.get("/market/overview", response_model=MarketOverviewResponse)
async def get_market_overview(market: Market, watchlist: Watchlist) -> MarketOverviewResponse:
    result = await market.get_indices()
    entries = await run_in_threadpool(watchlist.list_entries)
    return MarketOverviewResponse(
        indices=result.data, watchlist_count=len(entries), stale=result.stale, cached_at=result.cached_at
    )


@router.get("/stocks/search", response_model=DataResponse[list[SymbolSearchResult]])
async def search_stocks(
    stock: Stock,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DataResponse[list[SymbolSearchResult]]:
    result = await stock.search(q, limit)
    return DataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at)


@router.get("/stocks/quotes", response_model=StockDataResponse[list[StockQuote]])
async def get_batch_quotes(
    reader: Reader,
    codes: Annotated[str, Query(min_length=1)],
) -> StockDataResponse[list[StockQuote]]:
    symbols = [part.strip() for part in codes.split(",")]
    if len(symbols) > 50 or any(
        len(symbol) != 6 or symbol[0] not in "03468" or not symbol.isascii() or not symbol.isdigit()
        for symbol in symbols
    ):
        raise HTTPException(status_code=422, detail="codes must contain 1-50 valid A-share symbols")
    result = await reader.quotes(list(dict.fromkeys(symbols)))
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
                             collection_state=getattr(result, "state", None))


@router.get("/stocks/{code}/quote", response_model=StockDataResponse[StockQuote | None])
async def get_stock_quote(reader: Reader, code: Code):
    result = await reader.quote(code)
    if result.data is None and getattr(result, "state", None) is None:
        raise HTTPException(status_code=404, detail="Stock quote not found")
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
                             collection_state=getattr(result, "state", None))


@router.get("/stocks/{code}/kline", response_model=StockDataResponse[list[KlineItem]])
async def get_stock_kline(
    reader: Reader,
    code: Code,
    period: Literal["daily", "weekly"] = "daily",
    limit: Annotated[int, Query(ge=1, le=1000)] = 120,
) -> StockDataResponse[list[KlineItem]]:
    result = await reader.kline(code, period, limit)
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
                             collection_state=getattr(result, "state", None))


@router.get("/stocks/{code}/intraday", response_model=StockDataResponse[list[IntradayPoint]])
async def get_stock_intraday(reader: Reader, code: Code):
    result = await reader.intraday(code)
    return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at,
                             collection_state=getattr(result, "state", None))


@router.get("/watchlist/quotes", response_model=StockDataResponse[list[StockQuote]])
async def get_watchlist_quotes(reader: Reader):
    symbols = await reader.symbols()
    if not symbols:
        return StockDataResponse(data=[])
    if reader.collector is not None:
        result = await reader.quotes(symbols)
        return StockDataResponse(data=result.data, stale=result.stale, cached_at=result.cached_at, collection_state=result.state)
    # The legacy query endpoint is limited to 50; the complete watchlist need not be.
    results = [await reader.quotes(symbols[offset:offset + 50]) for offset in range(0, len(symbols), 50)]
    return StockDataResponse(data=[item for result in results for item in result.data],
                             stale=any(result.stale for result in results),
                             cached_at=min((r.cached_at for r in results if r.cached_at), default=None))


@router.get("/watchlist/status", response_model=DataResponse[CollectionOverview])
async def get_collection_status(reader: Reader):
    return DataResponse(data=await reader.overview())


@router.post("/watchlist/refresh", status_code=202)
async def refresh_watchlist(reader: Reader):
    if reader.collector is None:
        raise HTTPException(status_code=409, detail="Background collection is disabled")
    symbols = await reader.symbols()
    reader.collector.request_refresh(set(symbols))
    return {"requested": symbols}


@router.post("/watchlist/{code}/refresh", status_code=202)
async def refresh_watchlist_stock(reader: Reader, code: Code):
    if reader.collector is None:
        raise HTTPException(status_code=409, detail="Background collection is disabled")
    if code not in await reader.symbols():
        raise HTTPException(status_code=404, detail="Watchlist symbol not found")
    reader.collector.request_refresh({code})
    return {"requested": [code]}


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
