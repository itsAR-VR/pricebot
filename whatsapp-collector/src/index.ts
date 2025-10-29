import { WAMessage, WASocket } from "@adiwajshing/baileys";
import { loadConfig } from "./config.js";
import { MessageBatcher } from "./batcher.js";
import { ChatMetadataCache } from "./chat-metadata.js";
import { createWhatsAppClient } from "./collector/whatsapp.js";
import { createLogger, type Logger } from "./logger.js";
import { IngestClient } from "./ingest-client.js";
import { MessageNormalizer } from "./normalizer.js";
import type { IngestResponse, WhatsAppMessageIn } from "./types.js";
import { Metrics } from "./metrics.js";
import { StatusServer } from "./server.js";
import { MediaHandler } from "./media-handler.js";

async function main() {
  const config = loadConfig();
  const logger = createLogger(config.logLevel);

  logger.info(
    {
      ingestUrl: config.whatsappIngestUrl,
      clientId: config.clientId,
      authStateDir: config.authStateDir,
    },
    "Starting WhatsApp collector",
  );

  const metrics = new Metrics();
  metrics.setConnectionStatus("connecting");

  const statusServer = new StatusServer({ port: config.port, logger, metrics });
  await statusServer.start();

  let clientRef: Awaited<ReturnType<typeof createWhatsAppClient>> | null = null;
  let batcher: MessageBatcher | null = null;

  try {
    const metadataCache = new ChatMetadataCache(logger, () => clientRef?.socket ?? null);
    const normalizer = new MessageNormalizer({
      clientId: config.clientId,
      logger,
      metadataCache,
      socketRef: () => clientRef?.socket ?? null,
    });
    const ingestClient = new IngestClient({ config, logger });
    const mediaHandler = new MediaHandler({ config, logger, metrics });

    batcher = new MessageBatcher({
      maxBatchSize: config.batchMaxMessages,
      flushIntervalMs: config.batchFlushIntervalMs,
      onFlush: async (batch) => {
        metrics.recordBatchAttempt(batch.length);
        try {
          const response = await deliverBatch(batch, ingestClient, logger);
          metrics.recordBatchSuccess(response);
        } catch (error) {
          metrics.recordBatchFailure(error, batch.length);
          logger.error({ error }, "Failed to deliver WhatsApp ingest batch");
          batcher?.add(batch);
        }
      },
    });

    const client = await createWhatsAppClient({
      config,
      logger,
      onMessages: handleIncomingMessages(logger, normalizer, mediaHandler, batcher!),
      onConnectionStateChange: (state) => {
        if (state.connection === "open") {
          metrics.setConnectionStatus("open");
          metrics.setReady(true);
        } else if (state.connection === "close") {
          metrics.setConnectionStatus("closed");
          metrics.setReady(false);
        } else if (state.connection === "connecting") {
          metrics.setConnectionStatus("connecting");
          metrics.setReady(false);
        }
      },
    });

    clientRef = client;

    setupSignalHandlers(logger, client, batcher!, statusServer);
  } catch (error) {
    await statusServer.stop();
    if (batcher) {
      await batcher.shutdown();
    }
    if (clientRef) {
      await clientRef.shutdown();
    }
    throw error;
  }
}

function handleIncomingMessages(
  logger: Logger,
  normalizer: MessageNormalizer,
  mediaHandler: MediaHandler,
  batcher: MessageBatcher,
) {
  return async (messages: WAMessage[], _socket: WASocket) => {
    const normalised = await normalizer.normalize(messages);
    if (!normalised.length) {
      return;
    }

    const prepared = await mediaHandler.attachDocuments(normalised);
    if (!prepared.length) {
      return;
    }

    logger.debug({ count: prepared.length }, "Enqueuing normalised WhatsApp messages");
    batcher.add(prepared);
  };
}

async function deliverBatch(
  batch: WhatsAppMessageIn[],
  ingestClient: IngestClient,
  logger: Logger,
): Promise<IngestResponse | null> {
  const response = await ingestClient.sendBatch(batch);
  logger.info(
    {
      messageCount: batch.length,
      statuses: response?.results ?? [],
    },
    "Delivered WhatsApp ingest batch",
  );
  return response;
}

function setupSignalHandlers(
  logger: Logger,
  client: Awaited<ReturnType<typeof createWhatsAppClient>>,
  batcher: MessageBatcher,
  statusServer: StatusServer,
) {
  let shuttingDown = false;

  const shutdown = async (signal: string) => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;

    logger.info({ signal }, "Shutting down WhatsApp collector");
    await batcher.shutdown();
    await client.shutdown();
    await statusServer.stop();
    process.exit(0);
  };

  process.once("SIGINT", () => void shutdown("SIGINT"));
  process.once("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("unhandledRejection", (error) => {
    logger.error({ error }, "Unhandled promise rejection");
    void shutdown("unhandledRejection");
  });
  process.on("uncaughtException", (error) => {
    logger.error({ error }, "Uncaught exception");
    void shutdown("uncaughtException");
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
