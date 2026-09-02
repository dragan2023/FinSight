import { useState } from "react";
import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  ImportOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Popconfirm, Skeleton, Space, Tag } from "antd";
import {
  deleteIntegrationProfile,
  getIntegrations,
  getIntegrationProfiles,
  importEnvironment,
  statusLabels,
} from "../../api/apiConfig";
import type {
  IntegrationCategory,
  IntegrationProfile,
  IntegrationSpec,
  ProfileStatus,
} from "../../api/apiConfig";
import ErrorNotice from "../../components/ErrorNotice";
import ApiProviderEditor from "./ApiProviderEditor";

const categoryTitles: Record<IntegrationCategory, { title: string; hint: string }> = {
  finance_data: {
    title: "金融数据",
    hint: "结构化行情、估值与财务数据来源；搜索结果不能替代这些数据。",
  },
  web_search: {
    title: "联网搜索",
    hint: "用于公告、监管与经营解释的网页发现与正文提取；默认不发送私人材料。",
  },
  local: {
    title: "本地解析",
    hint: "无 Key 的本地能力；显示数据流向、存储与外发开关。",
  },
};

const statusColors: Record<ProfileStatus, string> = {
  unconfigured: "default",
  saved_unverified: "processing",
  available: "success",
  permission_denied: "warning",
  quota_limited: "warning",
  expired: "warning",
  locked: "error",
  not_implemented: "default",
};

