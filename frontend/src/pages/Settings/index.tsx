import { useState } from "react";
import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  ImportOutlined,
  LockOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Empty,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Tabs,
  Tag,
} from "antd";
import {
  activateProfile,
  deleteProfile,
  getProfiles,
  getProviders,
  modalityLabels,
  probeModel,
} from "../../api/models";
import type {
  ModelProfile,
  ProbeMode,
  ProbeResult,
  ProviderId,
} from "../../api/models";
import { importEnvironment } from "../../api/apiConfig";
import { systemQuery } from "../../api/system";
import ErrorNotice from "../../components/ErrorNotice";
import ProfileEditor from "./ProfileEditor";
import ModelPlayground from "./ModelPlayground";
import ApiProviders from "./ApiProviders";

function ModelServiceTab() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const system = useQuery(systemQuery);
  const profiles = useQuery({ queryKey: ["models"], queryFn: getProfiles });
  const catalog = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    staleTime: Infinity,
  });
  const [newProvider, setNewProvider] = useState<ProviderId>("deepseek");
  const [editor, setEditor] = useState<ModelProfile | "new" | null>(null);
  const [mode, setMode] = useState<ProbeMode>("chat");
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [testTarget, setTestTarget] = useState("active");
  const importLlm = useMutation({
    mutationFn: () => importEnvironment("llm"),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["models"] }),
        queryClient.invalidateQueries({ queryKey: ["system"] }),
      ]);
      void message.success("模型环境配置已加密导入，尚未激活");
    },
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["models"] }),
      queryClient.invalidateQueries({ queryKey: ["system"] }),
    ]);
  };
  const change = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "activate" | "delete";
    }) => {
      if (action === "activate") await activateProfile(id);
      else await deleteProfile(id);
    },
    onSuccess: async () => {
      setProbeResult(null);
      setTestTarget("active");
      await refresh();
      void message.success("模型配置已更新");
    },
  });
  const probe = useMutation({
    mutationFn: () =>
      probeModel(mode, testTarget === "active" ? undefined : testTarget),
    onMutate: () => setProbeResult(null),
    onSuccess: setProbeResult,
  });
  const writable = Boolean(
    system.data?.encryption.can_save || system.data?.encryption.can_initialize,
  );
  const providers = catalog.data?.providers ?? [];
  const proposedName =
    providers.find((item) => item.id === newProvider)?.name ?? "自定义模型";
  let suggestedName = proposedName;
  for (
    let suffix = 2;
    profiles.data?.some((item) => item.name === suggestedName);
    suffix++
  )
    suggestedName = `${proposedName} ${suffix}`;
  const ready = system.data?.model.status === "ready";
  const targetReady =
    testTarget === "active"
      ? ready
      : profiles.data?.some(
          (profile) =>
            profile.id === testTarget && profile.key_status === "configured",
        );
  const llmImportable = Boolean(system.data?.integrations.environment.llm);

  return (
    <>
      <div className="section-heading">
        <h2>模型服务</h2>
        <span>能力核验：{catalog.data?.reviewed_at ?? "加载中"}</span>
      </div>
      {importLlm.error && <ErrorNotice error={importLlm.error} />}
      {llmImportable && (
        <Alert
          className="settings-alert"
          type="info"
          showIcon
          title="检测到后端环境配置，可导入本机配置"
          description="导入会读取 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY 并加密保存为普通模型配置，不返回明文；导入后仍需手动激活才会在研究中使用。"
          action={
            <Button
              icon={<ImportOutlined aria-hidden />}
              loading={importLlm.isPending}
              onClick={() => importLlm.mutate()}
            >
              导入模型环境配置
            </Button>
          }
        />
      )}
      {system.error && (
        <ErrorNotice
          error={system.error}
          retry={() => {
            void system.refetch();
          }}
        />
      )}
      {system.data && !writable && (
        <Alert
          className="settings-alert"
          type="warning"
          showIcon
          title={
            system.data.encryption.status === "invalid"
              ? "加密根密钥无效，配置保存已禁用"
              : "先初始化本机密钥存储"
          }
          description={
            <div>
              <p>
                首次使用请在项目根目录主动运行下方命令，然后重启后端。它不会显示密钥，也不会覆盖已有加密记录。
              </p>
              <code>
                .\\.venv\\Scripts\\python.exe -m scripts.init_local_secrets
              </code>
              <p>
                若已有加密配置，请恢复原
                FINSIGHT_ENCRYPTION_KEY；不要生成替代密钥。
              </p>
            </div>
          }
        />
      )}
      {system.data?.encryption.can_initialize && (
        <Alert
          className="settings-alert"
          type="info"
          showIcon
          title="选择官方预设，只需填写 API Key"
          description="首次保存会安全初始化本机加密存储，无需命令或重启。保存不会调用模型；已有加密记录时不会生成替代根密钥。"
        />
      )}
      <div className="provider-grid">
        {providers.map((provider) => (
          <section
            key={provider.id}
            className={`provider-card provider-${provider.id}`}
          >
            <div className="profile-title">
              <h3>{provider.name}</h3>
              <Tag variant="filled">官方 API</Tag>
            </div>
            <div className="provider-model-id">{provider.default_model}</div>
            <p>{provider.description}</p>
            <Button
              disabled={
                !writable || profiles.isPending || Boolean(profiles.error)
              }
              onClick={() => {
                setNewProvider(provider.id);
                setEditor("new");
              }}
            >
              配置 {provider.name}
            </Button>
          </section>
        ))}
      </div>
      <section className="settings-summary">
        <div className="summary-symbol">
          <ApiOutlined aria-hidden />
        </div>
        <div>
          <span className="muted">当前活动模型</span>
          <h2>{system.data?.model.model_label || "尚未选择模型"}</h2>
          <p>
            模型只在保存并激活本机加密配置后可用；没有隐藏的环境回退。
            {system.data?.integrations.environment.llm
              ? "检测到后端环境变量，可点击上方导入。"
              : ""}
          </p>
        </div>
        <Tag color={ready ? "success" : "default"} variant="filled">
          {ready ? "配置就绪 · 尚需手动测试" : "配置未就绪"}
        </Tag>
      </section>
      <div className="section-heading">
        <h2>
          已保存的配置{" "}
          <span className="count-label">{profiles.data?.length ?? 0} / 12</span>
        </h2>
        <span>
          <LockOutlined aria-hidden /> 凭据仅在本机后端解密
        </span>
      </div>
      {profiles.error && (
        <ErrorNotice
          error={profiles.error}
          retry={() => {
            void profiles.refetch();
          }}
        />
      )}
      {change.error && <ErrorNotice error={change.error} />}
      {profiles.isPending ? (
        <Skeleton active />
      ) : !profiles.error && !profiles.data?.length ? (
        <section className="empty-models">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <>
                <h3>还没有模型配置</h3>
                <p>
                  选择上方官方客户端，填入 API Key 即可保存。
                  <br />
                  保存后，可分别验证连接、聊天与工具调用能力。
                </p>
              </>
            }
          />
          <Button
            disabled={!writable}
            onClick={() => {
              setNewProvider(providers.length ? "deepseek" : "custom");
              setEditor("new");
            }}
            icon={<PlusOutlined aria-hidden />}
          >
            添加第一个模型
          </Button>
        </section>
      ) : (
        <div className="profiles-list">
          {profiles.data?.map((profile) => (
            <section className="profile-card" key={profile.id}>
              <div className="profile-main">
                <div className="profile-title">
                  <h3>{profile.name}</h3>
                  {profile.active && (
                    <Tag color="success" variant="filled">
                      活动配置
                    </Tag>
                  )}
                  <Tag
                    color={
                      profile.key_status === "locked" ? "error" : "default"
                    }
                    variant="filled"
                  >
                    {profile.key_status === "configured"
                      ? "凭据已加密"
                      : profile.key_status === "locked"
                        ? "凭据不可解密"
                        : "未设置凭据"}
                  </Tag>
                  <Tag variant="filled">
                    {profile.output_policy === "maximum"
                      ? `最大输出 ${profile.effective_output.value} · ${profile.effective_output.param}`
                      : `自定义输出 ${profile.effective_output.value}`}
                  </Tag>
                </div>
                <p className="model-id">{profile.model}</p>
                <p className="endpoint">{profile.base_url}</p>
                <div className="profile-meta">
                  <span>
                    {profile.provider === "custom"
                      ? "自定义兼容接口"
                      : providers.find((item) => item.id === profile.provider)
                          ?.name}
                  </span>
                  <span>
                    {profile.provider === "custom"
                      ? "模式由兼容服务决定"
                      : profile.options.thinking === "enabled"
                        ? `思考开启 · ${profile.options.reasoning_effort ?? "官方默认强度"}`
                        : "思考关闭"}
                  </span>
                  {profile.options.input_modalities.map((item) => (
                    <span key={item}>{modalityLabels[item]}输入</span>
                  ))}
                  <span>
                    {profile.provider === "deepseek" &&
                    profile.options.thinking === "enabled"
                      ? "Temperature 不发送"
                      : `Temperature ${profile.temperature}`}
                  </span>
                  {profile.legacy_max_tokens !== null &&
                    profile.output_policy === "maximum" &&
                    profile.legacy_max_tokens !== profile.max_tokens && (
                      <span>迁移自旧上限 {profile.legacy_max_tokens}</span>
                    )}
                  <span>测试超时 {profile.timeout}s</span>
                </div>
              </div>
              <Space wrap>
                <Button
                  disabled={
                    profile.active ||
                    profile.key_status !== "configured" ||
                    !writable ||
                    change.isPending
                  }
                  onClick={() =>
                    change.mutate({ id: profile.id, action: "activate" })
                  }
                >
                  激活
                </Button>
                <Button
                  icon={<EditOutlined aria-hidden />}
                  disabled={!writable || change.isPending}
                  onClick={() => setEditor(profile)}
                >
                  编辑
                </Button>
                <Popconfirm
                  title={`删除「${profile.name}」？`}
                  description={
                    profile.active
                      ? "活动配置将被删除，不会自动激活其他配置。"
                      : "本机保存的配置和凭据将被删除。"
                  }
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() =>
                    change.mutate({ id: profile.id, action: "delete" })
                  }
                >
                  <Button
                    danger
                    aria-label={`删除 ${profile.name}`}
                    icon={<DeleteOutlined aria-hidden />}
                    disabled={change.isPending}
                  />
                </Popconfirm>
              </Space>
            </section>
          ))}
        </div>
      )}
      <ModelPlayground
        key={(profiles.data ?? [])
          .map((item) => `${item.id}:${item.updated_at}`)
          .join("|")}
        profiles={profiles.data ?? []}
        ready={ready}
        activeId={system.data?.model.active_profile_id ?? null}
      />
      <section className="probe-panel">
        <div className="section-heading">
          <h2>手动连接测试</h2>
          <Tag variant="filled">不自动发起请求</Tag>
        </div>
        <p>
          每次只测试所选的一项能力。聊天和工具测试可能产生服务商费用，模型列表可访问不代表研究能力可用。探针参数不会限制研究输出。
        </p>
        <Space wrap>
          <Select
            aria-label="测试目标"
            disabled={probe.isPending}
            className="target-select"
            value={testTarget}
            onChange={(value) => {
              setTestTarget(value);
              setProbeResult(null);
              probe.reset();
            }}
            options={[
              { value: "active", label: "当前活动配置" },
              ...(profiles.data ?? []).map((profile) => ({
                value: profile.id,
                label: profile.name,
              })),
            ]}
          />
          <Select
            aria-label="测试类型"
            disabled={probe.isPending}
            value={mode}
            className="mode-select"
            onChange={(value) => {
              setMode(value);
              setProbeResult(null);
              probe.reset();
            }}
            options={[
              { value: "connectivity", label: "连通性（模型列表）" },
              { value: "chat", label: "聊天响应" },
              { value: "tools", label: "结构化工具调用" },
            ]}
          />
          <Popconfirm
            title="执行一次外部连接测试？"
            description="请求将发送到所选模型服务；聊天和工具测试可能计费。"
            okText="执行测试"
            cancelText="取消"
            onConfirm={() => probe.mutate()}
          >
            <Button
              icon={<ApiOutlined aria-hidden />}
              disabled={!targetReady}
              loading={probe.isPending}
            >
              开始测试
            </Button>
          </Popconfirm>
        </Space>
        <p className="probe-hint">
          手动确认后即可调用，无需环境开关。GLM
          请使用聊天测试；思考模式按配置的输出预算计费，不会强行压缩至 256
          Token。
        </p>
        {probe.error && (
          <div className="form-notice">
            <ErrorNotice error={probe.error} />
          </div>
        )}
        {probeResult && (
          <Alert
            className="form-notice"
            type={probeResult.supported ? "success" : "warning"}
            showIcon
            title={probeResult.message}
            description={`本次测试模型：${probeResult.model_label}`}
          />
        )}
      </section>
      <div className="security-note">
        <SafetyCertificateOutlined aria-hidden />
        <span>
          API Key 只写不读，不保存在 localStorage 或
          sessionStorage，也不会包含在报告中。
        </span>
      </div>
      {editor && (
        <ProfileEditor
          profile={editor === "new" ? undefined : editor}
          first={!profiles.data?.length}
          providers={providers}
          providerId={newProvider}
          suggestedName={suggestedName}
          initializeEncryption={system.data?.encryption.can_initialize ?? false}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            setProbeResult(null);
            void refresh();
            void message.success("模型配置已安全保存");
          }}
        />
      )}
    </>
  );
}

export default function Settings() {
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">MODEL &amp; API SETTINGS</div>
          <h1>模型与 API 配置</h1>
          <p>
            统一管理研究使用的模型、金融数据与联网搜索凭据；所有 Key 都由你本人提供并加密保存在本机。
          </p>
        </div>
      </div>
      <Tabs
        defaultActiveKey="models"
        items={[
          { key: "models", label: "模型服务", children: <ModelServiceTab />, forceRender: true },
          { key: "finance", label: "金融数据", children: <ApiProviders category="finance_data" /> },
          { key: "search", label: "联网搜索", children: <ApiProviders category="web_search" /> },
          {
            key: "local",
            label: "数据使用与本地存储",
            children: (
              <>
                <Alert
                  className="settings-alert"
                  type="info"
                  showIcon
                  title="本地附件与受限公开原文读取将在阶段 04 接入"
                  description="届时此页显示数据流向、存储与外发开关：附件原文独立留源，未经确认不外发到搜索或模型服务商之外的位置。"
                />
                <ApiProviders category="local" />
              </>
            ),
          },
        ]}
      />
    </>
  );
}
