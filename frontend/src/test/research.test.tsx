import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import * as marketData from "../api/marketData";
import * as research from "../api/research";
import * as system from "../api/system";
import ResearchReport from "../components/ResearchReport";
import type {
  ResearchReportView,
  ResearchTaskView,
  ResearchStreamEvent,
  SupplementView,
} from "../api/research";
import type { SecurityIdentity } from "../api/marketData";

vi.mock("../api/system", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/system")>();
  const getSystemStatus = vi.fn();
  return {
    ...original,
    getSystemStatus,
    systemQuery: { queryKey: ["system"], queryFn: getSystemStatus },
  };
});
vi.mock("../api/marketData", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/marketData")>()),
  listIdentities: vi.fn(),
  resolveTickers: vi.fn(),
  confirmTicker: vi.fn(),
}));
vi.mock("../api/research", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/research")>()),
  createResearchTask: vi.fn(),
  getResearchTask: vi.fn(),
  cancelResearchTask: vi.fn(),
  getResearchReport: vi.fn(),
  getReportConversation: vi.fn(),
  askReportFollowUp: vi.fn(),
  addSupplement: vi.fn(),
  reanalyzeReport: vi.fn(),
  streamResearchEvents: vi.fn(),
}));
vi.mock("../api/supplements", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/supplements")>()),
  listReportSupplements: vi.fn().mockResolvedValue({ attachments: [], supplements: [] }),
}));

const identity: SecurityIdentity = {
  provider: "hexin",
  thscode: "600519.SH",
  ticker: "600519",
  name: "贵州茅台",
  exchange: "SH",
  asset_type: "a-share",
  currency: "CNY",
  confirmed_source: "user_selected",
  confirmed_at: "2026-09-01T10:00:00+08:00",
};

const systemStatus = {
  app: "FinSight",
  version: "0.1.0",
  stage: "foundation",
  local_only: true,
  database: "ready",
  market_data: { status: "ready", key_configured: true },
  model: {
    source: "profile",
    status: "ready",
    model_label: "DeepSeek V4 Flash",
    active_profile_id: "profile-1",
    profile_count: 1,
  },
  encryption: { status: "ready", can_save: true, can_initialize: false },
  live_tests: { api: false, llm: false },
  integrations: { environment: { finance: true, llm: true } },
} as unknown as system.SystemStatus;

const gap = {
  id: "b".repeat(32),
  dimension: "financial_statements.balance",
  fields: ["assets_total"],
  security_id: "600519.SH",
  period: "2024-FY",
  reason: "rate_limited",
  impact: "暂时未取得资产负债表，负债结构分析不可用。",
  blocking_for: [],
  remedies: ["稍后重试，避免短时间高频刷新。"],
};

const section = (overrides: Partial<ResearchReportView["sections"][number]>) => ({
  id: "facts",
  title: "事实概览",
  status: "supported" as const,
  summary: "已取得利润表与行情快照。",
  claims: [],
  counter_evidence: [],
  follow_up_questions: [],
  ...overrides,
});

const report: ResearchReportView = {
  schema_version: "finsight.report.v1",
  id: "r1".padEnd(32, "0"),
  version: 1,
  parent_report_id: null,
  task_id: "t1".padEnd(32, "0"),
  question: "600519 盈利质量如何？",
  scope: { thscode: "600519.SH" },
  model_snapshot: {
    provider: "deepseek",
    model: "deepseek-v4-flash",
    output_label: "8K",
    mode: "tools",
    json_mode: false,
  },
  created_at: "2026-09-01T10:00:00+08:00",
  evidence_ids: ["e1".padEnd(32, "0")],
  metrics: [
    {
      metric: "net_margin",
      value: "0.533",
      unit: "ratio",
      basis: "annual_report",
      formula: "net_profit / operating_income",
      evidence_ids: ["e1".padEnd(32, "0")],
      note: null,
    },
  ],
  sections: [
    section({
      id: "facts",
      status: "supported",
      claims: [
        {
          kind: "fact",
          text: "利润表数据显示盈利能力处于高位，数值以程序指标为准。",
          evidence_refs: ["e1".padEnd(32, "0")],
          metric_refs: ["net_margin"],
          gap_refs: [],
        },
      ],
    }),
    section({
      id: "price_valuation",
      title: "价格与估值",
      status: "unavailable",
      summary: "估值快照不可用，本节未生成结论。",
    }),
  ],
  data_gaps: [gap],
  coverage: "partial",
  output_integrity: "validated",
  limitations: ["部分来源暂时未取得数据，相关章节以条件性结论呈现。"],
};

const noEvidenceReport: ResearchReportView = {
  ...report,
  coverage: "no_business_evidence",
  output_integrity: "partial",
  metrics: [{ ...report.metrics[0], value: null }],
  sections: [
    section({
      id: "facts",
      status: "conditional",
      summary: "没有任何业务事实。",
      claims: [
        {
          kind: "inference",
          text: "在数据补齐前只能给条件性判断。",
          evidence_refs: [],
          metric_refs: [],
          gap_refs: [gap.id],
        },
      ],
      follow_up_questions: ["财报披露后利润率是否延续？"],
    }),
  ],
  data_gaps: [gap],
  limitations: ["没有可用模型，未完成 AI 分析；本报告仅包含本地事实、程序计算与缺口清单。"],
};

