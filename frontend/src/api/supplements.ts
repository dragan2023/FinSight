import { request, requestMultipart } from "./client";
import type { SupplementView } from "./research";

/**
 * Supplement attachment contracts (plan 04 sections 2.5/2.6), mirroring
 * backend/app/research/attachments.py and backend/app/api/supplements.py.
 * 上传/附件资料只保存在本机数据库；原始文件默认不留存，保存 ≠ 同意外发，
 * 也绝不自动触发再分析。
 */

export type AttachmentRetention = "reference_only" | "excerpt";

export interface AttachmentView {
  id: string;
  report_id: string;
  task_id: string;
  display_name: string;
  extension: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  kind: string;
  parse_method: string;
  storage_policy: "reference_only" | "excerpt";
  raw_file_retained: boolean;
  excerpt_path: string | null;
  truncated: boolean;
  warnings: string[];
  stats: Record<string, unknown>;
  supplement_id: string | null;
  mapping: Record<string, string>;
  mapping_confirmed_at: string | null;
  /** 批次7：解析时确定性提取的候选值（仅预填表单；确认仍走 mapping）。 */
  mapping_candidates: Record<string, string>;
  created_at: string;
}

export interface AttachmentGapView {
  id: string;
  dimension: string;
  reason: string;
  impact: string;
  remedies: string[];
}

export interface AttachmentFailureView {
  display_name: string;
  code: string;
  message: string;
  gap: AttachmentGapView | null;
}

export interface UploadOutcomeView {
  saved: AttachmentView[];
  failed: AttachmentFailureView[];
  supplements: SupplementView[];
  gaps: AttachmentGapView[];
}

export interface AttachmentPreviewView {
  attachment: AttachmentView;
  excerpt: string | null;
  supplement: SupplementView | null;
}

export interface AttachmentListView {
  attachments: AttachmentView[];
  supplements: SupplementView[];
}

export interface AttachmentDeletionView {
  id: string;
  deleted: boolean;
  excerpt_removed: boolean;
}

export interface MappingInput {
  security_id?: string;
  period?: string;
  unit?: string;
  note?: string;
}

export interface SourceFlowPreviewView {
  flow: string;
  description: string;
  enabled: boolean;
  budget_note: string | null;
  requires_confirmation: boolean;
}

export interface SourcePreviewView {
  task_id: string;
  task_status: string;
  flows: SourceFlowPreviewView[];
  budget: Record<string, number>;
  usage: Record<string, number>;
  supplements: { total: number; referenced: number };
  notice: string;
}

export function uploadSupplementFiles(
  reportId: string,
  files: File[],
  retention: AttachmentRetention,
): Promise<UploadOutcomeView> {
  const form = new FormData();
  form.append("report_id", reportId);
  form.append("retention", retention);
  for (const file of files) {
    form.append("files", file, file.name);
  }
  return requestMultipart<UploadOutcomeView>("/supplements/files", form);
}

export function listReportSupplements(reportId: string): Promise<AttachmentListView> {
  return request<AttachmentListView>({
    url: "/supplements/attachments",
    method: "GET",
    params: { report_id: reportId },
  });
}

export function getAttachmentPreview(attachmentId: string): Promise<AttachmentPreviewView> {
  return request<AttachmentPreviewView>({ url: "/supplements/" + attachmentId });
}

export function confirmAttachmentMapping(
  attachmentId: string,
  input: MappingInput,
): Promise<{ attachment: AttachmentView }> {
  return request<{ attachment: AttachmentView }>({
    url: "/supplements/" + attachmentId + "/mapping",
    method: "PUT",
    data: input,
  });
}

export function deleteAttachment(attachmentId: string): Promise<AttachmentDeletionView> {
  return request<AttachmentDeletionView>({
    url: "/supplements/" + attachmentId,
    method: "DELETE",
  });
}

export function sourcePreview(taskId: string): Promise<SourcePreviewView> {
  return request<SourcePreviewView>({
    url: "/research/tasks/" + taskId + "/source-preview",
    method: "POST",
    data: {},
  });
}
