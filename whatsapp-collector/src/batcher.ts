import type { WhatsAppMessageIn } from "./types.js";

type FlushHandler = (batch: WhatsAppMessageIn[]) => Promise<void>;

interface MessageBatcherOptions {
  maxBatchSize: number;
  flushIntervalMs: number;
  onFlush: FlushHandler;
}

export class MessageBatcher {
  private readonly maxBatchSize: number;
  private readonly flushIntervalMs: number;
  private readonly onFlush: FlushHandler;

  private buffer: WhatsAppMessageIn[] = [];
  private timer: NodeJS.Timeout | null = null;
  private flushing = false;

  constructor(options: MessageBatcherOptions) {
    this.maxBatchSize = options.maxBatchSize;
    this.flushIntervalMs = options.flushIntervalMs;
    this.onFlush = options.onFlush;
  }

  add(messages: WhatsAppMessageIn[]) {
    if (!messages.length) {
      return;
    }

    this.buffer.push(...messages);

    if (this.buffer.length >= this.maxBatchSize) {
      void this.flush();
    } else {
      this.ensureTimer();
    }
  }

  async flush() {
    if (this.flushing || this.buffer.length === 0) {
      this.clearTimer();
      return;
    }

    this.flushing = true;
    this.clearTimer();
    const batch = this.buffer;
    this.buffer = [];

    try {
      await this.onFlush(batch);
    } finally {
      this.flushing = false;
    }
  }

  async shutdown() {
    await this.flush();
  }

  private ensureTimer() {
    if (this.timer) {
      return;
    }

    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.flushIntervalMs);
  }

  private clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
