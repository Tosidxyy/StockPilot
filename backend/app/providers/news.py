"""Verified EastMoney public news search and securities headlines adapter."""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.models.news import NewsBatch, NewsItem
from app.providers.exceptions import DataSourceError, ProviderTimeoutError

BEIJING = timezone(timedelta(hours=8))
SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
MARKET_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"


def plain_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()[:maximum]


def parse_item(row: dict, symbol: str | None) -> NewsItem:
    code = str(row.get("code", ""))
    title = plain_text(row.get("title"), 500)
    if not re.fullmatch(r"\d{10,30}", code) or not title:
        raise ValueError("Invalid news identifier/title")
    url = row.get("uniqueUrl") or row.get("url") or f"https://finance.eastmoney.com/a/{code}.html"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or not parts.hostname.endswith(".eastmoney.com") or parts.username or parts.password:
        raise ValueError("Invalid news source URL")
    url = urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
    published = datetime.fromisoformat(str(row.get("date") if symbol else row.get("showTime")))
    if published.tzinfo is None:
        published = published.replace(tzinfo=BEIJING)
    return NewsItem(id=f"eastmoney:{code}", title=title,
                    source=plain_text(row.get("mediaName"), 100) or "东方财富（来源未注明）",
                    published_at=published.astimezone(timezone.utc), url=url,
                    excerpt=plain_text(row.get("content") if symbol else row.get("summary"), 2000) or None,
                    symbols=[symbol] if symbol else [], association="keyword_search" if symbol else "market_column")


class EastMoneyNews:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _page(self, symbol: str | None, page: int) -> tuple[list[dict], bool]:
        if symbol:
            search = {"uid": "", "keyword": symbol, "type": ["cmsArticleWebOld"], "client": "web",
                      "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {
                          "searchScope": "default", "sort": "time", "pageIndex": page, "pageSize": 20,
                          "preTag": "", "postTag": ""}}}
            url, params = SEARCH_URL, {"cb": "stockpilot", "param": json.dumps(search)}
        else:
            url, params = MARKET_URL, {"client": "web", "biz": "web_news_col", "column": 353,
                "order": 1, "needInteractData": 0, "page_index": page, "page_size": 20,
                "req_trace": str(uuid4()), "fields": "code,showTime,title,mediaName,summary,url,uniqueUrl", "types": "1,20"}
        try:
            response = await self._client.get(url, params=params, timeout=10,
                                              headers={"Referer": "https://so.eastmoney.com/" if symbol else "https://finance.eastmoney.com/"})
            response.raise_for_status()
            text = response.text.strip()
            if symbol and text.startswith("stockpilot("):
                text = text[len("stockpilot("):]
                if text.endswith(";"):
                    text = text[:-1]
                if not text.endswith(")"):
                    raise ValueError("Invalid JSONP")
                text = text[:-1]
            payload = json.loads(text)
            if symbol:
                if payload.get("code") not in (0, "0") or payload.get("bizCode"):
                    raise ValueError("News search rejected")
                rows = payload["result"]["cmsArticleWebOld"]
                total = int(payload["hitsTotal"])
                more = page * 20 < total
            else:
                if str(payload.get("code")) != "1":
                    raise ValueError("News column rejected")
                rows = payload["data"]["list"]
                more = len(rows) == 20
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("Invalid news list")
            return rows, more
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("News source timed out") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DataSourceError("News source temporarily unavailable") from exc

    async def collect(self, symbol: str | None) -> NewsBatch:
        items: dict[str, NewsItem] = {}
        complete, more = True, False
        for page in range(1, 6):
            try:
                rows, more = await self._page(symbol, page)
            except DataSourceError:
                if page == 1:
                    raise
                complete, more = False, True
                break
            invalid = 0
            for row in rows:
                try:
                    item = parse_item(row, symbol)
                    items[item.id] = item
                except (ValueError, TypeError, AttributeError):
                    invalid += 1
            if rows and invalid == len(rows) and not items:
                raise DataSourceError("News source returned no valid records")
            complete = complete and invalid == 0
            if not more:
                break
            await asyncio.sleep(0)  # Give other data lanes a chance to run.
        return NewsBatch(items=sorted(items.values(), key=lambda item: (item.published_at, item.id), reverse=True),
                         complete=complete, truncated=more)
