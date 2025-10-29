import { afterEach, beforeEach, describe, expect, jest, test } from "@jest/globals";

import { __axiosMocks } from "axios";
import { __pRetryMock } from "p-retry";
import { MediaHandler } from "../media-handler.js";
import type { AppConfig } from "../config.js";
import type { Logger } from "../logger.js";
import type { Metrics } from "../metrics.js";
import type { NormalizedMessage } from "../normalizer.js";
import type { WhatsAppMessageIn } from "../types.js";

const mockAxiosCreate = __axiosMocks.create;
const mockAxiosPost = __axiosMocks.post;
const mockPRetry = __pRetryMock;

function makeConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  return {
    whatsappIngestUrl: "https://api.pricebot.test/integrations/whatsapp/ingest",
    whatsappIngestToken: "test-token",
    clientId: "collector-1",
    authStateDir: "./auth-state",
    batchMaxMessages: 50,
    batchFlushIntervalMs: 1500,
    logLevel: "info",
    port: 8080,
    ...overrides,
  };
}

function makeLogger(): Logger {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
  };
}

function makeMetrics(): Metrics {
  return {
    recordBatchAttempt: jest.fn(),
    recordBatchFailure: jest.fn(),
    recordBatchSuccess: jest.fn(),
    recordMediaUpload: jest.fn(),
    setConnectionStatus: jest.fn(),
    setReady: jest.fn(),
    snapshot: jest.fn(),
  } as unknown as Metrics;
}

function buildMessage(): NormalizedMessage {
  const payload: WhatsAppMessageIn = {
    client_id: "collector-1",
    message_id: "msg-1",
    chat_id: "12345@g.us",
    chat_title: "Deals",
    chat_type: "group",
    sender_name: "Alice",
    sender_phone: "+15555550000",
    observed_at: new Date().toISOString(),
    message_timestamp: new Date().toISOString(),
    platform_id: "12345@g.us",
    text: "Photo incoming",
    raw_payload: {},
    media: {
      mimetype: "image/jpeg",
      kind: "image",
    },
  };
  return {
    original: { message: {} } as any,
    payload,
  };
}

function axiosError(status: number, retryAfter?: string | number) {
  return {
    isAxiosError: true,
    message: `status ${status}`,
    response: {
      status,
      headers: retryAfter != null ? { "retry-after": retryAfter } : {},
    },
  };
}

describe("MediaHandler", () => {
  beforeEach(() => {
    mockAxiosPost.mockReset();
    mockAxiosCreate.mockReset().mockImplementation(() => ({ post: mockAxiosPost }));
    mockPRetry.mockReset().mockImplementation(async (fn) => fn());
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("marks payload when media upload is rejected as unsupported", async () => {
    const logger = makeLogger();
    const metrics = makeMetrics();
    const recordMediaUpload = metrics.recordMediaUpload as jest.Mock;
    mockAxiosPost.mockRejectedValue(axiosError(415));

    const handler = new MediaHandler({
      config: makeConfig(),
      logger,
      metrics,
    });
    (handler as any).downloadMediaBuffer = jest.fn().mockResolvedValue(Buffer.from("hello"));

    const message = buildMessage();
    const [payload] = await handler.attachDocuments([message]);

    expect(mockAxiosPost).toHaveBeenCalledTimes(1);
    expect(recordMediaUpload).toHaveBeenCalledWith("failed", "unsupported_type");
    expect(logger.warn).toHaveBeenCalled();
    expect(payload.media?.document_id).toBeUndefined();
    expect(payload.media?.failure_reason).toBe("unsupported_type");
    expect(payload.raw_payload).toMatchObject({
      media_upload_reason: "unsupported_type",
    });
  });

  test("retries media upload on rate limit and succeeds", async () => {
    const logger = makeLogger();
    const metrics = makeMetrics();
    const recordMediaUpload = metrics.recordMediaUpload as jest.Mock;
    const rateLimitError = axiosError(429, "0");
    mockAxiosPost
      .mockRejectedValueOnce(rateLimitError)
      .mockResolvedValueOnce({
        data: {
          request_id: "req-1",
          status: "queued",
          document_id: "doc-1",
        },
      });

    mockPRetry.mockImplementation(async (fn, options) => {
      try {
        return await fn();
      } catch (error) {
        const attemptError = error as any;
        attemptError.attemptNumber = 1;
        attemptError.retriesLeft = 1;
        options?.onFailedAttempt?.(attemptError);
        return fn();
      }
    });

    const handler = new MediaHandler({
      config: makeConfig(),
      logger,
      metrics,
    });
    (handler as any).downloadMediaBuffer = jest.fn().mockResolvedValue(Buffer.from("hello"));

    const message = buildMessage();
    message.payload.media!.failure_reason = "previous_error";

    const [payload] = await handler.attachDocuments([message]);

    expect(mockAxiosPost).toHaveBeenCalledTimes(2);
    expect(recordMediaUpload).toHaveBeenCalledWith("queued");
    expect(recordMediaUpload).not.toHaveBeenCalledWith("failed", expect.anything());
    expect(payload.media?.document_id).toBe("doc-1");
    expect(payload.media?.failure_reason).toBeUndefined();
    expect(payload.raw_payload).not.toHaveProperty("media_upload_error");
  });
});
