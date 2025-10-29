export type ChatType = "group" | "direct";

export interface WhatsAppMessageIn {
  client_id: string;
  message_id: string;
  chat_id: string;
  chat_title: string | null;
  chat_type: ChatType;
  sender_name: string | null;
  sender_phone: string | null;
  observed_at: string;
  message_timestamp: string;
  platform_id: string;
  text: string | null;
  raw_payload: unknown;
  media?: {
    mimetype: string;
    filename?: string;
    size_bytes?: number;
    url?: string;
    caption?: string;
    kind?: string;
    document_id?: string;
    failure_reason?: string;
  };
}

export interface WhatsAppIngestBatch {
  messages: WhatsAppMessageIn[];
}

export interface IngestResult {
  message_id: string;
  status: "created" | "accepted" | "deduped" | "skipped";
  reason?: string;
}

export interface IngestResponse {
  results: IngestResult[];
}
