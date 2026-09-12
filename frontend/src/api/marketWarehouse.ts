import { request, requestMultipart } from "./client";
export interface WarehouseReadiness {
  price_panel: { status: "ready_for_research" | "not_ready"; missing: string[]; purpose: string };
  recommendation_evidence: { status: "not_ready"; missing: string[]; purpose: string };
}
export interface WarehouseStatus {
  available: boolean;
  row_count: number;
  min_date_ms?: number | null;
  max_date_ms?: number | null;
  symbol_count?: number;
  trading_day_count?: number;
  latest_day_symbol_count?: number;
  adjustment_factor_row_count?: number;
  adjustment_factor_min_date_ms?: number | null;
  adjustment_factor_max_date_ms?: number | null;
  adjustment_factor_symbol_count?: number;
  readiness: WarehouseReadiness;
}
export interface WarehouseSyncRun { id: string; kind: "daily_k" | "daily_k_10d" | "adjustment_factors" | "daily_k_import"; status: "queued" | "running" | "completed" | "failed" | "cancelled"; failure_type?: string | null; failure_message?: string | null; created_at: string; finished_at?: string | null; }
export const getWarehouseStatus = () => request<WarehouseStatus>({ url: "/market-warehouse/status", method: "GET" });
export const syncWarehouse = (kind: "daily_k" | "daily_k_10d" | "adjustment_factors") => request<WarehouseSyncRun>({ url: "/market-warehouse/sync", method: "POST", data: { kind } });
export const importWarehouseParquet = (file: File) => { const form = new FormData(); form.append("file", file); return requestMultipart<{ sync_id: string; status: string; quality: Record<string, number> }>("/market-warehouse/import", form); };
export const getWarehouseSync = (syncId: string) => request<WarehouseSyncRun>({ url: `/market-warehouse/sync/${encodeURIComponent(syncId)}`, method: "GET" });
export const cancelWarehouseSync = (syncId: string) => request<WarehouseSyncRun>({ url: `/market-warehouse/sync/${encodeURIComponent(syncId)}/cancel`, method: "POST", data: {} });
export const queryWarehouse = (data: { date_ms: number; metric: "breadth" | "liquidity" | "sample"; min_turnover?: number; page?: number; page_size?: number }) => request<Record<string, unknown>>({ url: "/market-warehouse/query", method: "POST", data });
export const clearWarehouseDerived = () => request<{ status: "completed"; notice: string }>({ url: "/market-warehouse/clear-derived", method: "POST", data: { confirmed: true } });
