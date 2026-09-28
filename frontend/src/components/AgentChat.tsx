"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, apiRequest, errorText, streamChat } from "../lib/api";
import { MarkdownMessage } from "./MarkdownMessage";

type Message = { role: "user" | "assistant"; content: string; interrupted?: boolean };
type StoredSession = { session_id: string; messages: Message[] };

const sessionKey = "stockpilot-agent-session";
const suggestions = ["今天市场怎么样？", "今天我的自选股怎么样？", "东方财富最近五天走势？"];

export function AgentChat({
  compact = false, initialPrompt = "", onSessionChange, onTraceUpdated,
}: {
  compact?: boolean;
  initialPrompt?: string;
  onSessionChange?: (sessionId: string | null) => void;
  onTraceUpdated?: () => void;
}) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState(initialPrompt);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const stream = useRef<AbortController | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);

  useEffect(() => () => stream.current?.abort(), []);
  useEffect(() => {
    if (followOutput.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    let active = true;
    const stored = window.localStorage.getItem(sessionKey);
    void apiRequest<{ configured: boolean }>("/api/agent/status")
      .then((result) => { if (active) setConfigured(result.configured); })
      .catch(() => { if (active) setConfigured(null); })
      .finally(() => { if (active && !stored) setLoading(false); });
    if (stored) {
      void apiRequest<StoredSession>(`/api/agent/sessions/${encodeURIComponent(stored)}`)
        .then((result) => {
          if (!active) return;
          setSessionId(result.session_id);
          setMessages(result.messages);
          onSessionChange?.(result.session_id);
        })
        .catch(() => {
          if (active) window.localStorage.removeItem(sessionKey);
        })
        .finally(() => { if (active) setLoading(false); });
    }
    return () => { active = false; };
  }, [onSessionChange]);

  const send = async (value = draft) => {
    const message = value.trim();
    if (!message || busy || stream.current || configured !== true) return;
    setBusy(true);
    setError(null);
    setDraft("");
    followOutput.current = true;
    setMessages((current) => [...current, { role: "user", content: message }, { role: "assistant", content: "" }]);
    const controller = new AbortController();
    stream.current = controller;
    try {
      await streamChat(message, sessionId, controller.signal, (event) => {
        if (event.event === "session") {
          setSessionId(event.session_id);
          onSessionChange?.(event.session_id);
          window.localStorage.setItem(sessionKey, event.session_id);
        } else {
          setMessages((current) => current.map((item, index) => index === current.length - 1 ? {
            ...item, content: event.event === "delta" ? item.content + event.text : event.answer,
          } : item));
        }
      });
    } catch (cause) {
      if (cause instanceof ApiError && cause.sessionId) {
        setSessionId(cause.sessionId);
        onSessionChange?.(cause.sessionId);
        window.localStorage.setItem(sessionKey, cause.sessionId);
        onTraceUpdated?.();
      }
      setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, interrupted: true } : item));
      setDraft(message);
      setError(controller.signal.aborted ? "回复已停止，未完成的内容未保存。" : errorText(cause));
    } finally {
      stream.current = null;
      setBusy(false);
      onTraceUpdated?.();
    }
  };

  const newChat = () => {
    setMessages([]);
    setSessionId(null);
    onSessionChange?.(null);
    setError(null);
    window.localStorage.removeItem(sessionKey);
  };

  return (
    <div className={`agent-chat ${compact ? "compact" : ""}`}>
      <div className="agent-chat-head">
        <div className="agent-head"><span className="agent-icon">✦</span><div><h2>Stock Agent</h2><small>行情 Tool 驱动的对话</small></div></div>
        {messages.length > 0 && <button className="text-button" type="button" onClick={newChat} disabled={busy}>新对话</button>}
      </div>
      <div className="agent-messages" ref={viewport} onScroll={() => { const element = viewport.current; if (element) followOutput.current = element.scrollHeight - element.scrollTop - element.clientHeight < 60; }} aria-live="polite" aria-busy={busy}>
        {loading ? <div className="agent-empty">正在恢复对话…</div> :
          messages.length === 0 ? <div className="agent-empty"><span className="agent-orb">✦</span><h3>从真实行情开始分析</h3><p>可以查询指数、个股行情、K 线或自选股。回答会区分行情事实与分析。</p></div> :
            messages.map((item, index) => <div className={`agent-message ${item.role}`} key={index}><span>{item.role === "user" ? "你" : "Stock Agent"}</span>{item.role === "assistant" ? item.content ? <MarkdownMessage content={item.content} /> : <p>{item.interrupted ? "未收到完整回复。" : "正在读取行情并分析…"}</p> : <p>{item.content}</p>}{item.interrupted && item.content && <small className="stream-note">回复已中断 · 未保存</small>}{busy && index === messages.length - 1 && item.content && <small className="stream-note">正在回复…</small>}</div>)}
      </div>
      {configured === false && <p className="agent-config-note">模型尚未配置。请在后端设置模型名称与 API Key 后启动对话。</p>}
      {configured === null && <p className="agent-config-note">无法确认模型状态，请检查后端连接。</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {messages.length === 0 && configured && <div className="agent-suggestions">{suggestions.map((item) => <button key={item} type="button" onClick={() => void send(item)} disabled={busy}>{item}</button>)}</div>}
      <form className="agent-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <input aria-label="向 Stock Agent 提问" placeholder="询问行情、K 线或自选股…" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy || configured !== true} maxLength={2000} />
        {busy ? <button type="button" onClick={() => stream.current?.abort()}>停止</button> : <button type="submit" disabled={configured !== true || !draft.trim()} aria-label="发送消息">发送</button>}
      </form>
      <p className="agent-disclaimer">行情信息仅供参考，不构成投资建议。</p>
    </div>
  );
}
