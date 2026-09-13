import { request } from "./client";

export interface Hypothesis {
  id: string;
  reference: string;
  kind: "strategy" | "factor" | "rule" | "scan";
  name: string;
  status: "draft" | "frozen" | "retired";
  exploratory: 0 | 1;
  trial_count: number;
  params_hash: string;
  submitted_at: string;
  frozen_at: string | null;
}

export interface EvaluationBatch {
  id: string;
  name: string;
  status: "open" | "frozen" | "closed";
  started_at: string;
  closed_at: string | null;
  superseded_by: string | null;
  plan_json: string;
  stop_criteria_json: string;
}

export interface RuleChange {
  id: string;
  batch_id: string | null;
  changed_at: string;
  field: string;
  old_hash: string;
  new_hash: string;
  reason: string;
  forced_new_batch_id: string | null;
}

export interface BatchStatistics {
  batch_id: string;
  cross_batch: boolean;
  usable_for_verdict: boolean;
  member_counts: Record<string, number>;
  experiment_grades: Record<string, number>;
  total_members: number;
}

export interface TestRunFile {
  name: string;
  path: string;
  bytes: number;
}

export interface TestRunExportResult {
  directory: string;
  files: TestRunFile[];
  date: string;
  scope_label: string;
  sample_is_empty: boolean;
  counts: { advice: number; experiments: number; tracks: number; predictions: number };
}

export interface TestRunRun {
  date: string;
  path: string;
  files: Record<string, { bytes: number; modified_at: string }>;
}

export const listHypotheses = () =>
  request<{ hypotheses: Hypothesis[] }>({ url: "/governance/hypotheses" });

export const listBatches = () =>
  request<{ batches: EvaluationBatch[] }>({ url: "/governance/batches" });

export const currentBatch = () =>
  request<{ batch_id: string | null; batch: EvaluationBatch | null }>({
    url: "/governance/batches/current",
  });

export const batchStatistics = (batchId: string) =>
  request<{ statistics: BatchStatistics }>({
    url: `/governance/batches/${batchId}/statistics`,
  });

export const listRuleChanges = () =>
  request<{ changes: RuleChange[] }>({ url: "/governance/changes" });

export const listTestRunExports = () =>
  request<{ runs: TestRunRun[]; note: string }>({ url: "/governance/test-run-exports" });

/**
 * 一键落盘导出。**只能由用户点击触发**：后端不注册任何定时任务，
 * 这个函数也只被界面上的按钮调用。
 */
export const createTestRunExport = (data: { date?: string; batch_id?: string }) =>
  request<TestRunExportResult>({ url: "/governance/test-run-export", method: "POST", data });
