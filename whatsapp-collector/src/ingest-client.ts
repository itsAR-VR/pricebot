import axios, { AxiosInstance } from "axios";
import pRetry from "p-retry";
import type { AppConfig } from "./config.js";
import type { Logger } from "./logger.js";
import type { IngestResponse, WhatsAppMessageIn } from "./types.js";

interface IngestClientOptions {
  config: AppConfig;
  logger: Logger;
}

export class IngestClient {
  private readonly axios: AxiosInstance;
  private readonly logger: Logger;
  private readonly ingestUrl: string;
  private readonly token: string;

  constructor({ config, logger }: IngestClientOptions) {
    this.logger = logger;
    this.ingestUrl = config.whatsappIngestUrl;
    this.token = config.whatsappIngestToken;
    this.axios = axios.create({
      timeout: 10_000,
      headers: {
        "Content-Type": "application/json",
        "User-Agent": "pricebot-whatsapp-collector/0.1.0",
      },
    });
  }

  async sendBatch(messages: WhatsAppMessageIn[]): Promise<IngestResponse | null> {
    if (!messages.length) {
      return null;
    }

    const attempt = async () => {
      const response = await this.axios.post<IngestResponse>(
        this.ingestUrl,
        { messages },
        {
          headers: {
            "X-Ingest-Token": this.token,
          },
        },
      );

      return response.data;
    };

    try {
      return await pRetry(attempt, {
        retries: 3,
        factor: 2,
        minTimeout: 1000,
        maxTimeout: 5000,
        onFailedAttempt: (error) => {
          this.logger.warn(
            { attemptNumber: error.attemptNumber, retriesLeft: error.retriesLeft },
            "Failed to deliver WhatsApp ingest batch, retrying",
          );
        },
      });
    } catch (error) {
      this.logger.error({ error }, "Failed to deliver WhatsApp ingest batch");
      throw error;
    }
  }
}
