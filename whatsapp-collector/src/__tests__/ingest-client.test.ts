import { afterEach, beforeEach, describe, expect, jest, test } from "@jest/globals";

import { __axiosMocks } from "axios";
import { __pRetryMock } from "p-retry";
import { IngestClient } from "../ingest-client.js";
import type { AppConfig } from "../config.js";
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

function makeLogger() {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
  };
}

const sampleMessage: WhatsAppMessageIn = {
  client_id: "collector-1",
  message_id: "m-1",
  chat_id: "123@g.us",
  chat_title: "Deals",
  chat_type: "group",
  sender_name: "Alice",
  sender_phone: "+15550000000",
  observed_at: new Date().toISOString(),
  message_timestamp: new Date().toISOString(),
  platform_id: "123@g.us",
  text: "Selling GPUs - $400",
  raw_payload: {},
};

describe("IngestClient", () => {
  beforeEach(() => {
    mockAxiosPost.mockReset();
    mockAxiosCreate.mockReset().mockImplementation(() => ({ post: mockAxiosPost }));
    mockPRetry.mockReset().mockImplementation(async (fn: () => unknown) => fn());
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("returns null when batch is empty", async () => {
    const client = new IngestClient({ config: makeConfig(), logger: makeLogger() });
    const result = await client.sendBatch([]);
    expect(result).toBeNull();
    expect(mockAxiosPost).not.toHaveBeenCalled();
  });

  test("sends batch with retry wrapper and returns response payload", async () => {
    const logger = makeLogger();
    const responseData = { results: [{ message_id: "m-1", status: "created" as const }] };
    mockAxiosPost.mockResolvedValue({ data: responseData });

    const client = new IngestClient({ config: makeConfig(), logger });
    const result = await client.sendBatch([sampleMessage]);

    expect(mockPRetry).toHaveBeenCalledTimes(1);
    expect(mockAxiosCreate).toHaveBeenCalledTimes(1);
    expect(mockAxiosPost).toHaveBeenCalledWith(
      "https://api.pricebot.test/integrations/whatsapp/ingest",
      { messages: [sampleMessage] },
      {
        headers: { "X-Ingest-Token": "test-token" },
      },
    );
    expect(result).toEqual(responseData);
    expect(logger.warn).not.toHaveBeenCalled();
    expect(logger.error).not.toHaveBeenCalled();
  });

  test("logs and rethrows when delivery fails after retries", async () => {
    const logger = makeLogger();
    const failure = new Error("network down");
    mockAxiosPost.mockRejectedValue(failure);
    mockPRetry.mockImplementation(async (fn, options) => {
      try {
        return await fn();
      } catch (error) {
        options?.onFailedAttempt?.({
          attemptNumber: 1,
          retriesLeft: 0,
          message: (error as Error).message,
          stack: (error as Error).stack,
          cause: error,
        } as any);
        throw error;
      }
    });

    const client = new IngestClient({ config: makeConfig(), logger });

    await expect(client.sendBatch([sampleMessage])).rejects.toThrow("network down");
    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(logger.error).toHaveBeenCalledWith({ error: failure }, expect.any(String));
  });
});
