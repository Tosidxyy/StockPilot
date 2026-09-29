"""Per-run visible tool outcomes, isolated source failures and data-time notes."""
from datetime import datetime
import re
from app.agent.trace import record_tool
from app.providers.exceptions import DataSourceError, ProviderTimeoutError
from app.services.news import BEIJING
from app.agent.citations import citation_references

REQUEST_LIMIT = 8
TOOL_LIMIT = 12
LABELS = {"get_stock_quote":"报价","get_stock_kline":"K线","get_market_indices":"市场指数",
    "get_market_breadth":"市场涨跌统计","get_stock_money_flow":"资金流","get_stock_news":"新闻",
    "get_market_news":"市场新闻","get_stock_announcements":"公告","get_watchlist":"自选股行情",
    "search_stock_documents":"资料检索","get_watchlist_insights":"自选股资讯","get_stock_sentiment":"股吧情绪"}


async def invoke_tool(deps,name,inputs,call):
    if deps.progress:
        await deps.progress("正在读取" + LABELS.get(name, "相关数据") + "…")
    try:
        result = await record_tool(deps.trace_steps,name,inputs,call)
    except (DataSourceError,ValueError) as error:
        result = {"available":False,"collection_state":"unavailable","error_status":
            504 if isinstance(error,ProviderTimeoutError) else 503 if isinstance(error,DataSourceError) else 422,
            "error":"数据源暂不可用，请使用已成功的其他数据，并明确本项缺失；不要补造数值。" if isinstance(error,DataSourceError) else "工具参数无效，请在预算内修正后重试。"}
    deps.tool_results.append({"tool":name,"input":inputs,"result":result})
    references = {identifier: f"[{key}]" for key, identifier in citation_references(deps.document_searches).items()}
    for evidence in result.get("evidence", []):
        if evidence["evidence_id"] in references:
            evidence["citation_ref"] = references[evidence["evidence_id"]]
    if deps.progress:
        await deps.progress(LABELS.get(name, "数据") + ("暂不可用，继续核对其他依据" if result.get("available") is False else "已读取"))
    return result


def available_market_data(outcomes):
    for item in outcomes:
        r = item["result"]
        if r.get("available") is False: continue
        if item["tool"] == "get_stock_quote" and r.get("quote"):
            return True
        if item["tool"] in ("get_stock_kline","get_market_indices","get_stock_money_flow","get_watchlist") and any(
            r.get(key) for key in ("klines","indices","items","quotes")):
            return True
        if item["tool"] == "get_market_breadth" and any(e.get("as_of") for e in r.get("exchanges",[])):
            return True
    return False


def all_failed_status(outcomes):
    if not outcomes or any(r["result"].get("available") is not False for r in outcomes): return None
    codes = [r["result"]["error_status"] for r in outcomes]
    return 504 if all(c==504 for c in codes) else 503 if any(c==503 for c in codes) else 502


def beijing_time(value):
    if not value: return "未提供"
    try:
        stamp = datetime.fromisoformat(value.replace("Z","+00:00"))
        if stamp.tzinfo is None: return "未提供"
        return stamp.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError,TypeError): return "未提供"


def data_notes(outcomes):
    lines=[]
    for item in outcomes:
        name,r = item["tool"],item["result"]
        symbol = item["input"].get("symbol", "")
        label = (symbol + " " if isinstance(symbol, str) and re.fullmatch(r"[03468][0-9]{5}", symbol) else "") + LABELS.get(name,"数据")
        if r.get("available") is False:
            note=label.strip()+"：本次不可用，未取得本项数据。"
        elif name == "get_stock_sentiment":
            note = label.strip() + f"：东方财富股吧，{'旧缓存' if r.get('stale') else '缓存读取'}；保存 {beijing_time(r.get('cached_at'))}（北京时间）；样本 {r['sample_count']} 条，发帖标题 {r['post_count']} 条、回复 {r['reply_count']} 条；发言时间 {beijing_time(r.get('sample_start'))} 至 {beijing_time(r.get('sample_end'))}；关键词规则，不代表全体投资者或买卖信号。"
            if r.get("partial"): note += " 部分样本无法解析。"
        elif name in ("get_stock_news","get_market_news","get_stock_announcements","search_stock_documents","get_watchlist_insights"):
            states = r.get("source_states", {})
            if not states and name == "get_watchlist_insights":
                states = {row["symbol"]+" "+kind:state for row in r.get("stocks", [])
                    for kind in ("news", "announcement") for state in row[kind]["source_states"].values()}
            if states:
                note = label.strip()+"："+"；".join(f"{kind.replace("announcement", "公告").replace("news", "新闻")} {dict(ready='已采集',stale='旧缓存',partial='部分资料',not_collected='未采集',unavailable='不可用').get(state['state'],state['state'])}，保存 {beijing_time(state.get('cached_at'))}（北京时间）" for kind,state in states.items())
            else:
                note = label.strip()+f"：{'旧缓存' if r.get('stale') else '缓存读取'}；保存 {beijing_time(r.get('cached_at'))}（北京时间）；资料日期见证据来源。"
            if name == "get_watchlist_insights":
                note += f"；覆盖 {len(r['stocks'])}/{r['watchlist_total']} 只，未覆盖 {r['remaining_count']} 只；每股每类最多一个片段。"
        else:
            items=r.get("items",r.get("klines",r.get("quotes",r.get("indices",[]))))
            dates=sorted({v["date"] for v in items if v.get("date")})
            quote = r.get("quote") or {}
            source=r.get("source") or quote.get("source") or "/".join(sorted({v["source"] for v in items if v.get("source")})) or "东方财富"
            source="/".join({"eastmoney":"东方财富","tencent":"腾讯财经","sina":"新浪财经"}.get(v,v) for v in source.split("/"))
            status="旧缓存" if r.get("stale") else "缓存读取"
            if r.get("collection_state") in ("warming","unavailable","partial") or r.get("partial"): status+="，部分/缺失"
            note=label.strip()+f"：{source}，{status}；保存于 {beijing_time(r.get('cached_at'))}（北京时间）"
            if name == "get_market_breadth":
                note += "；各市场来源时间 " + "，".join(f"{e.get('market',e.get('exchange','市场'))} {beijing_time(e.get('as_of'))}" for e in r.get("exchanges",[]))
                note += "；涨跌停股池各自日期 " + "，".join(str(r.get(k, {}).get("date") or "未提供") for k in ("limit_up","limit_down"))
            else:
                if name == "get_market_indices":
                    note += "；来源时间（北京时间） " + "，".join(f"{row['symbol']} {beijing_time(row.get('as_of'))}" for row in items)
                elif name == "get_watchlist":
                    note += "；源成交时间（北京时间） " + "，".join(f"{row['symbol']} {beijing_time(row.get('as_of'))}" for row in items)
                else:
                    note+=f"；资料日期 {dates[0]} 至 {dates[-1]}。" if dates else f"；源成交时间 {beijing_time(quote['as_of'])}（北京时间）。" if quote.get("as_of") else "；源成交时间未提供，保存时间不代表成交时间。"
        if note not in lines: lines.append(note)
    if not lines: return ""
    return "\n\n### 数据时间与限制\n\n"+"\n".join("- "+line for line in lines)+"\n\n各类数据的来源时间可能不同，不能视作同一时刻快照；缺失不是零，资讯相关性不能证明涨跌因果。"
