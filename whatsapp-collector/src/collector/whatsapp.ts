import { boomify, isBoom } from "@hapi/boom";
import * as baileysNamespace from "@adiwajshing/baileys";
import type { ConnectionState, WAMessage, WASocket } from "@adiwajshing/baileys";
import fs from "node:fs/promises";
import path from "node:path";
import qrcode from "qrcode-terminal";
import { AppConfig } from "../config.js";
import { Logger } from "../logger.js";

const baileys = (baileysNamespace as any).default ?? baileysNamespace;
const resolveMakeSocket = (): typeof baileysNamespace.default => {
  const candidates = [
    (baileys as any).default,
    (baileys as any).makeWASocket,
    (baileysNamespace as any).default,
    (baileysNamespace as any).makeWASocket,
    (baileysNamespace as any).default?.default,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "function") {
      return candidate;
    }
  }
  throw new Error("Unable to resolve makeWASocket export from @adiwajshing/baileys");
};

const makeWASocket = resolveMakeSocket();
const Browsers = baileys.Browsers ?? baileysNamespace.Browsers;
const DisconnectReason = baileys.DisconnectReason ?? baileysNamespace.DisconnectReason;
const fetchLatestBaileysVersion =
  baileys.fetchLatestBaileysVersion ?? baileysNamespace.fetchLatestBaileysVersion;
const useMultiFileAuthState =
  baileys.useMultiFileAuthState ?? baileysNamespace.useMultiFileAuthState;

export type MessageHandler = (messages: WAMessage[], socket: WASocket) => void | Promise<void>;

export interface WhatsAppClient {
  socket: WASocket | null;
  shutdown(): Promise<void>;
}

interface CreateWhatsAppClientParams {
  config: AppConfig;
  logger: Logger;
  onMessages: MessageHandler;
  onConnectionStateChange?: (state: Partial<ConnectionState>) => void;
}

export async function createWhatsAppClient({
  config,
  logger,
  onMessages,
  onConnectionStateChange,
}: CreateWhatsAppClientParams): Promise<WhatsAppClient> {
  await fs.mkdir(config.authStateDir, { recursive: true });

  const authDir = path.resolve(config.authStateDir);
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();

  let socket: WASocket | null = null;
  let shuttingDown = false;

  const startSocket = () => {
    socket = makeWASocket({
      version,
      auth: state,
      printQRInTerminal: false,
      browser: Browsers.macOS("Pricebot Collector"),
      syncFullHistory: false,
      markOnlineOnConnect: false,
    });

    socket.ev.on("creds.update", saveCreds);
    socket.ev.on("messages.upsert", (upsert) => {
      if (upsert.messages?.length && !shuttingDown) {
        // Ignore status broadcast messages early to reduce downstream noise.
        const actionable = upsert.messages.filter(
          (message) => !message.key.remoteJid?.includes("status@broadcast"),
        );

        if (actionable.length) {
          onMessages(actionable, socket!);
        }
      }
    });

    socket.ev.on("connection.update", (update) => {
      onConnectionStateChange?.(update);
      void handleConnectionUpdate(update);
    });
  };

  const handleConnectionUpdate = async (update: Partial<ConnectionState>) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr && !shuttingDown) {
      logger.info("New QR code received. Scan to link WhatsApp.");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "open") {
      logger.info("WhatsApp connection established.");
      return;
    }

    if (connection === "close") {
      const error = lastDisconnect?.error;
      const boom = error ? (isBoom(error) ? error : boomify(error)) : undefined;
      const statusCode = boom?.output.statusCode;
      const shouldReconnect = !shuttingDown && statusCode !== DisconnectReason.loggedOut;

      logger.warn(
        {
          statusCode,
          reconnecting: shouldReconnect,
        },
        "WhatsApp connection closed.",
      );

      if (shouldReconnect) {
        await delay(2000);
        startSocket();
      } else if (statusCode === DisconnectReason.loggedOut) {
        logger.error(
          "WhatsApp session is logged out. Remove auth state directory to relink.",
        );
      }
    }
  };

  startSocket();

  return {
    get socket() {
      return socket;
    },
    async shutdown() {
      shuttingDown = true;
      if (socket) {
        socket.ev.removeAllListeners("connection.update");
        socket.ev.removeAllListeners("messages.upsert");
        socket.ev.removeAllListeners("creds.update");
        socket.end(undefined);
      }
    },
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
