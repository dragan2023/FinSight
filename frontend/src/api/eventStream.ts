import { createParser } from "eventsource-parser";
import { ApiError, getSession, normalizeError } from "./client";

/**
 * Shared SSE reader for this app's own event streams (stage 01: model chat
 * streaming; stage 03 reuses it for research task progress). Parsing uses the
 * locked eventsource-parser dependency; this file contains no vendor-specific
 * logic. Research progress streams are GET with a session header; model chat
 * stays POST. Heartbeat comment lines never reach onEvent.
 */
export interface StreamHandlers<T> {
  signal?: AbortSignal;
  onEvent: (event: T) => void;
}

async function readSseResponse<T>(
  response: Response,
  { signal, onEvent }: StreamHandlers<T>,
): Promise<void> {
  if (!response.body) {
    throw new ApiError("事件流未能建立，请稍后重试。", "stream", null, true);
  }
  const parser = createParser({
    onEvent: (event) => {
      if (event.data && event.data !== "[DONE]") {
        onEvent(JSON.parse(event.data) as T);
      }
    },
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
  } catch (error: unknown) {
    if (signal?.aborted) return;
    throw normalizeError(error);
  } finally {
    reader.releaseLock();
  }
}

async function openEventStream(
  method: "GET" | "POST",
  url: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-FinSight-Client": "web",
        "X-FinSight-Session": await getSession(),
      },
      ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
      signal,
    });
  } catch (error: unknown) {
    if (signal?.aborted) throw new ApiError("事件流已取消。", "stream", null, false);
    throw normalizeError(error);
  }
  if (!response.ok) {
    // The security and validation middlewares answer with the shared error envelope.
    const text = await response.text();
    let parsed: { error?: Record<string, unknown> } | null;
    try {
      parsed = JSON.parse(text) as { error?: Record<string, unknown> };
    } catch {
      parsed = null;
    }
    const detail = parsed?.error;
    if (detail) {
      throw new ApiError(
        typeof detail.message === "string" ? detail.message : "请求未完成。",
        typeof detail.category === "string" ? detail.category : "unknown",
        typeof detail.request_id === "string" ? detail.request_id : null,
        detail.retryable === true,
      );
    }
    throw new ApiError("事件流未能建立，请稍后重试。", "stream", null, true);
  }
  return response;
}

export async function postEventStream<T>(
  url: string,
  body: unknown,
  handlers: StreamHandlers<T>,
): Promise<void> {
  const response = await openEventStream("POST", url, body, handlers.signal);
  await readSseResponse(response, handlers);
}

export async function getEventStream<T>(
  url: string,
  handlers: StreamHandlers<T>,
): Promise<void> {
  const response = await openEventStream("GET", url, null, handlers.signal);
  await readSseResponse(response, handlers);
}
