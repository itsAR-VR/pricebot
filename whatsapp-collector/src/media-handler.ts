import axios, { AxiosError, AxiosInstance } from "axios";
import FormData from "form-data";
import pRetry, { AbortError } from "p-retry";
import {
  WAMessage,
  downloadContentFromMessage,
  extractMessageContent,
  getContentType,
} from "@adiwajshing/baileys";

import type { AppConfig } from "./config.js";
import type { Logger } from "./logger.js";
import type { NormalizedMessage } from "./normalizer.js";
import type { WhatsAppMessageIn } from "./types.js";
import type { Metrics } from "./metrics.js";

interface MediaUploadResponse {
  request_id: string;
  status: "queued" | "deduped";
  document_id?: string;
  job_id?: string;
  size_bytes?: number;
  media_sha256?: string;
}

interface MediaHandlerOptions {
  config: AppConfig;
  logger: Logger;
  metrics?: Metrics;
}

function resolveMediaUrl(ingestUrl: string): string {
  const base = new URL(ingestUrl);
  // Replace the last segment (e.g. /ingest) with /media
  return new URL("./media", base).toString();
}

function determineUploadKind(message: WhatsAppMessageIn): string {
  return (
    message.media?.kind ||
    (message.media?.mimetype ? message.media.mimetype.split("/", 1)[0] : "media")
  );
}

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

const parseRetryAfter = (value: unknown): number | undefined => {
  if (value == null) {
    return undefined;
  }
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return value * 1000;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    const numeric = Number(trimmed);
    if (!Number.isNaN(numeric) && numeric > 0) {
      return numeric * 1000;
    }
    const parsed = Date.parse(trimmed);
    if (!Number.isNaN(parsed)) {
      const delta = parsed - Date.now();
      return delta > 0 ? delta : undefined;
    }
  }
  return undefined;
};

export class CollectorMediaUploadError extends Error {
  readonly reason: string;
  readonly status?: number;
  readonly retryable: boolean;
  readonly retryAfter?: number;
  counted = false;

  constructor(
    message: string,
    options: { reason: string; status?: number; retryable?: boolean; retryAfter?: number },
  ) {
    super(message);
    this.name = "CollectorMediaUploadError";
    this.reason = options.reason;
    this.status = options.status;
    this.retryable = options.retryable ?? true;
    this.retryAfter = options.retryAfter;
  }
}

export class MediaHandler {
  private readonly axios: AxiosInstance;
  private readonly logger: Logger;
  private readonly mediaUrl: string;
  private readonly token: string;
  private readonly clientId: string;
  private readonly metrics?: Metrics;

  constructor({ config, logger, metrics }: MediaHandlerOptions) {
    this.logger = logger;
    this.mediaUrl = resolveMediaUrl(config.whatsappIngestUrl);
    this.token = config.whatsappIngestToken;
    this.clientId = config.clientId;
    this.metrics = metrics;
    this.axios = axios.create({
      timeout: 20000,
      headers: {
        "User-Agent": "pricebot-whatsapp-collector/0.1.0",
      },
    });
  }

  async attachDocuments(messages: NormalizedMessage[]): Promise<WhatsAppMessageIn[]> {
    const prepared: WhatsAppMessageIn[] = [];
    for (const entry of messages) {
      const payload = entry.payload;
      if (payload.media && !payload.media.document_id) {
        try {
          const documentId = await this.uploadMedia(payload, entry.original);
          if (documentId) {
            payload.media.document_id = documentId;
          }
        } catch (error) {
          const uploadError = this.ensureUploadError(error);
          if (!uploadError.counted) {
            this.metrics?.recordMediaUpload("failed", uploadError.reason);
            uploadError.counted = true;
          }
          this.logger.warn(
            {
              messageId: payload.message_id,
              chatId: payload.chat_id,
              reason: uploadError.reason,
              status: uploadError.status,
            },
            "Failed to upload WhatsApp media attachment",
          );
          if (payload.media) {
            delete payload.media.document_id;
            payload.media.failure_reason = uploadError.reason;
          }
          payload.raw_payload = {
            ...(payload.raw_payload as Record<string, unknown> | undefined),
            media_upload_error: uploadError.message,
            media_upload_reason: uploadError.reason,
            ...(uploadError.status ? { media_upload_status: uploadError.status } : {}),
          };
        }
      }
      prepared.push(payload);
    }
    return prepared;
  }

