import { request } from "./client";

export interface TickerCandidate {
  thscode: string;
  ticker: string;
  name: string;
  exchange: string | null;
  asset_type: string;
  currency: string;
}

export interface SecurityIdentity {
  provider: string;
  thscode: string;
  ticker: string;
  name: string;
  exchange: string | null;
  asset_type: string;
  currency: string;
  confirmed_source: string;
  confirmed_at: string;
}

export interface NormalizedFieldView {
  source_field: string;
  label: string;
  kind: string;
  unit: string;
  raw_value: string | null;
  value: string | null;
  currency: string | null;
  gap_reason: string | null;
}

export interface NormalizedRecordView {
  thscode: string;
  period_key: string;
  period_end: string | null;
  disclosed_at: string | null;
  basis: string;
  currency: string | null;
  fields: Record<string, NormalizedFieldView>;
}

export interface DataGapView {
  id: string;
  dimension: string;
  fields: string[];
  security_id: string | null;
  period: string | null;
  reason: string;
  impact: string;
  blocking_for: string[];
  remedies: string[];
}

export interface DataResultView {
  status: "available" | "partial" | "unavailable";
  records: NormalizedRecordView[];
  evidence_ids: string[];
  gaps: DataGapView[];
  requested_scope: { capability: string; thscode?: string | null; params: Record<string, unknown> };
  returned_scope: Record<string, unknown>;
  source_profile_id: string | null;
}

export type DataCapability =
  | "price_snapshot"
  | "price_history"
  | "valuations"
  | "financial_statements"
  | "financial_indicators"
  | "trading_days";

export interface FetchInput {
  capability: DataCapability;
  thscode?: string;
  start?: number;
  end?: number;
  adjust?: "none" | "forward" | "backward";
  period?: "annual" | "quarterly";
  limit?: number;
  report?: string;
  statement?: "income" | "balance" | "cashflow" | "all";
  refresh?: boolean;
}

export interface EvidenceMetaView {
  id: string;
  provider: string;
  capability: string;
  endpoint_path: string;
  params: Record<string, unknown>;
  thscode: string | null;
  basis: string;
  source_time: string | null;
  fetched_at: string;
  content_hash: string;
  record_count: number;
  schema_version: string;
  storage_policy: string;
  license_note: string;
}

export interface EvidenceView {
  evidence: EvidenceMetaView & {
    records: NormalizedRecordView[];
    gaps: DataGapView[];
  };
}

export const gapReasonLabels: Record<string, string> = {
  not_configured: "未配置",
  permission_denied: "无权限",
  rate_limited: "频率/额度受限",
  timeout: "超时",
  not_published: "上游未披露",
  unsupported: "不支持",
  invalid_response: "响应无法解释",
  basis_unknown: "口径未知",
  source_conflict: "来源冲突",
  stale: "已过期",
  not_applicable: "不适用",
};

export const resultStatusLabels: Record<DataResultView["status"], string> = {
  available: "可用",
  partial: "部分可用",
  unavailable: "不可用",
};

export const resolveTickers = (query: string) =>
  request<{ candidates: TickerCandidate[] }>({ url: "/data/resolve", method: "POST", data: { query } });

export const confirmTicker = (thscode: string) =>
  request<{ identity: SecurityIdentity }>({
    url: "/data/resolve",
    method: "POST",
    data: { confirm_thscode: thscode },
  });

export const listIdentities = () =>
  request<{ identities: SecurityIdentity[] }>({ url: "/data/identities" });

export const fetchData = (input: FetchInput) =>
  request<{ results: DataResultView[] }>({ url: "/data/fetch", method: "POST", data: input });

export const getEvidence = (evidenceId: string) =>
  request<EvidenceView>({ url: `/evidence/${evidenceId}` });
