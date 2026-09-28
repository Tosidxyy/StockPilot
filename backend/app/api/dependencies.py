"""Request-scoped access to application services."""

from fastapi import Request

from app.agent.service import StockAgentService
from app.services.market import MarketService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.services.trace import TraceService
from app.services.reads import StockReadService


def get_stock_service(request: Request) -> StockService:
    return request.app.state.stock_service


def get_stock_reader(request: Request) -> StockReadService:
    return StockReadService(request.app.state.stock_service, request.app.state.watchlist_service,
                            getattr(request.app.state, "watchlist_collector", None))


def get_market_service(request: Request) -> MarketService:
    return request.app.state.market_service


def get_watchlist_service(request: Request) -> WatchlistService:
    return request.app.state.watchlist_service


def get_agent_service(request: Request) -> StockAgentService:
    return request.app.state.agent_service


def get_trace_service(request: Request) -> TraceService:
    return request.app.state.trace_service