function makeTask(overrides: Partial<ResearchTaskView> = {}): ResearchTaskView {
  return {
    id: "t1".padEnd(32, "0"),
    question: "600519 盈利质量如何？",
    scope: { thscode: "600519.SH" },
    status: "completed",
    budget: {
      max_llm_requests: 24,
      max_fetches: 24,
      max_tool_rounds: 8,
      max_searches: 0,
      max_extractions: 0,
    },
    usage: { llm_requests: 7, fetches: 7, tool_rounds: 1, searches: 0, extractions: 0, reasoning_chars: 7 },
    report_id: "r1".padEnd(32, "0"),
    error: null,
    created_at: "2026-09-01T10:00:00+08:00",
    updated_at: "2026-09-01T10:01:00+08:00",
    finished_at: "2026-09-01T10:01:00+08:00",
    ...overrides,
  };
}

function makeSupplement(overrides: Partial<SupplementView> = {}): SupplementView {
  return {
    id: "s1".padEnd(32, "0"),
    report_id: report.id,
    task_id: report.task_id,
    kind: "user_provided",
    content: "管理层电话会记录：一季度营收指引保持两位数增长。",
    gap_ref: null,
    fields: { period: "2025Q1" },
    referenced_by_report_id: null,
    created_at: "2026-09-01T10:02:00+08:00",
    ...overrides,
  };
}

function show(path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderReport(current: ResearchReportView) {
  return render(<ResearchReport report={current} />);
}

/** 把一串公开进度事件喂给页面，模拟后端 SSE。 */
function scriptStream(events: ResearchStreamEvent[]) {
  vi.mocked(research.streamResearchEvents).mockImplementation(
    (_taskId: string, _after: number, handlers: { onEvent: (event: ResearchStreamEvent) => void }) => {
      for (const event of events) handlers.onEvent(event);
      return Promise.resolve();
    },
  );
}

const progressEvents: ResearchStreamEvent[] = [
  { type: "task.created", seq: 1 },
  { type: "phase", seq: 2, phase: "collect", detail: "正在收集结构化数据" },
  { type: "reasoning", seq: 3, chars: 7 },
  { type: "section", seq: 4, id: "facts", status: "supported", integrity: "validated" },
  { type: "section", seq: 5, id: "price_valuation", status: "unavailable", integrity: "missing" },
  { type: "report", seq: 6, report_id: report.id },
  { type: "usage", seq: 7, llm_requests: 7, fetches: 7, tool_rounds: 1, reasoning_chars: 7 },
  { type: "task.completed", seq: 8, report_id: report.id },
  { type: "end", status: "completed" },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(system.getSystemStatus).mockResolvedValue(systemStatus);
  vi.mocked(marketData.listIdentities).mockResolvedValue({ identities: [identity] });
  vi.mocked(research.getResearchTask).mockResolvedValue({
    task: makeTask(),
    last_seq: 8,
    events: [],
  });
  vi.mocked(research.getResearchReport).mockResolvedValue({ report, raw_sections: [] });
  vi.mocked(research.getReportConversation).mockResolvedValue({ conversation: { id: "c1", report_id: report.id }, messages: [] });
});

