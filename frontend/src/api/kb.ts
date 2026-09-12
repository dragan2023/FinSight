import { request } from "./client";

export interface KbDocument {
  id: string;
  filename: string;
  kind: string;
  parse_method: string;
  byte_size: number;
  char_count: number;
  chunk_count: number;
  embedded_chunks: number;
  embedding_state: string;
  embedding_model: string;
  warnings: string[];
  knowledge_role?: "source" | "strategy" | "hypothesis";
  tags?: string[];
  review_state?: "unreviewed" | "approved" | "retired";
  effective_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KbSearchHit {
  chunk_id: string;
  document_id: string;
  filename: string;
  kind: string;
  heading: string;
  page: number | null;
  line_start: number | null;
  line_end: number | null;
  score: number;
  channels: string[];
  excerpt: string;
}

export interface KbSearchResult {
  query: string;
  embedding_used: boolean;
  embedding_model: string;
  hits: KbSearchHit[];
  warnings: string[];
  note: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export const listKbDocuments = () =>
  request<{ documents: KbDocument[] }>({ url: "/kb/documents" });

export const uploadKbDocument = (filename: string, data: Uint8Array) =>
  request<{ duplicate: boolean; document: KbDocument }>({
    url: "/kb/documents",
    method: "POST",
    data: { filename, data_base64: bytesToBase64(data) },
  });

export const deleteKbDocument = (id: string) =>
  request<{ deleted: boolean }>({ url: `/kb/documents/${id}`, method: "DELETE" });

export const rebuildKbDocument = (id: string) =>
  request<{ document: KbDocument }>({ url: `/kb/documents/${id}/rebuild`, method: "POST" });

export interface KbMetadata {
  knowledge_role: "source" | "strategy" | "hypothesis";
  tags: string[];
  review_state: "unreviewed" | "approved" | "retired";
  effective_at: string | null;
}

export const updateKbDocumentMetadata = (id: string, data: KbMetadata) =>
  request<{ document: KbDocument }>({ url: `/kb/documents/${id}`, method: "PATCH", data });

export const searchKb = (query: string, topK: number) =>
  request<KbSearchResult>({ url: "/kb/search", method: "POST", data: { query, top_k: topK } });

export interface KbChunk {
  chunk_id: string;
  position: number;
  heading: string;
  page: number | null;
  line_start: number | null;
  line_end: number | null;
  content: string;
}

/** 本机分块原文（按 position 排序）：证据回溯定位用，内容不出本机。 */
export const listKbChunks = (id: string) =>
  request<{ document: KbDocument; chunks: KbChunk[] }>({
    url: `/kb/documents/${id}/chunks`,
  });
