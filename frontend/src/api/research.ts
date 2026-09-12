import { request } from "./client";
import { getEventStream } from "./eventStream";

/**
 * Research task contracts mirroring backend/app/research/models.py and
 * backend/app/api/research.py (stage 03 section 2.6). Task status describes
 * how one round ended; data coverage lives on the report. SSE payloads carry
 * public progress only: reasoning appears solely as a character count.
 */

export type ResearchTaskStatus =
  | "queued"
  | "running"
  | "completed"
  | "interrupted"
  | "cancelled"
  | "failed";

export interface TaskBudgetView {
  max_llm_requests: number;
  max_fetches: number;
  max_tool_rounds: number;
  max_searches: number;
  max_extractions: number;
}

export interface TaskUsageView {
  llm_requests: number;
  fetches: number;
  tool_rounds: number;
  searches: number;
  extractions: number;
  reasoning_chars: number;
}

export interface ResearchTaskView {
  id: string;
  question: string;
  scope: { thscode?: string; thscodes?: string[]; type?: ResearchTaskKind | ResearchScopeType; start_ms?: number; end_ms?: number };
  status: ResearchTaskStatus;
  budget: TaskBudgetView;
  usage: TaskUsageView;
  report_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface TaskEventRecord {
  task_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SupplementView {
  id: string;
  report_id: string;
  task_id: string;
  kind: "user_provided" | "user_assumption";
  content: string;
  gap_ref: string | null;
  fields: Record<string, string>;
  referenced_by_report_id: string | null;
  created_at: string;
}

export interface SectionClaimView {
  kind: "fact" | "derived" | "inference" | "user_assumption";
  text: string;
  evidence_refs: string[];
  metric_refs: string[];
  gap_refs: string[];
}

export interface ReportSectionView {
  id: string;
  title: string;
  status: "supported" | "conditional" | "unavailable";
  summary: string;
  claims: SectionClaimView[];
  counter_evidence: { text: string; evidence_refs: string[] }[];
  follow_up_questions: string[];
}

export interface MetricValueView {
  metric: string;
  value: string | null;
  unit: string;
  basis: string;
  formula: string;
  evidence_ids: string[];
  note: string | null;
}

export interface ModelSnapshotView {
  provider: string;
  model: string;
  output_label: string;
  mode: "tools" | "template" | "none";
  json_mode: boolean;
}

export interface TopologyNodeView { id: string; label: string; category: string; value: number | null; }
export interface TopologyEdgeView { source: string; target: string; label: string; value: number | null; }
export interface PanelCellView {
  row: number; column: number; row_span: number; column_span: number;
  component: "chart" | "metrics" | "gaps"; reference: string; title: string;
}
export interface PanelGridView { columns: number; cells: PanelCellView[]; }
export interface BacktestPointView { period: string; net_value: number | null; drawdown_pct: number | null; information_coefficient: number | null; }
export interface RelationHeatView { x_labels: string[]; y_labels: string[]; values: Array<Array<number | null>>; }
export interface ChartSpecView {
  id: string;
  chart_type: "line" | "bar" | "candlestick" | "scatter" | "heatmap" | "relationship" | "topology" | "panel_grid" | "backtest_curve" | "relation_heat";
  title: string;
  metric_refs: string[];
  topology_nodes?: TopologyNodeView[];
  topology_edges?: TopologyEdgeView[];
  panel_grid?: PanelGridView | null;
  backtest_points?: BacktestPointView[];
  relation_heat?: RelationHeatView | null;
}

export interface ResearchReportView {
  schema_version: string;
  id: string;
  version: number;
  parent_report_id: string | null;
  task_id: string;
  question: string;
  scope: { thscode?: string };
  model_snapshot: ModelSnapshotView | null;
  created_at: string;
  evidence_ids: string[];
  metrics: MetricValueView[];
  chart_specs?: ChartSpecView[];
  sections: ReportSectionView[];
  data_gaps: import("./marketData").DataGapView[];
  coverage: "sufficient" | "partial" | "no_business_evidence";
  output_integrity: "validated" | "partial" | "unstructured" | "truncated";
  limitations: string[];
}

export interface RawSectionView {
  id: string;
  title: string;
  reason: string;
  text: string;
}

export interface ReportChatMessageView {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sources: { title: string; domain: string; url: string; published_at?: string | null }[];
  created_at: string;
}

export type ResearchTaskKind = "general" | "company_quality" | "company_comparison" | "thesis_review" | "sector_relative" | "fund_overlap" | "fund_quality" | "market_review";
export type ResearchScopeType = "company_overview" | "watchlist_review" | "index_strength" | "sector_breadth" | "market_pulse" | "fund_overview" | "fund_compare" | "market_breadth" | "general";
export interface CreateResearchInput {
  question: string;
  thscode?: string;
  type?: ResearchTaskKind;
  scope?: { type: ResearchScopeType; thscodes?: string[]; start_ms?: number; end_ms?: number; modules?: string[] };
  budget?: { max_llm_requests?: number; max_fetches?: number; max_tool_rounds?: number };
  model_profile_id?: string;
  client_request_id?: string;
}

export interface SupplementInput {
  kind: "user_provided" | "user_assumption";
  content: string;
  fields?: Record<string, string>;
  gap_ref?: string;
}

export interface ReanalyzeInput {
  supplement_ids?: string[];
  model_profile_id?: string;
  client_request_id?: string;
}

export const createResearchTask = (input: CreateResearchInput) =>
  request<{ task: ResearchTaskView }>({ url: "/research/tasks", method: "POST", data: input });

export const getResearchTask = (taskId: string) =>
  request<{ task: ResearchTaskView; last_seq: number; events: TaskEventRecord[] }>({
    url: `/research/tasks/${taskId}`,
  });

export const cancelResearchTask = (taskId: string) =>
  request<{ task: ResearchTaskView }>({
    url: `/research/tasks/${taskId}/cancel`,
    method: "POST",
    data: {},
  });

export const getResearchReport = (reportId: string) =>
  request<{ report: ResearchReportView; raw_sections: RawSectionView[] }>({
    url: `/research/reports/${reportId}`,
  });

export const getReportConversation = (reportId: string) =>
  request<{ conversation: { id: string; report_id: string }; messages: ReportChatMessageView[] }>({
    url: `/research/reports/${reportId}/conversation`,
  });

export const askReportFollowUp = (reportId: string, input: {
  question: string; use_web_search: boolean; search_provider?: "bocha";
}) => request<{ message: ReportChatMessageView }>({
  url: `/research/reports/${reportId}/conversation/messages`, method: "POST", data: input,
});

export interface OpenChatMessageView {
  role: "user" | "assistant";
  content: string;
  sources?: { title: string; domain: string; url: string; snippet: string }[];
  searchNotice?: string;
}

export const askOpenChat = (input: {
  question: string; thscode?: string; history: OpenChatMessageView[]; use_web_search: boolean;
}) => request<{ answer: string; sources: NonNullable<OpenChatMessageView["sources"]>; search_notice?: string | null }>({
  url: "/research/ai-chat/messages", method: "POST", data: input,
});

export const addSupplement = (reportId: string, input: SupplementInput) =>
  request<{ supplement: SupplementView }>({
    url: `/research/reports/${reportId}/supplements`,
    method: "POST",
    data: input,
  });

export const reanalyzeReport = (reportId: string, input: ReanalyzeInput) =>
  request<{ task: ResearchTaskView }>({
    url: `/research/reports/${reportId}/reanalyze`,
    method: "POST",
    data: input,
  });

/**
 * Research progress SSE. The backend answers GET with a snapshot frame first,
 * then seq-ordered public events, then an "end" frame before closing; pass
 * after=lastSeq to resume without replaying older events.
 */
export function streamResearchEvents(
  taskId: string,
  after: number,
  handlers: { signal?: AbortSignal; onEvent: (event: ResearchStreamEvent) => void },
): Promise<void> {
  return getEventStream<ResearchStreamEvent>(
    `/api/research/tasks/${taskId}/events?after=${after}`,
    handlers,
  );
}

/**
 * Public progress frames. Only field names used by the UI are typed; unknown
 * payload keys stay accessible through the index signature.
 */
export interface ResearchStreamEvent {
  type: string;
  seq?: number;
  status?: string;
  chars?: number;
  phase?: string;
  detail?: string;
  id?: string;
  integrity?: string;
  section?: string;
  code?: string;
  report_id?: string;
  llm_requests?: number;
  fetches?: number;
  tool_rounds?: number;
  reasoning_chars?: number;
  [key: string]: unknown;
}

export const isTerminalEvent = (type: string) =>
  type === "task.completed" || type === "task.cancelled" ||
  type === "task.interrupted" || type === "task.failed";

const HEX = "0123456789abcdef";

/** 32 hex chars like the backend's uuid4().hex; safe fallback without crypto. */
export function newClientRequestId(): string {
  const globalCrypto = globalThis.crypto;
  if (globalCrypto && typeof globalCrypto.randomUUID === "function") {
    return globalCrypto.randomUUID().replace(/-/g, "");
  }
  let id = "";
  while (id.length < 32) {
    id += HEX[Math.floor(Math.random() * 16)];
  }
  return id;
}

export const taskStatusLabels: Record<ResearchTaskStatus, string> = {
  queued: "排队中",
  running: "分析中",
  completed: "已完成",
  interrupted: "已中断",
  cancelled: "已取消",
  failed: "执行失败",
};

export const coverageLabels = {
  sufficient: "数据充分",
  partial: "部分覆盖",
  no_business_evidence: "无业务事实",
} as const;

export const integrityLabels = {
  validated: "已通过校验",
  partial: "部分完成",
  unstructured: "含未通过结构校验章节",
  truncated: "存在截断章节",
} as const;

export const sectionStatusLabels = {
  supported: "有依据",
  conditional: "条件性",
  unavailable: "未生成",
} as const;

export const claimKindLabels = {
  fact: "事实",
  derived: "计算",
  inference: "推断",
  user_assumption: "用户假设",
} as const;

export const modelModeLabels = {
  tools: "结构化工具",
  template: "固定取数模板（能力降级）",
  none: "无可用模型",
} as const;
