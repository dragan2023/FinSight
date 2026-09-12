import { request } from "./client";
import type { DataResultView } from "./marketData";

export interface IndexItem { thscode: string; name: string; tag: string }
export interface IndexCatalog { tag: string; items: IndexItem[]; evidence_ids: string[]; local_only: boolean; notice: string }
export interface IndexResearch { thscode: string; history: { prices: Record<string, string | null> } | null; current_constituents: Array<{ thscode: string; name: string | null }>; breadth: Record<string, number | string | null>; evidence_ids: string[]; limitation: string }
export interface IndexComparison { comparison: { common_dates: string[]; normalized: Record<string, Record<string, string>>; returns: Record<string, string | null>; period_returns: Record<string, Record<string, string | null>>; selected_ranks: Record<string, number | null> }; evidence_ids: string[]; ranking_scope: string; limitation: string }

export const getIndexCatalog = (tag: string) => request<IndexCatalog>({ url: "/indices/catalog", params: { tag } });
export const refreshIndexCatalog = (tag: string) => request<DataResultView>({ url: "/indices/catalog/refresh", method: "POST", params: { tag }, data: {} });
export const selectIndex = (thscode: string) => request({ url: `/indices/${encodeURIComponent(thscode)}/select`, method: "POST", data: {} });
export const getIndexResearch = (thscode: string) => request<IndexResearch>({ url: `/indices/${encodeURIComponent(thscode)}/research` });
export const refreshIndex = (thscode: string, data: { history: boolean; constituents: boolean; member_snapshots: boolean; start_ms?: number; end_ms?: number }) => request<{ results: DataResultView[] }>({ url: `/indices/${encodeURIComponent(thscode)}/refresh`, method: "POST", data });
export const compareIndices = (thscodes: string[], reference_id: string) => request<IndexComparison>({ url: "/indices/compare", method: "POST", data: { thscodes, reference_id } });
