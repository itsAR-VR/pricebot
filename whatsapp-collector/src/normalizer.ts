import {
  BufferJSON,
  WAMessage,
  WASocket,
  extractMessageContent,
  getContentType,
  jidNormalizedUser,
} from "@adiwajshing/baileys";
import { ChatMetadataCache } from "./chat-metadata.js";
import type { AppConfig } from "./config.js";
import type { Logger } from "./logger.js";
import type { WhatsAppMessageIn } from "./types.js";

const SKIPPED_CONTENT_TYPES = new Set([
  "protocolMessage",
  "reactionMessage",
  "senderKeyDistributionMessage",
  "pollCreationMessage",
  "pollUpdateMessage",
  "ciphertextMessage",
  "stickerMessage",
  "viewOnceMessage",
  "viewOnceMessageV2",
  "viewOnceMessageV2Extension",
]);

interface MessageNormalizerDeps {
  clientId: AppConfig["clientId"];
  logger: Logger;
  metadataCache: ChatMetadataCache;
  socketRef: () => WASocket | null;
}

export interface NormalizedMessage {
  original: WAMessage;
  payload: WhatsAppMessageIn;
}

export class MessageNormalizer {
  private readonly clientId: string;
  private readonly logger: Logger;
  private readonly metadataCache: ChatMetadataCache;
  private readonly socketRef: () => WASocket | null;

  constructor({ clientId, logger, metadataCache, socketRef }: MessageNormalizerDeps) {
    this.clientId = clientId;
    this.logger = logger;
    this.metadataCache = metadataCache;
    this.socketRef = socketRef;
  }

  async normalize(messages: WAMessage[]): Promise<NormalizedMessage[]> {
    const normalized: NormalizedMessage[] = [];

    for (const message of messages) {
      try {
        const payload = await this.normalizeSingle(message);
        if (payload) {
          normalized.push({ original: message, payload });
        }
      } catch (error) {
        this.logger.warn({ error }, "Failed to normalise WhatsApp message");
      }
    }

    return normalized;
  }

  private async normalizeSingle(message: WAMessage): Promise<WhatsAppMessageIn | null> {
    const remoteJid = message.key.remoteJid;
    if (!remoteJid) {
      return null;
    }

    if (message.messageStubType) {
      return null;
    }

    if (
      message.message?.viewOnceMessage ||
      message.message?.viewOnceMessageV2 ||
      message.message?.viewOnceMessageV2Extension
    ) {
      return null;
    }

    const content = extractMessageContent(message.message);
    if (!content) {
      return null;
    }

    const type = getContentType(content);
    if (!type || SKIPPED_CONTENT_TYPES.has(type)) {
      return null;
    }

    const chatInfo = await this.metadataCache.get(remoteJid);
    const senderInfo = this.extractSenderInfo(message, chatInfo.type);
    const timestamps = this.extractTimestamps(message);

    const chatTitle =
      chatInfo.type === "direct"
        ? chatInfo.title ?? senderInfo.name ?? senderInfo.phone
        : chatInfo.title;

    const normalisedMessage: WhatsAppMessageIn = {
      client_id: this.clientId,
      message_id: message.key.id ?? this.generateFallbackMessageId(),
      chat_id: remoteJid,
      chat_title: chatTitle,
      chat_type: chatInfo.type,
      sender_name: senderInfo.name,
      sender_phone: senderInfo.phone,
      observed_at: new Date().toISOString(),
      message_timestamp: timestamps.messageTimestampIso,
      platform_id: remoteJid,
      text: this.extractText(content, type),
      raw_payload: JSON.parse(JSON.stringify(message, BufferJSON.replacer)),
    };

    const media = this.extractMediaMetadata(content, type);
    if (media) {
      normalisedMessage.media = media;
      if (!normalisedMessage.text && media.caption) {
        normalisedMessage.text = media.caption;
      }
      if (!normalisedMessage.text) {
        const label = media.kind ?? media.mimetype ?? "media";
        normalisedMessage.text = `[${label.split("/")[0]}]`;
      }
    }

    return normalisedMessage;
  }

