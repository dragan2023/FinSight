import { request } from "./client";
import type { DataResultView } from "./marketData";
import type { MarketReviewDashboardData } from "../components/MarketReviewDashboard/types";

export interface MarketObservation {
  id: string;
  date: string;
  modules: string[];
  summary: Record<string, { status: string; count: number; gaps: number }>;
  dashboard: MarketReviewDashboardData;
  evidence_ids: string[];
  created_at: string;
}
export const getLatestMarketObservation = () =>
  request<{ observation: MarketObservation | null }>({
    url: "/market-reviews/latest",
  });
export const refreshMarketObservation = (data: {
  date_ms: number;
  modules: string[];
  max_calls: number;
}) =>
  request<{
    results: DataResultView[];
    summary: MarketObservation["summary"];
    dashboard: MarketReviewDashboardData;
  }>({ url: "/market-reviews/refresh", method: "POST", data });
