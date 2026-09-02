import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ApiError } from "../api/client";
import * as models from "../api/models";
import * as system from "../api/system";
import providerCatalog from "./provider-catalog.json";
import type { SystemStatus } from "../api/system";

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
  chatModel: vi.fn(),
  streamChat: vi.fn(),
  getProfiles: vi.fn(),
  saveProfile: vi.fn(),
  activateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  probeModel: vi.fn(),
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
const profile: models.ModelProfile = {
  id: "offline-profile",
  provider: "custom",
  options: models.defaultOptions(),
  name: "研究模型",
  base_url: "https://model.example/v1",
  model: "offline-model",
  temperature: 0.2,
  max_tokens: 2048,
  output_policy: "custom",
  legacy_max_tokens: null,
  effective_output: {
    param: "max_tokens",
    value: 2048,
    requested: 2048,
    policy: "custom",
    estimated_input_tokens: 0,
    reason: null,
  },
  timeout: 30,
  active: true,
  key_status: "configured",
  masked_api_key: "••••••••",
  created_at: "2026-08-31",
  updated_at: "2026-08-31",
};

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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(system.getSystemStatus).mockResolvedValue(status);
  vi.mocked(models.getProfiles).mockResolvedValue([]);
  vi.mocked(models.getProviders).mockResolvedValue(
    providerCatalog as unknown as models.ProviderCatalog,
  );
});

describe("local workspace", () => {
  it("shows honest no-data state and navigates to settings", async () => {
    const user = userEvent.setup();
    show();
    expect(await screen.findByText("已就绪")).toBeInTheDocument();
    expect(screen.getByText("可配置")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "配置研究模型" }));
    expect(
      await screen.findByText("选择官方预设，只需填写 API Key"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "添加第一个模型" })).toBeEnabled(),
    );
    expect(screen.getByRole("button", { name: "开始测试" })).toBeDisabled();
    expect(models.probeModel).not.toHaveBeenCalled();
  });

  it("displays actionable service errors without pretending it is ready", async () => {
    vi.mocked(system.getSystemStatus).mockRejectedValue(
      new ApiError("无法连接本机服务", "network", "test-request", true),
    );
    show();
    expect(await screen.findByText("无法连接本机服务")).toBeInTheDocument();
    expect(screen.getByText("未连接")).toBeInTheDocument();
    expect(screen.getByText(/test-request/)).toBeInTheDocument();
  });

  it("keeps an existing key unchanged on blank edit and never writes browser storage", async () => {
    vi.mocked(system.getSystemStatus).mockResolvedValue({
      ...status,
      encryption: { status: "ready", can_save: true, can_initialize: false },
    });
    vi.mocked(models.getProfiles).mockResolvedValue([profile]);
    vi.mocked(models.saveProfile).mockResolvedValue({
      ...profile,
      name: "已更名",
    });
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    show("/settings");
    await user.click(await screen.findByRole("button", { name: "编辑" }));
    const dialog = await screen.findByRole("dialog");
    const password = within(dialog).getByLabelText("API Key", {
      exact: false,
      selector: 'input[type="password"]',
    });
    expect(password).toHaveValue("");
    const name = within(dialog).getByLabelText("配置名称", { exact: false });
    await user.clear(name);
    await user.type(name, "已更名");
    await user.click(within(dialog).getByRole("button", { name: "保存配置" }));
    await waitFor(() =>
      expect(models.saveProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "已更名",
          api_key: undefined,
          clear_api_key: false,
        }),
        profile.id,
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("validates required fields and drops credential field on cancel", async () => {
    vi.mocked(system.getSystemStatus).mockResolvedValue({
      ...status,
      encryption: { status: "ready", can_save: true, can_initialize: false },
    });
    const user = userEvent.setup();
    show("/settings");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "添加第一个模型" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "添加第一个模型" }));
    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByText("请输入 API Key")).toBeInTheDocument();
    expect(models.saveProfile).not.toHaveBeenCalled();
    await user.type(
      screen.getByLabelText("API Key", { exact: false }),
      "test-only-disposable",
    );
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "添加第一个模型" }));
    expect(screen.getByLabelText("API Key", { exact: false })).toHaveValue("");
  });

  it("requires confirmation before a single test request", async () => {
    vi.mocked(system.getSystemStatus).mockResolvedValue({
      ...status,
      model: {
        ...status.model,
        source: "environment",
        status: "ready",
        model_label: "offline-model",
      },
      live_tests: { api: false, llm: true },
    });
    vi.mocked(models.probeModel).mockResolvedValue({
      mode: "connectivity",
      supported: true,
      message: "模型列表端点可访问",
      model_label: "offline-model",
    });
    const user = userEvent.setup();
    show("/settings");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "开始测试" })).toBeEnabled(),
    );
    expect(models.probeModel).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "开始测试" }));
    expect(models.probeModel).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "执行测试" }));
    expect(await screen.findByText("模型列表端点可访问")).toBeInTheDocument();
    expect(models.probeModel).toHaveBeenCalledTimes(1);
    expect(models.probeModel).toHaveBeenCalledWith("chat", undefined);
  });
});

