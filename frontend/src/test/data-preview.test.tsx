import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as marketData from "../api/marketData";
import type { DataResultView, SecurityIdentity, TickerCandidate } from "../api/marketData";
import DataPreview from "../components/DataPreview";

vi.mock("../api/marketData", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/marketData")>()),
  resolveTickers: vi.fn(),
  confirmTicker: vi.fn(),
  listIdentities: vi.fn(),
  fetchData: vi.fn(),
  getEvidence: vi.fn(),
}));

const candidate: TickerCandidate = {
  thscode: "600519.SH",
  ticker: "600519",
  name: "贵州茅台",
  exchange: "SH",
  asset_type: "a-share",
  currency: "CNY",
};

const identity: SecurityIdentity = {
  provider: "hexin",
  thscode: "600519.SH",
  ticker: "600519",
  name: "贵州茅台",
  exchange: "SH",
  asset_type: "a-share",
  currency: "CNY",
  confirmed_source: "user_selected",
  confirmed_at: "2026-08-31T10:00:00+08:00",
};

const result: DataResultView = {
  status: "partial",
  records: [
    {
      thscode: "600519.SH",
      period_key: "2024-FY",
      period_end: "2024-12-31",
      disclosed_at: "2024-12-31T00:00:00+08:00",
      basis: "annual_report",
      currency: "CNY",
      fields: {
        operating_income: {
          source_field: "operating_income",
          label: "营业收入",
          kind: "amount",
          unit: "CNY",
          raw_value: "174144000000",
          value: "174144000000",
          currency: "CNY",
          gap_reason: null,
        },
        net_profit: {
          source_field: "net_profit",
          label: "净利润",
          kind: "amount",
          unit: "CNY",
          raw_value: null,
          value: null,
          currency: "CNY",
          gap_reason: "not_published",
        },
      },
    },
  ],
  evidence_ids: ["a".repeat(32)],
  gaps: [
    {
      id: "b".repeat(32),
      dimension: "financials.income",
      fields: ["net_profit"],
      security_id: "600519.SH",
      period: "2024-FY",
      reason: "not_published",
      impact: "上游尚未披露该期数据，不构成数据错误。",
      blocking_for: [],
      remedies: ["等待上游披露后重试，或改用已披露的期间。"],
    },
  ],
  requested_scope: { capability: "financial_statements", thscode: "600519.SH", params: {} },
  returned_scope: { count: 1, from_cache: false },
  source_profile_id: "p1",
};

function renderPreview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DataPreview />
    </QueryClientProvider>,
  );
}

async function confirmFlow(user: ReturnType<typeof userEvent.setup>) {
  vi.mocked(marketData.resolveTickers).mockResolvedValue({ candidates: [candidate] });
  vi.mocked(marketData.confirmTicker).mockResolvedValue({ identity });
  await user.type(await screen.findByLabelText("标的检索"), "600519");
  await user.click(screen.getByRole("button", { name: /检\s*索/ }));
  expect(await screen.findByText("贵州茅台")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /确\s*认/ }));
  await waitFor(() => expect(marketData.confirmTicker).toHaveBeenCalledWith("600519.SH"));
  expect(await screen.findByText(/当前已确认：贵州茅台/)).toBeInTheDocument();
}

describe("data preview", () => {
  beforeEach(() => {
    vi.mocked(marketData.listIdentities).mockResolvedValue({ identities: [identity] });
    vi.mocked(marketData.getEvidence).mockResolvedValue({
      evidence: {
        id: "a".repeat(32),
        provider: "hexin",
        capability: "financial_statements",
        endpoint_path: "/api/a-share/financials/income-statements",
        params: { thscode: "600519.SH" },
        thscode: "600519.SH",
        basis: "annual_report",
        source_time: "2024-12-31T00:00:00+08:00",
        fetched_at: "2026-08-31T10:00:00+08:00",
        content_hash: "c".repeat(64),
        record_count: 1,
        schema_version: "finsight.evidence.v1",
        storage_policy: "reference_only",
        license_note: "同花顺 Financial-API 返回数据",
        records: result.records,
        gaps: result.gaps,
      },
    });
  });

  it("confirms a searched candidate and shows the saved identity", async () => {
    const user = userEvent.setup();
    renderPreview();
    await confirmFlow(user);
  });

  it("renders gap reasons and keeps decimals as strings without zero-filling", async () => {
    const user = userEvent.setup();
    vi.mocked(marketData.fetchData).mockResolvedValue({ results: [result] });
    renderPreview();
    await confirmFlow(user);
    await user.click(screen.getByRole("button", { name: "财务三表" }));
    expect(await screen.findByText(/查看数据与证据/)).toBeInTheDocument();
    expect(marketData.fetchData).toHaveBeenCalledWith(
      expect.objectContaining({ capability: "financial_statements", thscode: "600519.SH" }),
    );
    await user.click(screen.getByRole("button", { name: /查看数据与证据/ }));
    expect(await screen.findByText(/缺失：上游未披露/)).toBeInTheDocument();
    expect(screen.getByText(/174144000000/)).toBeInTheDocument();
    // A null value must show as "--", never as 0 or a fabricated number.
    expect(screen.getByText(/净利润: --/)).toBeInTheDocument();
    expect(screen.getByText(/上游尚未披露该期数据/)).toBeInTheDocument();
  });

  it("keeps the evidence drawer honest about provenance", async () => {
    const user = userEvent.setup();
    vi.mocked(marketData.fetchData).mockResolvedValue({ results: [result] });
    renderPreview();
    await confirmFlow(user);
    await user.click(screen.getByRole("button", { name: "财务三表" }));
    await user.click(await screen.findByRole("button", { name: /查看数据与证据/ }));
    expect(await screen.findByText("证据出处")).toBeInTheDocument();
    expect(screen.getByText(/reference_only/)).toBeInTheDocument();
    expect(screen.getAllByText("annual_report").length).toBeGreaterThan(0);
  });
});
