import pino from "pino";

export type Logger = pino.Logger;

export function createLogger(level: string): Logger {
  return pino({
    level,
    base: undefined,
    timestamp: pino.stdTimeFunctions.isoTime,
    messageKey: "message",
    formatters: {
      level: (label) => ({ level: label }),
    },
  });
}
