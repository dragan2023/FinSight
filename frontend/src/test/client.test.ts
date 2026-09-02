import axios, { AxiosError } from "axios";
import { describe, expect, it } from "vitest";
import { normalizeError } from "../api/client";

describe("safe API errors", () => {
  it("preserves category, retryable and request ID but not axios payloads", () => {
    const error = new AxiosError(
      "contains-private-value",
      "ERR_BAD_RESPONSE",
      undefined,
      undefined,
      {
        data: {
          error: {
            message: "模型限流",
            category: "model_rate_limited",
            request_id: "local-id",
            retryable: true,
          },
        },
        status: 429,
        statusText: "Too Many Requests",
        headers: {},
        config: { headers: new axios.AxiosHeaders() },
      },
    );
    const result = normalizeError(error);
    expect(result.message).toBe("模型限流");
    expect(result.category).toBe("model_rate_limited");
    expect(result.request_id).toBe("local-id");
    expect(result.retryable).toBe(true);
    expect(JSON.stringify(result)).not.toContain("contains-private-value");
  });
  it("does not expose unexpected exception text", () => {
    expect(normalizeError(new Error("private-value")).message).not.toContain(
      "private-value",
    );
  });
});
