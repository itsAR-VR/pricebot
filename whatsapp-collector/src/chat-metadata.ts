import type { GroupMetadata, WASocket } from "@adiwajshing/baileys";
import { ChatType } from "./types.js";
import type { Logger } from "./logger.js";

export interface ChatInfo {
  id: string;
  type: ChatType;
  title: string | null;
}

type SocketProvider = () => WASocket | null;

export class ChatMetadataCache {
  private readonly cache = new Map<string, Promise<ChatInfo>>();

  constructor(
    private readonly logger: Logger,
    private readonly socketProvider: SocketProvider,
  ) {}

  async get(remoteJid: string): Promise<ChatInfo> {
    if (!this.cache.has(remoteJid)) {
      this.cache.set(remoteJid, this.fetch(remoteJid));
    }
    return this.cache.get(remoteJid)!;
  }

  clear(remoteJid?: string) {
    if (remoteJid) {
      this.cache.delete(remoteJid);
    } else {
      this.cache.clear();
    }
  }

  private async fetch(remoteJid: string): Promise<ChatInfo> {
    const type: ChatType = remoteJid.endsWith("@g.us") ? "group" : "direct";
    let title: string | null = null;

    if (type === "group") {
      const socket = this.socketProvider();
      if (!socket) {
        return { id: remoteJid, type, title: null };
      }

      try {
        const metadata: GroupMetadata = await socket.groupMetadata(remoteJid);
        title = metadata.subject ?? null;
      } catch (error) {
        this.logger.warn(
          { chatId: remoteJid, error },
          "Failed to resolve group metadata",
        );
      }
    }

    return { id: remoteJid, type, title };
  }
}