describe("ResearchReport + DataGaps 组件", () => {
  it("partial 报告默认呈现关键数据与结论", () => {
    renderReport(report);
    expect(screen.getByText("关键数据")).toBeInTheDocument();
    expect(screen.getByText("净利率")).toBeInTheDocument();
    expect(screen.getByText(/53.3%/)).toBeInTheDocument();
    expect(screen.getByText("事实概览")).toBeInTheDocument();
    expect(screen.getByText(/价格与估值/)).toBeInTheDocument();
    expect(screen.getByText("查看仍待核验的信息")).toBeInTheDocument();
  });

  it("缺口明细按需在核验详情中展开", async () => {
    const user = userEvent.setup();
    renderReport(report);
    await user.click(screen.getByText("查看仍待核验的信息"));
    expect(screen.getByText("数据缺口（1）")).toBeInTheDocument();
    expect(screen.getByText("仍待补齐或核验的信息")).toBeInTheDocument();
    expect(screen.getByText(/影响：暂时未取得资产负债表/)).toBeInTheDocument();
    expect(screen.getByText(/可以怎样补：稍后重试/)).toBeInTheDocument();
  });

  it("无业务事实报告仍给出条件框架与待验证事项，不显示 0 充数", () => {
    renderReport(noEvidenceReport);
    expect(screen.getByText("暂缺")).toBeInTheDocument();
    expect(screen.getByText(/条件性判断/)).toBeInTheDocument();
    expect(screen.getByText(/待验证事项/)).toBeInTheDocument();
    expect(screen.getByText(/没有可用模型/)).toBeInTheDocument();
  });

  it("格式异常不会把原始模型文本展示给用户", () => {
    renderReport({ ...report, output_integrity: "truncated" });
    expect(screen.queryByText(/"status":"supported","summ/)).not.toBeInTheDocument();
  });
});

describe("Research 页面流程", () => {
  it("发起任务后读取公开进度并展示报告与缺口", { timeout: 15000 }, async () => {
    const user = userEvent.setup();
    vi.mocked(research.createResearchTask).mockResolvedValue({
      task: makeTask({ status: "queued", report_id: null }),
    });
    scriptStream(progressEvents);
    show("/research");

    await screen.findByText("发起新研究");
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByText("贵州茅台（600519.SH）"));
    await user.type(screen.getByLabelText("研究问题"), "600519 盈利质量如何？");
    await user.click(screen.getByRole("button", { name: "开始研究" }));

    expect(research.createResearchTask).toHaveBeenCalledWith(
      expect.objectContaining({
        question: "600519 盈利质量如何？",
        thscode: "600519.SH",
        client_request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
      }),
    );
    expect(await screen.findByText("研究报告 · 600519 盈利质量如何？")).toBeInTheDocument();
    expect(screen.queryByText(/深度思考 7 字/)).not.toBeInTheDocument();
    await user.click(screen.getByText("查看仍待核验的信息"));
    expect(screen.getByText("仍待补齐或核验的信息")).toBeInTheDocument();
    expect(research.streamResearchEvents).toHaveBeenCalledWith("t1".padEnd(32, "0"), 0, expect.anything());
  });

  it("运行中的任务可取消，状态如实显示", { timeout: 15000 }, async () => {
    const user = userEvent.setup();
    vi.mocked(research.createResearchTask).mockResolvedValue({
      task: makeTask({ status: "running", report_id: null, finished_at: null }),
    });
    vi.mocked(research.cancelResearchTask).mockResolvedValue({
      task: makeTask({ status: "cancelled", report_id: null }),
    });
    // 流保持打开：模拟在途生成请求。
    vi.mocked(research.streamResearchEvents).mockReturnValue(new Promise(() => undefined));
    show("/research");

    await screen.findByText("发起新研究");
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByText("贵州茅台（600519.SH）"));
    await user.type(screen.getByLabelText("研究问题"), "600519 盈利质量如何？");
    await user.click(screen.getByRole("button", { name: "开始研究" }));

    await user.click(await screen.findByRole("button", { name: "取消任务" }));
    expect(research.cancelResearchTask).toHaveBeenCalledWith("t1".padEnd(32, "0"));
    expect(await screen.findByText("已取消")).toBeInTheDocument();
    expect(screen.queryByText(/研究报告 · /)).not.toBeInTheDocument();
  });

  it("补充保存后展示待分析状态，再分析携带所选补充并建立新任务", { timeout: 20000 }, async () => {
    const user = userEvent.setup();
    vi.mocked(research.createResearchTask).mockResolvedValue({
      task: makeTask({ status: "queued", report_id: null }),
    });
    scriptStream(progressEvents);
    vi.mocked(research.addSupplement).mockResolvedValue({ supplement: makeSupplement() });
    vi.mocked(research.reanalyzeReport).mockResolvedValue({
      task: makeTask({ id: "t2".padEnd(32, "0"), status: "queued", report_id: null }),
    });
    show("/research");

    await screen.findByText("发起新研究");
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByText("贵州茅台（600519.SH）"));
    await user.type(screen.getByLabelText("研究问题"), "600519 盈利质量如何？");
    await user.click(screen.getByRole("button", { name: "开始研究" }));
    await screen.findByText("研究报告 · 600519 盈利质量如何？");

    await user.type(screen.getByLabelText("补充内容"), "管理层电话会记录：一季度营收指引。");
    await user.click(screen.getByRole("button", { name: "保存补充" }));
    expect(await screen.findByText("已保存，尚未再分析")).toBeInTheDocument();
    expect(research.addSupplement).toHaveBeenCalledWith(report.id, {
      kind: "user_provided",
      content: "管理层电话会记录：一季度营收指引。",
    });

    const callsBefore = vi.mocked(research.streamResearchEvents).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "基于这些资料再分析" }));
    expect(research.reanalyzeReport).toHaveBeenCalledWith(
      report.id,
      expect.objectContaining({
        supplement_ids: ["s1".padEnd(32, "0")],
        client_request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
      }),
    );
    await waitFor(() =>
      expect(vi.mocked(research.streamResearchEvents).mock.calls.length).toBeGreaterThan(callsBefore),
    );
  });

  it("截断报告不展示后台原始片段", () => {
    renderReport({ ...report, output_integrity: "truncated" });
    expect(screen.queryByText("查看未通过结构校验的原始片段")).not.toBeInTheDocument();
  });
});