it.each([
  ["DeepSeek", "deepseek", "deepseek-v4-flash-vision-exp"],
  ["GLM · 智谱", "glm", "glm-5.3-flash"],
  ["Qwen · 通义千问", "qwen", "qwen3.8-flash"],
])(
  "saves %s official defaults with only a key",
  async (label, provider, model) => {
    const user = userEvent.setup();
    vi.mocked(models.saveProfile).mockResolvedValue(profile);
    show("/settings");
    const button = await screen.findByRole("button", { name: `配置 ${label}` });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("API Key", { exact: false }),
      "offline-disposable",
    );
    await user.click(within(dialog).getByRole("button", { name: "保存配置" }));
    await waitFor(() =>
      expect(models.saveProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          provider,
          model,
          api_key: "offline-disposable",
          base_url: undefined,
          initialize_encryption: true,
          options: expect.objectContaining({
            thinking: "enabled",
            input_modalities: expect.arrayContaining(["image"]),
          }),
        }),
        undefined,
      ),
    );
    expect(models.chatModel).not.toHaveBeenCalled();
    expect(models.probeModel).not.toHaveBeenCalled();
  },
);

it("locks GLM thinking and updates Qwen modes when switching to Omni", async () => {
  const user = userEvent.setup();
  show("/settings");
  await user.click(
    await screen.findByRole("button", { name: "配置 GLM · 智谱" }),
  );
  expect(screen.getByLabelText("思考模式", { exact: false })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "视频" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "取消" }));
  await user.click(
    screen.getByRole("button", { name: "配置 Qwen · 通义千问" }),
  );
  expect(
    screen.getByLabelText("思考预算（Token）", { exact: false }),
  ).toHaveValue("32768");
  await user.click(screen.getByLabelText("模型", { exact: true }));
  await user.click(await screen.findByText("Qwen3.5 Omni Plus · 全模态"));
  expect(screen.getByLabelText("思考模式", { exact: false })).toBeDisabled();
  expect(
    screen.queryByLabelText("思考预算（Token）", { exact: false }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "音频" })).toBeChecked();
  expect(screen.getByText("必须流式 · 仅文本输出")).toBeInTheDocument();
});

it("sends a multimodal trial only after explicit consent and renders output as text", async () => {
  const user = userEvent.setup();
  const official: models.ModelProfile = {
    ...profile,
    provider: "glm",
    model: "glm-5.3-flash",
    options: {
      ...models.defaultOptions(),
      thinking: "enabled",
      input_modalities: ["image", "video"],
    },
  };
  vi.mocked(models.getProfiles).mockResolvedValue([official]);
  vi.mocked(system.getSystemStatus).mockResolvedValue({
    ...status,
    model: { ...status.model, status: "ready", active_profile_id: official.id },
  });
  vi.mocked(models.streamChat).mockImplementation(async (_data, handlers) => {
    handlers.onEvent({
      type: "meta",
      model_label: official.model,
      output: official.effective_output,
    });
    handlers.onEvent({ type: "delta", content: "<script>untrusted" });
    handlers.onEvent({ type: "delta", content: "</script>" });
    handlers.onEvent({
      type: "done",
      content: "<script>untrusted</script>",
      finish: "stop",
      truncated: false,
      abnormal: false,
    });
  });
  show("/settings");
  await screen.findByText("研究模型");
  const send = screen.getByRole("button", { name: "发送试用请求" });
  expect(send).toBeDisabled();
  await user.click(screen.getByLabelText("输入类型"));
  await user.click(await screen.findByText("图像 + 文本"));
  await user.type(
    screen.getByLabelText("图像 HTTPS URL", { exact: false }),
    "https://public.example/chart.png",
  );
  await user.click(screen.getByRole("checkbox", { name: /我确认将提示词/ }));
  await user.click(send);
  await waitFor(() =>
    expect(models.streamChat).toHaveBeenCalledWith(
      {
        prompt: "请用一句话介绍你自己。",
        media: { kind: "image", url: "https://public.example/chart.png" },
      },
      expect.objectContaining({ onEvent: expect.any(Function) }),
      undefined,
    ),
  );
  expect(
    await screen.findByText("<script>untrusted</script>"),
  ).toBeInTheDocument();
  expect(document.querySelector(".reply-text script")).toBeNull();
  expect(models.streamChat).toHaveBeenCalledTimes(1);
  expect(models.chatModel).not.toHaveBeenCalled();
});
