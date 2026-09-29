"""Resolve citations from this run's evidence, never from model-invented links."""
import re
from urllib.parse import urlparse

NO_EVIDENCE = "本次检索没有可支持回答的本地证据。仅覆盖已采集的30天新闻片段与90天公告；正文可能不可用、尚未采集或关键词未命中，不能据此断言事项不存在。"
INVALID_CITATIONS = "本次回答未通过证据引用校验，暂不展示未核验的结论。请缩小股票、日期或关键词后重试。"


def wants_documents(message):
    return any(word in message for word in ("检索", "证据", "根据资料", "查阅", "公告正文", "新闻全文"))


def safe_label(value):
    return re.sub(r"[\[\]()<>`*_\\\r\n]", " ", value)


def cited_evidence(answer, searches):
    available = {e["evidence_id"]:e for result in searches for e in result["evidence"]}
    ids = dict.fromkeys(re.findall(r"\[(E[A-Za-z0-9_-]+)\]", answer))
    return [available[i] for i in ids if i in available][:20]


def finalize_documents(answer, searches, message, *, allow_without_evidence=False):
    if not searches:
        return NO_EVIDENCE if wants_documents(message) and not allow_without_evidence else answer
    evidence = {e["evidence_id"]: e for result in searches for e in result["evidence"]}
    if not evidence:
        if allow_without_evidence and not re.search(r"\[E[A-Za-z0-9_-]+\]|https?://|\[[^\]]*\]\s*\(|<\s*[A-Za-z/!]", answer, re.I):
            return answer + "\n\n资讯检索未取得可引用证据，不能据此确认资讯事实或解释涨跌原因。"
        return NO_EVIDENCE
    # Some compatible models return complete IDs without brackets. Canonicalize
    # only the exact versioned-ID format; the same membership check still applies.
    answer = re.sub(r"(?<![A-Za-z0-9_\[])E[0-9a-f]{24}(?![A-Za-z0-9_\]])",
        lambda match: "[" + match.group() + "]", answer)
    cited = re.findall(r"\[(E[A-Za-z0-9_-]+)\]", answer)
    # Source URLs are appended by the server. Reject raw HTML or supplied links.
    if not cited or len(set(cited)) > 20 or any(i not in evidence for i in cited) or re.search(
        r"https?://|(?:javascript|data|file):|\[[^\]]*\]\s*\(|<\s*[A-Za-z/!]", answer, re.I):
        return INVALID_CITATIONS
    sources = []
    for identifier in dict.fromkeys(cited):
        item = evidence[identifier]
        url = item["url"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c in url for c in "\n\r<>\" "):
            return INVALID_CITATIONS
        url = url.replace("(", "%28").replace(")", "%29")
        status = "标题/来源片段，非全文" if item["kind"] == "news" else "仅元信息" if not item["body_available"] else "提取文本片段（"+item["text_status"]+"）"
        status += "，旧缓存/旧正文" if item["text_stale"] else ""
        sources.append(f"- [{identifier}] [{safe_label(item['title'])}]({url}) · {safe_label(item['source'])} · {item['date']} · 文档 {safe_label(item['document_id'])} · {status}")
    return answer + "\n\n### 证据来源\n\n" + "\n".join(sources) + "\n\n引用仅核验片段标识与来源对应，不保证模型解读无误，请核对原文。行情信息仅供参考，不构成投资建议。"
