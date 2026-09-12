import { request } from "./client";
import { getEventStream } from "./eventStream";

export interface AdvisorProfile {
  risk_appetite: string;
  focus_domains: string[];
  excluded_thscodes: string[];
}

export interface AdvisorSession {
  id: string;
  title: string;
  user_profile: AdvisorProfile;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface AdvisorMessage {
  id: string;
  session_id: string;
  role: "user" | "advisor";
  content: string;
  suggestion: Record<string, unknown> | null;
  created_at: string;
}

export interface AdvisorPhase {
  phase: string;
  detail: string;
}

export interface SendResult {
  needs_clarification: boolean;
  message: AdvisorMessage;
  phases: AdvisorPhase[];
  llm_requests: number;
  advice_id: string | null;
}

export interface AdvisorRun {
  id: string;
  session_id: string;
  status: "queued" | "running" | "completed" | "cancelled" | "failed" | "interrupted";
  phases: AdvisorPhase[];
  llm_requests: number;
  error: string | null;
  evidence: AdvisorEvidence[];
  analysis_mode?: "standard" | "deep";
  created_at: string;
  updated_at: string;
}

export interface AdvisorEvidenceSource {
  kind: "rss" | "web" | "kb" | "market" | "local" | string;
  label: string;
  ref?: string | null;
  page?: number | null;
}

export interface AdvisorEvidence {
  id: string;
  dimension: string;
  title: string;
  text: string;
  sources?: AdvisorEvidenceSource[];
  metrics?: { name: string; value: string | null; unit: string; evidence_refs: string[] }[];
}

export interface SuggestionCardView {
  thscode: string;
  name: string;
  direction: string;
  horizon: string;
  confidence: string;
  recommendation_tier?: string;
  recommendation_tier_reason?: string;
  scenarios: { name: string; probability_pct: number; probability_source: string;
    trigger_condition: string; expected_range_pct: [number, number] | null }[];
  schema_version?: string;
  claims?: { kind: "fact" | "derived" | "inference" | "forecast"; text: string; evidence_refs: string[]; metric_refs: string[]; strategy_refs: string[] }[];
  key_evidence: string[];
  counter_evidence?: string[];
  strategy_refs?: string[];
  confidence_basis?: Record<string, unknown>;
  legacy_unverified?: boolean;
  invalidations: string[];
  watchpoints: string[];
  missing_for_decision: string[];
  position_thinking: string | null;
  decision_narrative: string;
}

export interface LedgerEntry {
  id: string;
  thscode: string;
  name: string;
  direction: string;
  confidence: string;
  horizon: string;
  status: string;
  settled_at: string | null;
  settlement: Record<string, unknown> | null;
  evidence?: AdvisorEvidence[];
  created_at: string;
  strategy_refs?: string[];
  strategy_forecasts?: { track: "strategy_only"; strategy_ref: string; signal: string;
    event: string | null; horizon_days: number; probability: number | null;
    signal_as_of: string | null; fresh: boolean; eligible: boolean; reason?: string | null }[];
  forecast_event?: { event: string | null; horizon_days: number; probability: number | null; return_threshold_pct: number; source: string };
  card?: Record<string, unknown>;
  confidence_basis?: Record<string, unknown>;
}

export interface Calibration {
  window: number;
  hit_rate: number | null;
  high_rate: number | null;
  warn_high: boolean;
  coverage_rate?: number;
  brier?: number | null;
  brier_skill_score?: number | null;
  calibration_gap?: number | null;
  sample_sufficient?: boolean;
  insufficiency_reason?: string | null;
  track_comparison?: {
    advisor_combined: { count: number; hit_rate: number | null; brier: number | null };
    strategy_only: { count: number; hit_rate: number | null; brier: number | null };
  };
}

export type ResearchPredictionHorizon = "days_5" | "days_20" | "quarter" | "year" | "custom";

export interface ResearchPrediction {
  id: string;
  source: "manual" | "advisor_research";
  thscode: string;
  name: string;
  as_of_date: string;
  horizon: ResearchPredictionHorizon;
  probabilities: { up: number; flat: number; down: number };
  dominant_scenario: "up" | "flat" | "down";
  event: "positive_return" | "flat_return" | "negative_return";
  return_threshold_pct: number;
  probability_source: "model_subjective" | "base_rate";
  rationale: string;
  counter_evidence: string[];
  invalidations: string[];
  evidence_snapshot: Record<string, unknown>[];
  evidence_hash: string;
  record_hash: string;
  model_profile_id: string | null;
  prompt_version: string;
  linked_advice_id: string | null;
  status: "sealed" | "settled" | "void";
  settlement: Record<string, unknown> | null;
  settled_at: string | null;
  created_at: string;
}

export interface ResearchPredictionInput {
  thscode: string;
  name: string;
  as_of_date: string;
  horizon: ResearchPredictionHorizon;
  p_up: number;
  p_flat: number;
  p_down: number;
  probability_source: "model_subjective" | "base_rate";
  rationale: string;
  counter_evidence: string[];
  invalidations: string[];
  evidence_snapshot?: Record<string, unknown>[];
  return_threshold_pct?: number;
  source?: "manual" | "advisor_research";
}

export interface ResearchPredictionSummary {
  sealed: number;
  settled: number;
  void: number;
  independent_samples: number;
  hit_rate: number | null;
  brier: number | null;
  sample_sufficient: boolean;
}

export const listAdvisorSessions = () =>
  request<{ sessions: AdvisorSession[] }>({ url: "/advisor/sessions" });

export const createAdvisorSession = (title: string) =>
  request<{ duplicate: boolean; session: AdvisorSession }>({ url: "/advisor/sessions", method: "POST", data: { title } });

export const deleteAdvisorSession = (id: string) =>
  request<{ deleted: boolean }>({ url: `/advisor/sessions/${id}`, method: "DELETE" });

export const listAdvisorMessages = (sessionId: string) =>
  request<{ messages: AdvisorMessage[] }>({ url: `/advisor/sessions/${sessionId}/messages` });

export const sendAdvisorMessage = (sessionId: string, content: string, allowWebSearch: boolean, analysisMode: "standard" | "deep" = "standard") =>
  request<SendResult>({ url: `/advisor/sessions/${sessionId}/send`, method: "POST",
    data: { content, allow_web_search: allowWebSearch, analysis_mode: analysisMode } });

export const startAdvisorRun = (sessionId: string, content: string, allowWebSearch: boolean, analysisMode: "standard" | "deep" = "standard") =>
  request<{ run: AdvisorRun }>({ url: `/advisor/sessions/${sessionId}/runs`, method: "POST",
    data: { content, allow_web_search: allowWebSearch, analysis_mode: analysisMode } });

export const getAdvisorRun = (id: string) => request<{ run: AdvisorRun }>({ url: `/advisor/runs/${id}` });
export const listSessionRuns = (sessionId: string) =>
  request<{ runs: AdvisorRun[] }>({ url: `/advisor/sessions/${sessionId}/runs` });
export const cancelAdvisorRun = (id: string) => request<{ run: AdvisorRun }>({ url: `/advisor/runs/${id}/cancel`, method: "POST", data: {} });
export const resumeAdvisorRun = (id: string) => request<{ run: AdvisorRun }>({ url: `/advisor/runs/${id}/resume`, method: "POST", data: {} });

/** 顾问深度任务的公开进度流；断线时可按最后 seq 续读，不会重放付费请求。 */
export interface AdvisorRunEvent {
  seq: number;
  type: "queued" | "running" | "phase" | "completed" | "cancelled" | "failed" | "end";
  run: AdvisorRun;
}

export const streamAdvisorRunEvents = (
  id: string,
  after: number,
  handlers: { signal?: AbortSignal; onEvent: (event: AdvisorRunEvent) => void },
) => getEventStream<AdvisorRunEvent>(`/api/advisor/runs/${id}/events?after=${after}`, handlers);

export const listAdviceLedger = () =>
  request<{ entries: LedgerEntry[]; calibration: Calibration }>({ url: "/advisor/ledger" });

export const settleAdvice = (id: string) =>
  request<{ entry: LedgerEntry }>({ url: `/advisor/ledger/${id}/settle`, method: "POST", data: {} });

export const settleEligibleAdvice = () =>
  request<{ eligible: number; outcomes: { id: string; entry?: LedgerEntry; error?: { code: string; message: string } }[] }>({
    url: "/advisor/ledger/settle-eligible", method: "POST", data: { confirmed: true },
  });

export const listResearchPredictions = () =>
  request<{ predictions: ResearchPrediction[]; summary: ResearchPredictionSummary }>({ url: "/advisor/research-predictions" });

export const sealResearchPrediction = (payload: ResearchPredictionInput) =>
  request<{ prediction: ResearchPrediction }>({ url: "/advisor/research-predictions", method: "POST", data: payload });

export const verifyAllResearchPredictions = () =>
  request<{ checked: number; intact: boolean; violations: { id: string }[] }>({ url: "/advisor/research-predictions/verify-all" });

export const verifyResearchPrediction = (id: string) =>
  request<{ id: string; record_hash: string; recomputed_hash: string; intact: boolean }>({ url: `/advisor/research-predictions/${id}/verify` });

export const settleResearchPrediction = (id: string) =>
  request<{ prediction: ResearchPrediction }>({ url: `/advisor/research-predictions/${id}/settle`, method: "POST", data: {} });
