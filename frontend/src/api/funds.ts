import { request } from "./client";
import type { DataResultView } from "./marketData";

export type ProviderFundType = "otc" | "exchange" | "reits";
export type FundModule = "profile" | "portfolio" | "performance" | "people" | "financials" | "diagnostics" | "news" | "market";

export interface FundSearchItem { thscode: string; ticker: string; name: string; asset_type: string; }
export interface FundRefreshView { modules: Record<string, DataResultView[]>; budget: Record<string, number>; notice: string; }
export interface FundReportDate { holding: "stock" | "bond"; report_type: string; end_date: string; }

export const searchFunds = (q: string) => request<{ items: FundSearchItem[] }>({ url: "/funds/search", method: "GET", params: { q } });
export const refreshFund = (thscode: string, data: { provider_fund_type: ProviderFundType; modules: FundModule[]; range_name: string; report_type?: string; report_end?: string }) =>
  request<FundRefreshView>({ url: `/funds/${encodeURIComponent(thscode)}/refresh`, method: "POST", data });
export const compareFunds = (fund_ids: string[]) =>
  request<{ fund_ids: string[]; items: Array<{ fund_id: string; reports: unknown[] }>; notice: string }>({ url: "/funds/compare", method: "POST", data: { fund_ids } });
export const getFundNews = (thscode: string, fund_type: ProviderFundType, offset?: string) =>
  request<{ result: DataResultView; notice: string }>({ url: `/funds/${encodeURIComponent(thscode)}/news`, method: "GET", params: { fund_type, limit: 20, offset } });
export const getFundReportDates = (thscode: string, fund_type: ProviderFundType) =>
  request<{ items: FundReportDate[]; notice: string }>({ url: `/funds/${encodeURIComponent(thscode)}/report-dates`, method: "GET", params: { fund_type } });
export const getFundOfferings = (subscribe: "active" | "upcoming" = "active") =>
  request<{ result: DataResultView; notice: string }>({ url: "/funds/offerings/list", method: "GET", params: { subscribe } });
