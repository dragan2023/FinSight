import axios from "axios";
import type { AxiosRequestConfig } from "axios";

export class ApiError extends Error {
  readonly category: string;
  readonly request_id: string | null;
  readonly retryable: boolean;

  constructor(
    message: string,
    category: string,
    requestId: string | null,
    retryable: boolean,
  ) {
    super(message);
    this.name = "ApiError";
    this.category = category;
    this.request_id = requestId;
    this.retryable = retryable;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (axios.isAxiosError<unknown>(error)) {
    const body = error.response?.data;
    if (record(body) && record(body.error)) {
      const detail = body.error;
      return new ApiError(
        typeof detail.message === "string" ? detail.message : "请求未完成。",
        typeof detail.category === "string" ? detail.category : "unknown",
        typeof detail.request_id === "string" ? detail.request_id : null,
        detail.retryable === true,
      );
    }
  }
  return new ApiError(
    "无法连接本机服务，请确认后端已启动。",
    "network",
    null,
    true,
  );
}

const client = axios.create({ baseURL: "/api", timeout: 130_000 });
let session: Promise<string> | undefined;

export async function getSession(): Promise<string> {
  session ??= client
    .get<{ token: string }>("/system/session", {
      headers: { "X-FinSight-Client": "web" },
      timeout: 10_000,
    })
    .then((response) => response.data.token)
    .catch((error: unknown) => {
      session = undefined;
      throw normalizeError(error);
    });
  return session;
}

export async function request<T>(
  config: AxiosRequestConfig,
  isPublic = false,
): Promise<T> {
  const send = async () =>
    client.request<T>({
      ...config,
      headers: {
        "Content-Type": "application/json",
        ...(!isPublic ? { "X-FinSight-Session": await getSession() } : {}),
      },
    });
  try {
    return (await send()).data;
  } catch (error: unknown) {
    const failure = normalizeError(error);
    // The security middleware rejects an expired session before any operation starts.
    // Only that explicit rejection is safe to replay; timeouts never replay writes.
    if (!isPublic && failure.category === "session_expired") {
      session = undefined;
      try {
        return (await send()).data;
      } catch (retryError: unknown) {
        throw normalizeError(retryError);
      }
    }
    throw failure;
  }
}

/**
 * Multipart upload (stage 04 batch 4/5): the browser must set the multipart
 * boundary itself, so no Content-Type header is forced here; the session
 * header and error normalization stay identical to request().
 */
export async function requestMultipart<T>(
  url: string,
  form: FormData,
): Promise<T> {
  try {
    return (
      await client.post<T>(url, form, {
        headers: { "X-FinSight-Session": await getSession() },
      })
    ).data;
  } catch (error: unknown) {
    throw normalizeError(error);
  }
}

export async function downloadFile(url: string): Promise<Blob> {
  try {
    const response = await client.get(url, {
      headers: { "X-FinSight-Session": await getSession() },
      responseType: "blob",
    });
    return response.data as Blob;
  } catch (error: unknown) {
    throw normalizeError(error);
  }
}