  private extractSenderInfo(
    message: WAMessage,
    chatType: "group" | "direct",
  ): { name: string | null; phone: string | null } {
    const socket = this.socketRef();
    const pushName = message.pushName ?? null;

    if (chatType === "group") {
      const participant = message.key.participant ?? undefined;
      const phone = participant ? normalisePhone(participant) : null;
      return { name: pushName, phone };
    }

    if (message.key.fromMe) {
      const userId = socket?.user?.id ?? null;
      const phone = userId ? normalisePhone(userId) : null;
      return {
        name: socket?.user?.name ?? pushName,
        phone,
      };
    }

    const remoteJid = message.key.remoteJid ?? "";
    return {
      name: pushName,
      phone: remoteJid ? normalisePhone(remoteJid) : null,
    };
  }

  private extractTimestamps(message: WAMessage) {
    const timestampSeconds = safeNumber(message.messageTimestamp) ?? Date.now() / 1000;
    const messageTimestampIso = new Date(timestampSeconds * 1000).toISOString();
    return { messageTimestampIso };
  }

  private extractText(content: Record<string, any>, type: string): string | null {
    switch (type) {
      case "conversation":
        return typeof content.conversation === "string" ? content.conversation : null;
      case "extendedTextMessage":
        return typeof content.extendedTextMessage?.text === "string"
          ? (content.extendedTextMessage.text as string)
          : null;
      case "imageMessage":
        return typeof content.imageMessage?.caption === "string"
          ? (content.imageMessage.caption as string)
          : null;
      case "videoMessage":
        return typeof content.videoMessage?.caption === "string"
          ? (content.videoMessage.caption as string)
          : null;
      case "documentMessage":
        return typeof content.documentMessage?.caption === "string"
          ? (content.documentMessage.caption as string)
          : null;
      case "buttonsResponseMessage":
        return typeof content.buttonsResponseMessage?.selectedDisplayText === "string"
          ? (content.buttonsResponseMessage.selectedDisplayText as string)
          : null;
      case "listResponseMessage":
        return typeof content.listResponseMessage?.title === "string"
          ? (content.listResponseMessage.title as string)
          : null;
      case "templateButtonReplyMessage":
        return typeof content.templateButtonReplyMessage?.selectedDisplayText === "string"
          ? (content.templateButtonReplyMessage.selectedDisplayText as string)
          : null;
      case "interactiveResponseMessage":
        return typeof content.interactiveResponseMessage?.nativeFlowResponseMessage?.paramsJson ===
          "string"
          ? (content.interactiveResponseMessage.nativeFlowResponseMessage.paramsJson as string)
          : null;
      case "audioMessage":
        return null;
      default:
        return null;
    }
  }

  private extractMediaMetadata(content: Record<string, any>, type: string) {
    if (type === "imageMessage") {
      const image = content.imageMessage;
      return {
        kind: "image",
        mimetype: image?.mimetype ?? "image/jpeg",
        filename: image?.fileName ?? undefined,
        size_bytes: safeNumber(image?.fileLength) ?? undefined,
        caption: typeof image?.caption === "string" ? (image.caption as string) : undefined,
      };
    }

    if (type === "videoMessage") {
      const video = content.videoMessage;
      return {
        kind: "video",
        mimetype: video?.mimetype ?? "video/mp4",
        filename: video?.fileName ?? undefined,
        size_bytes: safeNumber(video?.fileLength) ?? undefined,
        caption: typeof video?.caption === "string" ? (video.caption as string) : undefined,
      };
    }

    if (type === "documentMessage") {
      const document = content.documentMessage;
      return {
        kind: "document",
        mimetype: document?.mimetype ?? "application/octet-stream",
        filename: document?.fileName ?? undefined,
        size_bytes: safeNumber(document?.fileLength) ?? undefined,
        caption: typeof document?.caption === "string"
          ? (document.caption as string)
          : undefined,
      };
    }

    return undefined;
  }

  private generateFallbackMessageId(): string {
    return `temp-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  }
}

function normalisePhone(jid: string): string | null {
  const normalised = jidNormalizedUser(jid);
  if (!normalised) {
    return null;
  }

  const base = normalised.replace(/(@s\.whatsapp\.net|@whatsapp\.net)$/i, "");
  return base ? `+${base}` : null;
}

function safeNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "bigint") {
    return Number(value);
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  if (value && typeof value === "object") {
    // Handle Long from protobuf
    if ("low" in (value as any) && "high" in (value as any)) {
      const long = value as { low: number; high: number; unsigned?: boolean };
      return long.high * 2 ** 32 + (long.low >>> 0);
    }
  }
  return null;
}