  private async uploadMedia(payload: WhatsAppMessageIn, original: WAMessage): Promise<string | null> {
    const buffer = await this.downloadMediaBuffer(original);
    if (!buffer?.length) {
      throw new CollectorMediaUploadError("media download returned empty buffer", {
        reason: "empty_download",
        retryable: false,
      });
    }

    if (payload.media && !payload.media.size_bytes) {
      payload.media.size_bytes = buffer.length;
    }

    const { mimetype, filename, caption } = payload.media ?? {};
    const kind = determineUploadKind(payload);
    const safeFilename =
      filename || `${payload.message_id ?? `media_${Date.now()}`}.${kind === "image" ? "jpg" : "bin"}`;

    const buildFormData = () => {
      const form = new FormData();
      form.append("client_id", this.clientId);
      form.append("chat_title", payload.chat_title ?? "WhatsApp Chat");
      if (payload.platform_id) {
        form.append("chat_platform_id", payload.platform_id);
      }
      if (payload.message_id) {
        form.append("message_id", payload.message_id);
      }
      if (payload.sender_name) {
        form.append("sender_name", payload.sender_name);
      }
      if (payload.sender_phone) {
        form.append("sender_phone", payload.sender_phone);
      }
      if (payload.observed_at) {
        form.append("observed_at", payload.observed_at);
      }
      form.append("media_kind", kind);
      if (caption) {
        form.append("caption", caption);
      }
      if (payload.media?.size_bytes) {
        form.append("size_bytes", String(payload.media.size_bytes));
      }
      if (mimetype) {
        form.append("mimetype", mimetype);
      }
      form.append("file", buffer, {
        filename: safeFilename,
        contentType: mimetype ?? "application/octet-stream",
      });
      return form;
    };

    const attempt = async () => {
      const form = buildFormData();
      try {
        const response = await this.axios.post<MediaUploadResponse>(this.mediaUrl, form, {
          headers: {
            ...form.getHeaders(),
            "X-Ingest-Token": this.token,
          },
          maxBodyLength: Infinity,
        });
        const data = response.data;
        if (!data?.document_id) {
          throw new CollectorMediaUploadError("media upload response missing document_id", {
            reason: "invalid_response",
            status: response.status,
            retryable: true,
          });
        }
        return data;
      } catch (error) {
        const uploadError = this.normalizeUploadError(error);
        if (!uploadError.retryable) {
          throw new AbortError(uploadError);
        }
        if (uploadError.retryAfter && uploadError.retryAfter > 0) {
          await sleep(uploadError.retryAfter);
        }
        throw uploadError;
      }
    };

    let result: MediaUploadResponse;
    try {
      result = await pRetry(attempt, {
        retries: 2,
        factor: 2,
        minTimeout: 500,
        maxTimeout: 4000,
        onFailedAttempt: (error) => {
          const uploadError = this.ensureUploadError(error);
          this.logger.warn(
            {
              attempt: error.attemptNumber,
              retriesLeft: error.retriesLeft,
              chatId: payload.chat_id,
              messageId: payload.message_id,
              reason: uploadError.reason,
              status: uploadError.status,
            },
            "Retrying WhatsApp media upload after failure",
          );
        },
      });
    } catch (error) {
      const finalError = error instanceof AbortError ? error.originalError : error;
      const uploadError = this.ensureUploadError(finalError);
      if (!uploadError.counted) {
        this.metrics?.recordMediaUpload("failed", uploadError.reason);
        uploadError.counted = true;
      }
      throw uploadError;
    }

    const status = result.status;
    this.metrics?.recordMediaUpload(status);
    this.logger.debug(
      {
        status,
        documentId: result.document_id,
        chatId: payload.chat_id,
        messageId: payload.message_id,
      },
      "Uploaded WhatsApp media attachment",
    );

    if (payload.media) {
      delete payload.media.failure_reason;
    }

    return result.document_id;
  }

