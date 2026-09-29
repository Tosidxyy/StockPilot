"""Read only JSON embedded in EastMoney's public forum HTML; never execute JS."""

import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx

from app.database.models import utc_now
from app.models.sentiment import CommentBatch, ForumComment
from app.providers.exceptions import DataSourceError, ProviderTimeoutError

BEIJING = timezone(timedelta(hours=8))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.hidden += 1
        elif tag in ("br", "p", "div"): self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"): self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def clean_text(value: object) -> str:
    if not isinstance(value, str): return ""
    parser = _Text()
    parser.feed(value[:20000])
    return " ".join("".join(parser.parts).split())[:1000]


def _embedded(html: str, name: str, *, required=False):
    match = re.search(r"\bvar\s+" + name + r"\s*=\s*", html)
    if not match:
        if required: raise DataSourceError("Forum page has no public sample data")
        return None
    try:
        data, end = json.JSONDecoder().raw_decode(html[match.end():])
        if not isinstance(data, dict) or not html[match.end()+end:].lstrip().startswith(";"):
            raise ValueError()
        if data.get("rc") != 1 or not isinstance(data.get("re"), list) or len(data["re"]) > 500:
            raise ValueError()
        return data
    except (ValueError, TypeError) as error:
        raise DataSourceError("Invalid public forum sample data") from error


def parse_comments(html: str, symbol: str, *, now: datetime | None = None) -> CommentBatch:
    if len(html) > 2_000_000: raise DataSourceError("Forum page exceeds sample size limit")
    now = now or utc_now()
    main = _embedded(html, "article_list", required=True)
    if str(main.get("bar_code")) != symbol: raise DataSourceError("Forum stock does not match request")
    extra = _embedded(html, "other_list")
    items, partial = {}, False

    def add(identifier, post_id, text, stamp, kind):
        nonlocal partial
        try:
            published = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
            content = clean_text(text)
            if not content: return
            if published > now + timedelta(minutes=5): raise ValueError()
            if published < now - timedelta(hours=24): return
            item = ForumComment(id=identifier, symbol=symbol, text=content, published_at=published,
                url=f"https://guba.eastmoney.com/news,{symbol},{post_id}.html", kind=kind)
            items[identifier] = item
        except (ValueError, TypeError): partial = True

    for row in main["re"] + (extra["re"] if extra else []):
        if not isinstance(row, dict): partial = True; continue
        guba = row.get("post_guba") or {}
        if not isinstance(guba, dict): partial = True; continue
        code = row.get("stockbar_code") or guba.get("stockbar_code")
        if str(code) != symbol: continue
        post_id = str(row.get("post_id", ""))
        if not re.fullmatch(r"[1-9][0-9]{0,19}", post_id): partial = True; continue
        # News/announcements/wealth-account titles are not user opinions.
        if row.get("post_type") == 0:
            add("post:"+post_id, post_id, row.get("post_title"), row.get("post_publish_time"), "post_title")
        replies = row.get("reply_list") or []
        if not isinstance(replies, list): partial = True; continue
        for reply in replies[:30]:
            if not isinstance(reply, dict): partial = True; continue
            identifier = str(reply.get("reply_id", ""))
            if not re.fullmatch(r"[1-9][0-9]{0,19}", identifier): partial = True; continue
            add("reply:"+identifier, post_id, reply.get("reply_text"), reply.get("reply_time"), "reply")
    return CommentBatch(items=sorted(items.values(), key=lambda item: (item.published_at, item.id), reverse=True)[:200], partial=partial)


class EastMoneyComments:
    def __init__(self, client: httpx.AsyncClient): self.client = client

    async def collect(self, symbol: str) -> CommentBatch:
        try:
            response = await self.client.get(f"https://guba.eastmoney.com/list,{symbol}.html",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://guba.eastmoney.com/"}, timeout=8)
            response.raise_for_status()
            if len(response.content) > 2_000_000: raise DataSourceError("Forum page exceeds sample size limit")
            return parse_comments(response.text, symbol)
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Public forum timed out") from error
        except httpx.HTTPError as error:
            raise DataSourceError("Public forum unavailable") from error
