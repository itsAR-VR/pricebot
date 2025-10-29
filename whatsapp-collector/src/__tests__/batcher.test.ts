import {
  describe,
  expect,
  beforeEach,
  afterEach,
  test,
  jest,
} from "@jest/globals";

import { MessageBatcher } from "../batcher.js";
import type { WhatsAppMessageIn } from "../types.js";

function makeMessage(overrides: Partial<WhatsAppMessageIn> = {}): WhatsAppMessageIn {
  const timestamp = new Date().toISOString();
  return {
    client_id: "collector",
    message_id: overrides.message_id ?? `msg-${Math.random().toString(16).slice(2)}`,
    chat_id: "123@g.us",
    chat_title: "Deals Chat",
    chat_type: "group",
    sender_name: "Alice",
    sender_phone: "+15551234567",
    observed_at: timestamp,
    message_timestamp: timestamp,
    platform_id: "123@g.us",
    text: "Selling GPUs",
    raw_payload: {},
    ...overrides,
  };
}

describe("MessageBatcher", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  test("flushes immediately when max batch size is reached", async () => {
    const onFlush = jest.fn().mockResolvedValue(undefined);
    const batcher = new MessageBatcher({
      maxBatchSize: 2,
      flushIntervalMs: 10_000,
      onFlush,
    });

    const first = makeMessage({ message_id: "a" });
    const second = makeMessage({ message_id: "b" });

    batcher.add([first]);
    expect(onFlush).not.toHaveBeenCalled();

    batcher.add([second]);

    await Promise.resolve();
    await Promise.resolve();

    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith([first, second]);
  });

  test("flushes pending messages when timer fires", async () => {
    const onFlush = jest.fn().mockResolvedValue(undefined);
    const batcher = new MessageBatcher({
      maxBatchSize: 5,
      flushIntervalMs: 1000,
      onFlush,
    });

    const message = makeMessage({ message_id: "timed" });
    batcher.add([message]);

    expect(onFlush).not.toHaveBeenCalled();

    await jest.advanceTimersByTimeAsync(1000);
    await Promise.resolve();

    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith([message]);
  });

  test("shutdown flushes remaining buffered messages", async () => {
    const onFlush = jest.fn().mockResolvedValue(undefined);
    const batcher = new MessageBatcher({
      maxBatchSize: 10,
      flushIntervalMs: 5000,
      onFlush,
    });

    const buffered = [makeMessage({ message_id: "x" }), makeMessage({ message_id: "y" })];
    batcher.add(buffered);

    await batcher.shutdown();

    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith(buffered);
  });

  test("second flush call while flushing does not trigger another send", async () => {
    let resolveFlush!: () => void;
    const flushPromise = new Promise<void>((resolve) => {
      resolveFlush = resolve;
    });

    const onFlush = jest.fn().mockImplementation(async () => flushPromise);
    const batcher = new MessageBatcher({
      maxBatchSize: 2,
      flushIntervalMs: 10_000,
      onFlush,
    });

    batcher.add([makeMessage({ message_id: "p" }), makeMessage({ message_id: "q" })]);
    await Promise.resolve();

    expect(onFlush).toHaveBeenCalledTimes(1);

    await batcher.flush();
    expect(onFlush).toHaveBeenCalledTimes(1);

    resolveFlush();
    await Promise.resolve();
  });
});
