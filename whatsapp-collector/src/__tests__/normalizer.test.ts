import { beforeEach, describe, expect, jest, test } from "@jest/globals";

import { __baileysMocks } from "@adiwajshing/baileys";
import { MessageNormalizer } from "../normalizer.js";
import type { ChatMetadataCache } from "../chat-metadata.js";
import type { Logger } from "../logger.js";
import type { WhatsAppMessageIn } from "../types.js";

const { extractMessageContent, getContentType, jidNormalizedUser } = __baileysMocks;

function buildLogger(): Logger {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
    trace: jest.fn?.() ?? (() => undefined),
  } as unknown as Logger;
}

function buildMessage(overrides: Record<string, unknown> = {}): any {
  return {
    key: {
      remoteJid: "15551234567@s.whatsapp.net",
      id: "ABCD",
      fromMe: false,
      participant: undefined,
    },
    pushName: "Alice",
    messageTimestamp: 1_720_000_000,
    message: {},
    ...overrides,
  };
}

describe("MessageNormalizer", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    extractMessageContent.mockReset();
    getContentType.mockReset();
    jidNormalizedUser.mockReset().mockImplementation((jid: string) => jid);
  });

  test("normalizes conversation messages", async () => {
    const logger = buildLogger();
    const metadataCache = {
      get: jest.fn().mockResolvedValue({ id: "15551234567@s.whatsapp.net", type: "direct", title: null }),
    } as unknown as ChatMetadataCache;

    extractMessageContent.mockReturnValue({ conversation: "Selling GPUs - $400" });
    getContentType.mockReturnValue("conversation");

    const normalizer = new MessageNormalizer({
      clientId: "collector-1",
      logger,
      metadataCache,
      socketRef: () => null,
    });

    const message = buildMessage();
    const result = await normalizer.normalize([message]);
    expect(extractMessageContent).toHaveBeenCalled();
    expect(result).toHaveLength(1);
    const entry = result[0];
    const payload = entry.payload as WhatsAppMessageIn;

    expect(entry.original).toBe(message);
    expect(payload.chat_title).toBe("Alice");
    expect(payload.sender_phone).toBe("+15551234567");
    expect(payload.text).toBe("Selling GPUs - $400");
    expect(payload.message_timestamp).toBe(new Date(1_720_000_000 * 1000).toISOString());
    expect(payload.media).toBeUndefined();
    expect(typeof payload.observed_at).toBe("string");
    expect(metadataCache.get).toHaveBeenCalledWith("15551234567@s.whatsapp.net");
  });

  test("captures media metadata when present", async () => {
    const logger = buildLogger();
    const metadataCache = {
      get: jest.fn().mockResolvedValue({ id: "1203@g.us", type: "group", title: "Deals" }),
    } as unknown as ChatMetadataCache;

    extractMessageContent.mockReturnValue({
      imageMessage: {
        mimetype: "image/png",
        fileName: "offer.png",
        fileLength: "2048",
        caption: "Price list",
      },
    });
    getContentType.mockReturnValue("imageMessage");

    const normalizer = new MessageNormalizer({
      clientId: "collector-1",
      logger,
      metadataCache,
      socketRef: () => null,
    });

    const result = await normalizer.normalize([buildMessage()]);
    expect(result).toHaveLength(1);
    const { payload } = result[0];
    expect(payload.media).toEqual(
      expect.objectContaining({
        mimetype: "image/png",
        filename: "offer.png",
        size_bytes: 2048,
        caption: "Price list",
      }),
    );
    expect(payload.text).toBe("Price list");
  });

  test("skips unsupported message types", async () => {
    const logger = buildLogger();
    const metadataCache = {
      get: jest.fn(),
    } as unknown as ChatMetadataCache;

    extractMessageContent.mockReturnValue({});
    getContentType.mockReturnValue("protocolMessage");

    const normalizer = new MessageNormalizer({
      clientId: "collector-1",
      logger,
      metadataCache,
      socketRef: () => null,
    });

    const result = await normalizer.normalize([buildMessage()]);
    expect(result).toHaveLength(0);
    expect(metadataCache.get).not.toHaveBeenCalled();
  });

  test("logs and skips when normalization fails", async () => {
    const logger = buildLogger();
    const error = new Error("metadata failure");
    const metadataCache = {
      get: jest.fn().mockRejectedValue(error),
    } as unknown as ChatMetadataCache;

    extractMessageContent.mockReturnValue({ conversation: "hello" });
    getContentType.mockReturnValue("conversation");

    const normalizer = new MessageNormalizer({
      clientId: "collector-1",
      logger,
      metadataCache,
      socketRef: () => null,
    });

    const result = await normalizer.normalize([buildMessage()]);
    expect(result).toHaveLength(0);
    expect(logger.warn).toHaveBeenCalledWith({ error }, "Failed to normalise WhatsApp message");
  });
});
