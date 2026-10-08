"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "StockPilot"
    environment: str = "development"
    model_name: str = ""
    model_api_key: str = ""
    model_base_url: str = ""
    model_thinking_enabled: bool = True
    sentiment_model_name: str = "deepseek-flash"
    database_url: str = "sqlite:///./stockpilot.db"
    market_data_provider: str = "eastmoney"
    watchlist_prefetch_enabled: bool = True
    watchlist_prefetch_workers: int = Field(default=4, ge=1, le=16)
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000", "http://127.0.0.1:3000"
    ])

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
