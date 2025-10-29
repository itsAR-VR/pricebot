import type { IngestResponse } from "./types.js";

type ConnectionStatus = "initialising" | "connecting" | "open" | "closed";

export interface MetricsSnapshot {
  connection_status: ConnectionStatus;
  ready: boolean;
  batches_sent: number;
  batches_failed: number;
  messages_sent: number;
  ingest_created: number;
  ingest_accepted: number;
  ingest_deduped: number;
  ingest_skipped: number;
  media_uploaded: number;
  media_deduped: number;
  media_failed: number;
  media_failure_reasons: Record<string, number>;
  last_media_failure?: string;
  last_error?: string;
  last_flush_at?: string;
}

export class Metrics {
  private connectionStatus: ConnectionStatus = "initialising";
  private ready = false;
  private batchesSent = 0;
  private batchesFailed = 0;
  private messagesSent = 0;
  private ingestCreated = 0;
  private ingestAccepted = 0;
  private ingestDeduped = 0;
  private ingestSkipped = 0;
  private mediaUploaded = 0;
  private mediaDeduped = 0;
  private mediaFailed = 0;
  private mediaFailureReasons = new Map<string, number>();
  private lastMediaFailure: string | undefined;
  private lastError: string | undefined;
  private lastFlushAt: string | undefined;

  setConnectionStatus(status: ConnectionStatus) {
    this.connectionStatus = status;
    if (status === "open") {
      this.ready = true;
    }
  }

  setReady(ready: boolean) {
    this.ready = ready;
  }

  recordBatchAttempt(messageCount: number) {
    this.messagesSent += messageCount;
    this.batchesSent += 1;
    this.lastFlushAt = new Date().toISOString();
  }

  recordBatchSuccess(response: IngestResponse | null) {
    if (!response?.results?.length) {
      return;
    }

    for (const result of response.results) {
      switch (result.status) {
        case "created":
          this.ingestCreated += 1;
          break;
        case "accepted":
          this.ingestAccepted += 1;
          break;
        case "deduped":
          this.ingestDeduped += 1;
          break;
        case "skipped":
          this.ingestSkipped += 1;
          break;
        default:
          break;
      }
    }
  }

  recordBatchFailure(error: unknown, _messageCount: number) {
    this.batchesFailed += 1;
    this.lastError = error instanceof Error ? error.message : String(error);
  }

  recordMediaUpload(status: "queued" | "deduped" | "failed", reason?: string) {
    if (status === "queued") {
      this.mediaUploaded += 1;
      return;
    }
    if (status === "deduped") {
      this.mediaDeduped += 1;
      return;
    }
    this.mediaFailed += 1;
    if (reason) {
      const current = this.mediaFailureReasons.get(reason) ?? 0;
      this.mediaFailureReasons.set(reason, current + 1);
      this.lastMediaFailure = reason;
    }
  }

  snapshot(): MetricsSnapshot {
    return {
      connection_status: this.connectionStatus,
      ready: this.ready,
      batches_sent: this.batchesSent,
      batches_failed: this.batchesFailed,
      messages_sent: this.messagesSent,
      ingest_created: this.ingestCreated,
      ingest_accepted: this.ingestAccepted,
      ingest_deduped: this.ingestDeduped,
      ingest_skipped: this.ingestSkipped,
      media_uploaded: this.mediaUploaded,
      media_deduped: this.mediaDeduped,
      media_failed: this.mediaFailed,
      media_failure_reasons: Object.fromEntries(this.mediaFailureReasons),
      last_media_failure: this.lastMediaFailure,
      last_error: this.lastError,
      last_flush_at: this.lastFlushAt,
    };
  }
}
