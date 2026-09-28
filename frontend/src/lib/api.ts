import type { DataEnvelope } from "./types";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string, public sessionId: string | null = null) {
    super(message);
  }
}

function statusMessage(status: number): string {
  const messages: Record<number, string> = {
    404: "没有找到对应数据。", 422: "输入格式不正确，请检查后重试。",
    503: "行情数据源暂不可用，请稍后重试。", 502: "模型请求失败，请稍后重试。",
    504: "行情数据源响应超时，请稍后重试。",
  };
  return messages[status] || `请求失败（HTTP ${status}）。`;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { cache: "no-store", ...init });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法连接后端服务，请确认 API 已启动。");
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      statusMessage(response.status),
      response.headers.get("X-Agent-Session-ID"),
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type ChatStreamEvent =
  | { event: "session"; session_id: string }
  | { event: "delta"; text: string }
  | { event: "done"; session_id: string; answer: string };

export async function streamChat(
  message: string, sessionId: string | null, signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/agent/chat/stream`, {
      method: "POST", cache: "no-store", signal,
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError(0, "无法连接后端服务，请确认 API 已启动。");
  }
  if (!response.ok) throw new ApiError(response.status, statusMessage(response.status));
  if (!response.body) throw new Error("当前连接无法接收流式回复。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let activeSession = sessionId;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const lines = frame.split(/\r?\n/);
        const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
        const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === "session") activeSession = payload.session_id;
        if (event === "error") throw new ApiError(payload.status, statusMessage(payload.status), payload.session_id || activeSession);
        if (event === "session" || event === "delta" || event === "done") {
          onEvent({ ...payload, event } as ChatStreamEvent);
          if (event === "done") completed = true;
        }
      }
      if (done) break;
    }
    if (!completed) throw new ApiError(502, "回复连接已中断，未完成的内容未保存。请重试。", activeSession);
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function getData<T>(path: string, signal?: AbortSignal): Promise<DataEnvelope<T>> {
  return apiRequest<DataEnvelope<T>>(path, { signal });
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}
