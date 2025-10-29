import { z } from "zod";

const logLevels = ["trace", "debug", "info", "warn", "error", "fatal"] as const;

const configSchema = z.object({
  whatsappIngestUrl: z
    .string()
    .url()
    .describe("Absolute URL to the backend ingest endpoint"),
  whatsappIngestToken: z
    .string()
    .min(1, "WHATSAPP_INGEST_TOKEN is required"),
  clientId: z.string().min(1, "CLIENT_ID is required"),
  authStateDir: z.string().default("./auth-state"),
  batchMaxMessages: z
    .union([z.string(), z.number()])
    .transform((value) => Number(value))
    .refine((value) => Number.isInteger(value) && value > 0, {
      message: "BATCH_MAX_MESSAGES must be a positive integer",
    })
    .default(50),
  batchFlushIntervalMs: z
    .union([z.string(), z.number()])
    .transform((value) => Number(value))
    .refine((value) => Number.isInteger(value) && value > 0, {
      message: "BATCH_FLUSH_INTERVAL_MS must be a positive integer",
    })
    .default(1500),
  logLevel: z
    .string()
    .transform((level) => level.toLowerCase())
    .refine((level) => logLevels.includes(level as (typeof logLevels)[number]), {
      message: `LOG_LEVEL must be one of: ${logLevels.join(", ")}`,
    })
    .default("info"),
  port: z
    .union([z.string(), z.number()])
    .transform((value) => Number(value))
    .refine((value) => Number.isInteger(value) && value > 0, {
      message: "PORT must be a positive integer",
    })
    .default(8080),
});

export type AppConfig = z.infer<typeof configSchema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = configSchema.safeParse({
    whatsappIngestUrl: env.WHATSAPP_INGEST_URL,
    whatsappIngestToken: env.WHATSAPP_INGEST_TOKEN,
    clientId: env.CLIENT_ID,
    authStateDir: env.AUTH_STATE_DIR ?? "./auth-state",
    batchMaxMessages: env.BATCH_MAX_MESSAGES ?? 50,
    batchFlushIntervalMs: env.BATCH_FLUSH_INTERVAL_MS ?? 1500,
    logLevel: env.LOG_LEVEL ?? "info",
    port: env.PORT ?? 8080,
  });

  if (!parsed.success) {
    const formatted = parsed.error.issues.map((issue) => issue.message).join("; ");
    throw new Error(`Invalid configuration: ${formatted}`);
  }

  return parsed.data;
}
