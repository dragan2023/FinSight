import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import * as apiConfig from "../api/apiConfig";
import * as models from "../api/models";
import * as system from "../api/system";
import type { SystemStatus } from "../api/system";
import providerCatalog from "./provider-catalog.json";

vi.mock("../api/system", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/system")>();
  const getSystemStatus = vi.fn();
  return {
    ...original,
    getSystemStatus,
    systemQuery: { queryKey: ["system"], queryFn: getSystemStatus },
  };
});

vi.mock("../api/models", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/models")>()),
  getProviders: vi.fn(),
  getProfiles: vi.fn(),
  saveProfile: vi.fn(),
  activateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  probeModel: vi.fn(),
  streamChat: vi.fn(),
  chatModel: vi.fn(),
}));

vi.mock("../api/apiConfig", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/apiConfig")>()),
  getIntegrations: vi.fn(),
  getIntegrationProfiles: vi.fn(),
  saveIntegrationProfile: vi.fn(),
  deleteIntegrationProfile: vi.fn(),
  importEnvironment: vi.fn(),
}));

const status: SystemStatus = {
  app: "FinSight",
  version: "0.1.0",
  stage: "foundation",
  local_only: true,
  database: "ready",
  market_data: { status: "not_implemented", key_configured: false },
  model: {
    source: "none",
    status: "model_unconfigured",
    model_label: null,
    active_profile_id: null,
    profile_count: 0,
  },
  encryption: { status: "missing", can_save: false, can_initialize: true },
  live_tests: { api: false, llm: false },
  integrations: { environment: { finance: false, llm: false } },
};

const integrationCatalog: apiConfig.IntegrationCatalog = {
  reviewed_at: "2026-08-31",
  environment: { finance: false, llm: false },
  providers: [
    {
      id: "hexin_finance",
      category: "finance_data",
      name: "同花顺金融数据",
      purpose: "结构化行情、估值与财务数据。",
      docs_url: "https://docs.example/hexin",
      apply_url: "https://apply.example/hexin",
      credential: { kind: "api_key", label: "API Key", hint: "X-api-key 头部传输。" },
      implementation: "planned",
      stage: "02",
      capabilities: [
        { id: "prices", name: "行情与估值" },
        { id: "financials", name: "财务报表" },
      ],
      notes: "阶段 02 接入适配器。",
    },
    {
      id: "tushare",
      category: "finance_data",
      name: "Tushare Pro",
      purpose: "历史行情与基本面数据。",
      docs_url: "https://docs.example/tushare",
      apply_url: "https://apply.example/tushare",
      credential: { kind: "token", label: "Token", hint: "请求体携带 token。" },
      implementation: "planned",
      stage: "04",
      capabilities: [{ id: "prices", name: "行情与估值" }],
      notes: "阶段 04 接入。",
    },
    {
      id: "bocha",
      category: "web_search",
      name: "博查搜索",
      purpose: "网页搜索发现。",
      docs_url: "https://docs.example/bocha",
      apply_url: "https://apply.example/bocha",
      credential: { kind: "api_key", label: "API Key", hint: "Authorization 头部。" },
      implementation: "planned",
      stage: "04",
      capabilities: [{ id: "search", name: "网页搜索" }],
      notes: "阶段 04 接入。",
    },
    {
      id: "tavily",
      category: "web_search",
      name: "Tavily",
      purpose: "网页搜索与正文提取。",
      docs_url: "https://docs.example/tavily",
      apply_url: "https://apply.example/tavily",
      credential: { kind: "api_key", label: "API Key", hint: "Authorization 头部。" },
      implementation: "planned",
      stage: "04",
      capabilities: [
        { id: "search", name: "网页搜索" },
        { id: "extract", name: "正文提取" },
      ],
      notes: "阶段 04 接入。",
    },
    {
      id: "local_sources",
      category: "local",
      name: "本地附件解析",
      purpose: "本地 PDF/文本解析，不经外部服务。",
      docs_url: "",
      apply_url: "",
      credential: { kind: "none", label: "无需凭据", hint: "本地能力，不需要 Key。" },
      implementation: "planned",
      stage: "04",
      capabilities: [{ id: "parse", name: "本地解析" }],
      notes: "阶段 04 提供数据流开关。",
    },
  ],
};