  private normalizeUploadError(error: unknown): CollectorMediaUploadError {
    if (error instanceof CollectorMediaUploadError) {
      return error;
    }
    if (error instanceof AbortError && error.originalError) {
      return this.normalizeUploadError(error.originalError);
    }
    if (axios.isAxiosError(error)) {
      const axiosError = error as AxiosError;
      const status = axiosError.response?.status;
      const retryAfterHeader =
        (axiosError.response?.headers?.["retry-after"] ??
          axiosError.response?.headers?.["Retry-After"]) ?? undefined;
      const retryAfter = parseRetryAfter(retryAfterHeader as string | number | undefined);
      if (status === 413) {
        return new CollectorMediaUploadError("media rejected: payload too large", {
          reason: "too_large",
          status,
          retryable: false,
        });
      }
      if (status === 415) {
        return new CollectorMediaUploadError("media rejected: unsupported type", {
          reason: "unsupported_type",
          status,
          retryable: false,
        });
      }
      if (status === 429) {
        return new CollectorMediaUploadError("media upload rate limited", {
          reason: "rate_limited",
          status,
          retryAfter,
          retryable: true,
        });
      }
      if (status && status >= 500) {
        return new CollectorMediaUploadError(`media upload server error (${status})`, {
          reason: "server_error",
          status,
          retryable: true,
        });
      }
      if (status && status >= 400) {
        return new CollectorMediaUploadError(`media upload rejected (${status})`, {
          reason: "client_error",
          status,
          retryable: false,
        });
      }
      if (axiosError.code === "ECONNABORTED") {
        return new CollectorMediaUploadError("media upload timed out", {
          reason: "timeout",
          retryable: true,
        });
      }
      return new CollectorMediaUploadError(
        axiosError.message || "network error during media upload",
        {
          reason: "network_error",
          retryable: true,
        },
      );
    }
    if (error instanceof Error) {
      return new CollectorMediaUploadError(error.message, {
        reason: "unexpected_error",
        retryable: false,
      });
    }
    return new CollectorMediaUploadError(String(error), {
      reason: "unexpected_error",
      retryable: false,
    });
  }

  private ensureUploadError(error: unknown): CollectorMediaUploadError {
    if (error instanceof CollectorMediaUploadError) {
      return error;
    }
    if (error instanceof AbortError && error.originalError) {
      return this.ensureUploadError(error.originalError);
    }
    return this.normalizeUploadError(error);
  }

  private async downloadMediaBuffer(message: WAMessage): Promise<Buffer> {
    const content = extractMessageContent(message.message);
    if (!content) {
      throw new Error("no content to download");
    }
    const type = getContentType(content);
    if (!type) {
      throw new Error("unknown media content type");
    }

    const streamType = this.mapStreamType(type);
    const stream = await downloadContentFromMessage(
      // @ts-expect-error dynamic access based on type
      content[type],
      streamType,
    );

    const chunks: Buffer[] = [];
    for await (const chunk of stream) {
      chunks.push(chunk as Buffer);
    }
    return Buffer.concat(chunks);
  }

  private mapStreamType(type: string): "image" | "video" | "audio" | "document" {
    if (type === "imageMessage") {
      return "image";
    }
    if (type === "videoMessage") {
      return "video";
    }
    if (type === "audioMessage") {
      return "audio";
    }
    return "document";
  }
}
