import http from "node:http";
import type { Logger } from "./logger.js";
import type { Metrics } from "./metrics.js";

interface ServerOptions {
  port: number;
  logger: Logger;
  metrics: Metrics;
}

export class StatusServer {
  private readonly port: number;
  private readonly logger: Logger;
  private readonly metrics: Metrics;
  private server: http.Server | null = null;

  constructor({ port, logger, metrics }: ServerOptions) {
    this.port = port;
    this.logger = logger;
    this.metrics = metrics;
  }

  start(): Promise<void> {
    if (this.server) {
      return Promise.resolve();
    }

    this.server = http.createServer((req, res) => {
      if (!req.url) {
        res.statusCode = 400;
        res.end();
        return;
      }

      if (req.url === "/healthz") {
        const snapshot = this.metrics.snapshot();
        const statusCode = snapshot.ready ? 200 : 503;
        const body = JSON.stringify({
          status: snapshot.ready ? "ok" : "initialising",
          connection_status: snapshot.connection_status,
        });

        res.statusCode = statusCode;
        res.setHeader("Content-Type", "application/json");
        res.end(body);
        return;
      }

      if (req.url === "/metrics") {
        const snapshot = this.metrics.snapshot();
        res.statusCode = 200;
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify(snapshot));
        return;
      }

      res.statusCode = 404;
      res.end();
    });

    return new Promise((resolve, reject) => {
      this.server!.once("error", (error) => {
        this.logger.error({ error }, "Status server error");
        reject(error);
      });

      this.server!.listen(this.port, () => {
        this.logger.info({ port: this.port }, "Status server listening");
        resolve();
      });
    });
  }

  stop(): Promise<void> {
    if (!this.server) {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      this.server!.close(() => {
        this.logger.info("Status server closed");
        this.server = null;
        resolve();
      });
    });
  }
}