export default function ApiProviders({ category }: { category: IntegrationCategory }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const catalog = useQuery({
    queryKey: ["integrations"],
    queryFn: getIntegrations,
    staleTime: Infinity,
  });
  const profiles = useQuery({
    queryKey: ["integration-profiles"],
    queryFn: getIntegrationProfiles,
  });
  const [editor, setEditor] = useState<
    { spec: IntegrationSpec; profile?: IntegrationProfile } | null
  >(null);
  const writable = catalog.isSuccess;
  const remove = useMutation({
    mutationFn: (profile: IntegrationProfile) =>
      deleteIntegrationProfile(profile.id, profile.credential_version),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["integration-profiles"] });
      void message.success("服务配置已删除");
    },
  });
  const importFinance = useMutation({
    mutationFn: () => importEnvironment("finance"),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["integration-profiles"] });
      void message.success("环境配置已加密导入");
    },
  });
  const specs = (catalog.data?.providers ?? []).filter(
    (item) => item.category === category,
  );
  const byProvider = new Map<string, IntegrationProfile[]>();
  for (const profile of profiles.data ?? []) {
    byProvider.set(profile.provider, [
      ...(byProvider.get(profile.provider) ?? []),
      profile,
    ]);
  }
  const heading = categoryTitles[category];
  const financeImportable =
    category === "finance_data" &&
    Boolean(catalog.data?.environment.finance) &&
    !(byProvider.get("hexin_finance") ?? []).some(
      (profile) => profile.key_status === "configured",
    );

  return (
    <div>
      <div className="section-heading">
        <h2>{heading.title}</h2>
        <span>能力核验：{catalog.data?.reviewed_at ?? "加载中"}</span>
      </div>
      <p className="probe-hint">{heading.hint}</p>
      {catalog.error && (
        <ErrorNotice
          error={catalog.error}
          retry={() => {
            void catalog.refetch();
          }}
        />
      )}
      {profiles.error && <ErrorNotice error={profiles.error} />}
      {remove.error && <ErrorNotice error={remove.error} />}
      {importFinance.error && <ErrorNotice error={importFinance.error} />}
      {financeImportable && (
        <Alert
          className="settings-alert"
          type="info"
          showIcon
          title="检测到后端环境配置，可导入本机配置"
          description="导入会读取 HITHINK_FINANCE_API_KEY 并加密保存，只显示来源与脱敏状态，不返回明文；导入前不会自动使用该环境 Key。"
          action={
            <Button
              icon={<ImportOutlined aria-hidden />}
              loading={importFinance.isPending}
              onClick={() => importFinance.mutate()}
            >
              导入环境配置
            </Button>
          }
        />
      )}
      {profiles.isPending || catalog.isPending ? (
        <Skeleton active />
      ) : (
        <div className="provider-grid">
          {specs.map((spec) => {
            const list = byProvider.get(spec.id) ?? [];
            const planned = spec.implementation === "planned";
            return (
              <section key={spec.id} className={"provider-card provider-" + spec.id}>
                <div className="profile-title">
                  <h3>{spec.name}</h3>
                  {planned ? (
                    <Tag variant="filled">计划接入 · 阶段 {spec.stage}</Tag>
                  ) : (
                    <Tag color="success" variant="filled">已接入</Tag>
                  )}
                </div>
                <p>{spec.purpose}</p>
                <p className="probe-hint">{spec.notes}</p>
                <p className="probe-hint">{spec.credential.hint}</p>
                {(spec.docs_url || spec.apply_url) && (
                  <p>
                    {spec.docs_url && (
                      <a href={spec.docs_url} target="_blank" rel="noreferrer">
                        官方文档 ↗
                      </a>
                    )}
                    {spec.docs_url && spec.apply_url ? " · " : ""}
                    {spec.apply_url && (
                      <a href={spec.apply_url} target="_blank" rel="noreferrer">
                        申请入口 ↗
                      </a>
                    )}
                  </p>
                )}
                <div className="capability-tags">
                  {spec.capabilities.map((capability) => (
                    <Tag key={capability.id}>{capability.name}</Tag>
                  ))}
                </div>
                {list.length === 0 ? (
                  <Button
                    disabled={!writable || spec.credential.kind === "none"}
                    onClick={() => setEditor({ spec })}
                  >
                    {spec.credential.kind === "none" ? "无需凭据" : "配置凭据"}
                  </Button>
                ) : (
                  list.map((profile) => (
                    <div key={profile.id} className="integration-profile">
                      <div className="profile-title">
                        <strong>{profile.name}</strong>
                        <Tag color={statusColors[profile.status]} variant="filled">
                          {statusLabels[profile.status]}
                        </Tag>
                        <Tag variant="filled">
                          {profile.key_status === "configured"
                            ? "凭据已加密 " + (profile.masked_api_key ?? "")
                            : profile.key_status === "locked"
                              ? "凭据不可解密"
                              : "未设置凭据"}
                        </Tag>
                        {!profile.enabled && <Tag>已停用</Tag>}
                      </div>
                      <p className="probe-hint">
                        配置版本 v{profile.credential_version}
                        {profile.tests.length
                          ? " · 最后测试 " +
                            profile.tests[0].tested_at.slice(0, 19).replace("T", " ")
                          : " · 尚无能力测试记录"}
                      </p>
                      <Space wrap>
                        <Button
                          icon={<EditOutlined aria-hidden />}
                          disabled={!writable}
                          onClick={() => setEditor({ spec, profile })}
                        >
                          编辑
                        </Button>
                        <Popconfirm
                          title={"删除「" + profile.name + "」？"}
                          description="本机保存的服务凭据将被删除；进行中的外部请求可能已计费。"
                          okText="删除"
                          cancelText="取消"
                          onConfirm={() => remove.mutate(profile)}
                        >
                          <Button
                            danger
                            aria-label={"删除 " + profile.name}
                            icon={<DeleteOutlined aria-hidden />}
                            disabled={remove.isPending}
                          />
                        </Popconfirm>
                      </Space>
                    </div>
                  ))
                )}
              </section>
            );
          })}
        </div>
      )}
      <div className="security-note">
        <ApiOutlined aria-hidden />
        <span>
          适配器未实现的服务显示“计划接入”，调用与测试保持禁用，不会伪装连接成功。
        </span>
      </div>
      {editor && (
        <ApiProviderEditor
          spec={editor.spec}
          profile={editor.profile}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            void message.success("服务凭据已安全保存");
          }}
        />
      )}
    </div>
  );
}