const savedProfile: apiConfig.IntegrationProfile = {
  id: "int-1",
  provider: "hexin_finance",
  name: "同花顺金融数据",
  enabled: true,
  options: {},
  credential_version: 2,
  key_status: "configured",
  masked_api_key: "••••••••",
  status: "saved_unverified",
  implementation: "planned",
  tests: [],
  created_at: "2026-08-31T08:00:00Z",
  updated_at: "2026-08-31T08:00:00Z",
};

function show(path = "/settings") {
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

beforeEach(() => {
  vi.mocked(system.getSystemStatus).mockResolvedValue(status);
  vi.mocked(models.getProviders).mockResolvedValue(
    providerCatalog as unknown as models.ProviderCatalog,
  );
  vi.mocked(models.getProfiles).mockResolvedValue([]);
  vi.mocked(apiConfig.getIntegrations).mockResolvedValue(integrationCatalog);
  vi.mocked(apiConfig.getIntegrationProfiles).mockResolvedValue([]);
});

describe("unified settings for models and API credentials", () => {
  it("lists finance and search integrations with planned status and disabled key flow for local", async () => {
    const user = userEvent.setup();
    show();
    await screen.findByRole("heading", { name: "模型与 API 配置", level: 1 });
    await user.click(await screen.findByRole("tab", { name: "金融数据" }));
    const finance = await screen.findByText("同花顺金融数据");
    expect(finance).toBeInTheDocument();
    expect(screen.getAllByText("计划接入 · 阶段 02").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tushare Pro").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("能力核验：2026-08-31").length,
    ).toBeGreaterThanOrEqual(2);

    await user.click(screen.getByRole("tab", { name: "联网搜索" }));
    expect(await screen.findAllByText("博查搜索")).toBeTruthy();
    expect(screen.getAllByText("Tavily").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: "数据使用与本地存储" }));
    expect(
      await screen.findAllByText("本地附件解析"),
    ).toBeTruthy();
    expect(screen.getAllByText("无需凭据").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("API Key", { exact: false })).toBeNull();
  });

  it("never echoes a saved integration key and keeps editing disabled state honest", async () => {
    const user = userEvent.setup();
    vi.mocked(apiConfig.getIntegrationProfiles).mockResolvedValue([savedProfile]);
    show();
    await screen.findByRole("heading", { name: "模型与 API 配置", level: 1 });
    await user.click(await screen.findByRole("tab", { name: "金融数据" }));
    const card = (
      await screen.findAllByText("同花顺金融数据")
    )[0].closest("section") as HTMLElement;
    expect(
      within(card).getByText(/凭据已加密 ••••••••/),
    ).toBeInTheDocument();
    expect(within(card).getByText("已保存未验证")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "编辑" }));
    const keyInput = await screen.findByLabelText(/^API Key/);
    expect(keyInput).toHaveValue("");
    expect(await screen.findByText(/留空保留原凭据/)).toBeInTheDocument();
  });

  it("shows the upgraded output policy with cost notice and capability-bound maximums", async () => {
    const user = userEvent.setup();
    show();
    await screen.findByRole("heading", { name: "模型与 API 配置", level: 1 });
    await user.click(
      await screen.findByRole("button", { name: "配置 DeepSeek" }),
    );
    await user.click(await screen.findByText("高级设置 · 名称、地址、输出与地域"));
    expect(
      screen.getByText(/本次升级默认采用官方文档最大输出：长回复更完整，但可能增加费用与耗时/),
    ).toBeInTheDocument();
    const maximumRadio = screen.getByRole("radio", { name: "最大输出（官方目录）" });
    expect(maximumRadio).toBeChecked();
    expect(screen.getByText(/官方文档输出上限 393216 Token/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "自定义上限" }));
    expect(
      await screen.findByLabelText("自定义输出 Token"),
    ).toHaveAttribute("aria-valuemax", "393216");
    expect(screen.getByText("不能超过官方上限 393216。")).toBeInTheDocument();
  });

  it("bounds thinking budget to leave room for the answer", async () => {
    const user = userEvent.setup();
    show();
    await screen.findByRole("heading", { name: "模型与 API 配置", level: 1 });
    await user.click(
      await screen.findByRole("button", { name: "配置 Qwen · 通义千问" }),
    );
    const budget = await screen.findByLabelText("思考预算（Token）", {
      exact: false,
    });
    expect(budget).toHaveAttribute("aria-valuemax", "114688");
    expect(screen.getByText(/当前上限下最大 114688/)).toBeInTheDocument();
  });
});
