"""FastAPI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pydantic_ai.models import Model

from app.agent.service import StockAgentService
from app.agent.tools import AgentDependencies
from app.api.agent import router as agent_router
from app.api.routes import router
from app.api.news import router as news_router
from app.api.announcements import router as announcement_router
from app.core.config import get_settings
from app.database.session import create_database_engine, create_session_factory, init_db
from app.providers.base import MarketDataProvider
from app.providers.resilient import ResilientMarketProvider
from app.providers.exceptions import DataSourceError, InvalidSymbolError, ProviderTimeoutError
from app.services.market import MarketService
from app.services.chat import ChatService
from app.services.trace import TraceService
from app.services.stock import StockService
from app.services.watchlist import WatchlistService
from app.services.collector import WatchlistCollector
from app.services.reads import StockReadService
from app.services.news import NewsService
from app.services.news_collector import NewsCollector
from app.services.announcements import AnnouncementService
from app.services.announcement_collector import AnnouncementCollector


def create_app(
    *,
    provider: MarketDataProvider | None = None,
    database_url: str | None = None,
    agent_model: Model | None = None,
    prefetch_enabled: bool | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(database_url)
        owns_provider = provider is None
        data_provider = provider if provider is not None else ResilientMarketProvider()
        collector = None
        news_collector = None
        announcement_collector = None
        try:
            init_db(engine)
            session_factory = create_session_factory(engine)
            application.state.stock_service = StockService(data_provider, snapshot_sessions=session_factory)
            application.state.market_service = MarketService(data_provider, snapshot_sessions=session_factory)
            application.state.watchlist_service = WatchlistService(session_factory)
            enabled = prefetch_enabled if prefetch_enabled is not None else (
                get_settings().watchlist_prefetch_enabled and owns_provider
            )
            news = NewsService(data_provider, session_factory, application.state.watchlist_service, background=enabled)
            application.state.news_service = news
            announcements = AnnouncementService(data_provider, session_factory, application.state.watchlist_service, background=enabled)
            application.state.announcement_service = announcements
            application.state.trace_service = TraceService(session_factory)
            reader = StockReadService(application.state.stock_service, application.state.watchlist_service, None)
            application.state.agent_service = StockAgentService(
                get_settings(),
                AgentDependencies(
                    stocks=application.state.stock_service,
                    market=application.state.market_service,
                    watchlist=application.state.watchlist_service,
                    reader=reader,
                    news=news,
                    announcements=announcements,
                ),
                ChatService(session_factory),
                application.state.trace_service,
                model=agent_model,
            )
            if enabled:
                collector = WatchlistCollector(
                    application.state.stock_service, application.state.watchlist_service,
                    workers=get_settings().watchlist_prefetch_workers,
                )
                collector.start()
                news_collector = NewsCollector(news)
                news.collector = news_collector
                news_collector.start()
                announcement_collector = AnnouncementCollector(announcements)
                announcements.collector = announcement_collector
                announcement_collector.start()
            application.state.watchlist_collector = collector
            reader.collector = collector
            yield
        finally:
            if announcement_collector is not None:
                await announcement_collector.stop()
            if news_collector is not None:
                await news_collector.stop()
            if collector is not None:
                await collector.stop()
            for name in ("stock_service", "market_service", "news_service", "announcement_service"):
                service = getattr(application.state, name, None)
                if service is not None:
                    await service.aclose()
            if owns_provider:
                await data_provider.aclose()
            engine.dispose()

    application = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Agent-Session-ID"],
    )
    application.include_router(router)
    application.include_router(news_router)
    application.include_router(announcement_router)
    application.include_router(agent_router)

    @application.exception_handler(ProviderTimeoutError)
    async def provider_timeout(_request: Request, _exc: ProviderTimeoutError) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": "Market data provider timed out"})

    @application.exception_handler(DataSourceError)
    async def data_source_error(_request: Request, _exc: DataSourceError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Market data temporarily unavailable"})

    @application.exception_handler(InvalidSymbolError)
    async def invalid_symbol(_request: Request, _exc: InvalidSymbolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": "Invalid stock symbol"})

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
