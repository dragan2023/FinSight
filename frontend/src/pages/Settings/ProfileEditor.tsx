import { useState } from "react";
import {
  Alert,
  Checkbox,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Tag,
} from "antd";
import { defaultOptions, modalityLabels, saveProfile } from "../../api/models";
import type {
  ModelProfile,
  OutputPolicy,
  ProfileInput,
  ProviderId,
  ProviderSpec,
} from "../../api/models";
import ErrorNotice from "../../components/ErrorNotice";

interface Props {
  profile?: ModelProfile;
  first: boolean;
  providers: ProviderSpec[];
  providerId: ProviderId;
  initializeEncryption: boolean;
  suggestedName: string;
  onClose: () => void;
  onSaved: () => void;
}

export default function ProfileEditor({
  profile,
  first,
  providers,
  providerId,
  initializeEncryption,
  suggestedName,
  onClose,
  onSaved,
}: Props) {
  const [form] = Form.useForm<ProfileInput>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const provider =
    Form.useWatch("provider", form) ?? profile?.provider ?? providerId;
  const spec = providers.find((item) => item.id === provider);
  const modelId =
    Form.useWatch("model", form) ?? profile?.model ?? spec?.default_model;
  const capability = spec?.models.find((item) => item.id === modelId);
  const options =
    Form.useWatch("options", form) ??
    profile?.options ??
    defaultOptions(capability);
  const clear = Form.useWatch("clear_api_key", form);
  const outputPolicyWatch = Form.useWatch("output_policy", form);
  const maxTokensWatch = Form.useWatch("max_tokens", form);
  const outputPolicy: OutputPolicy =
    outputPolicyWatch ?? profile?.output_policy ?? (provider === "custom" ? "custom" : "maximum");
  const custom = provider === "custom";
  const declaredOutput = options.max_output;
  const customMax =
    outputPolicy === "custom"
      ? custom
        ? (declaredOutput ?? 393216)
        : (capability?.max_output ?? 393216)
      : undefined;
  const budgetLimit =
    outputPolicy === "maximum"
      ? (custom ? declaredOutput : capability?.max_output)
      : (maxTokensWatch ?? profile?.max_tokens);
  const budgetMax = budgetLimit ? Math.max(1024, budgetLimit - 16384) : 114688;
  const officialEndpoint =
    provider !== "qwen"
      ? spec?.base_url
      : options.workspace_id
        ? `https://${options.workspace_id}.${options.region === "singapore" ? "ap-southeast-1" : "cn-beijing"}.maas.aliyuncs.com/compatible-mode/v1`
        : `https://${options.region === "singapore" ? "dashscope-intl" : "dashscope"}.aliyuncs.com/compatible-mode/v1`;
  const initialSpec = providers.find(
    (item) => item.id === (profile?.provider ?? providerId),
  );
  const initialModel = initialSpec?.models.find(
    (item) => item.id === initialSpec.default_model,
  );

  const submit = async (values: ProfileInput) => {
    setSaving(true);
    setError(null);
    try {
      await saveProfile(
        {
          ...values,
          base_url: custom ? values.base_url?.trim() : undefined,
          initialize_encryption: initializeEncryption,
          api_key: values.clear_api_key
            ? undefined
            : values.api_key?.trim() || undefined,
        },
        profile?.id,
      );
      form.resetFields();
      onSaved();
    } catch (failure: unknown) {
      setError(failure);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={profile ? "编辑模型配置" : "添加模型配置"}
      onCancel={saving ? undefined : onClose}
      onOk={() => form.submit()}
      okText="保存配置"
      cancelText="取消"
      confirmLoading={saving}
      closable={!saving}
      mask={{ closable: !saving }}
      cancelButtonProps={{ disabled: saving }}
      width={680}
      style={{ top: 32 }}
      styles={{
        body: { maxHeight: "70vh", overflowY: "auto", paddingRight: 8 },
      }}
    >
      <Form
        form={form}
        disabled={saving}
        layout="vertical"
        onFinish={() => {
          void submit(form.getFieldsValue(true));
        }}
        requiredMark="optional"
        initialValues={{
          provider: profile?.provider ?? providerId,
          name: profile?.name ?? suggestedName,
          base_url: profile?.base_url ?? "",
          model: profile?.model ?? initialSpec?.default_model ?? "",
          options: profile?.options ?? defaultOptions(initialModel),
          api_key: "",
          clear_api_key: false,
          temperature: profile?.temperature ?? (providerId === "glm" ? 1 : 0.2),
          max_tokens: profile?.max_tokens ?? 8192,
          output_policy: profile?.output_policy ?? (providerId === "custom" ? "custom" : "maximum"),
          timeout: profile?.timeout ?? 120,
          active: profile?.active ?? first,
        }}
      >
        {error ? (
          <div className="form-notice">
            <ErrorNotice error={error} />
          </div>
        ) : null}
        <Form.Item
          name="provider"
          label="模型服务商"
          rules={[{ required: true }]}
        >
          <Select
            options={[
              ...providers.map((item) => ({
                value: item.id,
                label: item.name,
              })),
              { value: "custom", label: "自定义 OpenAI 兼容接口" },
            ]}
            onChange={(value: ProviderId) => {
              const next = providers.find((item) => item.id === value);
              const nextModel = next?.models.find(
                (item) => item.id === next.default_model,
              );
              form.setFieldsValue({
                name: next?.name ?? "",
                base_url: "",
                model: next?.default_model ?? "",
                api_key: "",
                clear_api_key: false,
                options: defaultOptions(nextModel),
                temperature: value === "glm" ? 1 : 0.2,
                output_policy: value === "custom" ? "custom" : "maximum",
              });
            }}
          />
        </Form.Item>
        {spec && (
          <div className="provider-description">
            <p>{spec.description}</p>
            <a href={spec.docs_url} target="_blank" rel="noreferrer">
              查看官方能力文档 ↗
            </a>
          </div>
        )}
        <Form.Item
          name="model"
          label="模型"
          rules={[
            { required: true, whitespace: true, message: "请输入模型 ID" },
          ]}
        >
          {custom ? (
            <Input placeholder="服务商提供的完整模型 ID" maxLength={150} />
          ) : (
            <Select
              options={spec?.models.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              onChange={(value) => {
                const next = spec?.models.find((item) => item.id === value);
                form.setFieldValue("options", {
                  ...defaultOptions(next),
                  region: options.region,
                  workspace_id: options.workspace_id,
                });
              }}
            />
          )}
        </Form.Item>
        {capability && (
          <Space wrap className="capability-tags">
            <Tag>文本</Tag>
            {capability.modalities.map((item) => (
              <Tag key={item} color="blue">
                {modalityLabels[item]}理解
              </Tag>
            ))}
            <Tag>工具调用</Tag>
            {capability.stream_required && (
              <Tag color="purple">必须流式 · 仅文本输出</Tag>
            )}
          </Space>
        )}
        <Form.Item
          name="api_key"
          label="API Key"
          required={!profile}
          rules={[
            {
              validator: async (_, value: string) => {
                if (!profile && !value?.trim())
                  throw new Error("请输入 API Key");
              },
            },
          ]}
          extra={
            profile
              ? "留空保留原凭据；更换官方地域、业务空间或服务商时须重新填写。"
              : "只需填写密钥，其他参数已预置。凭据加密保存，不写入浏览器存储。"
          }
        >
          <Input.Password
            disabled={clear}
            autoComplete="new-password"
            maxLength={4096}
            placeholder={
              profile?.key_status === "configured"
                ? "已配置 · 留空不修改"
                : "输入 API Key"
            }
          />
        </Form.Item>
        {profile && (
          <Form.Item name="clear_api_key" valuePropName="checked">
            <Checkbox onChange={() => form.setFieldValue("api_key", "")}>
              显式清除已保存的 API Key（需先关闭激活）
            </Checkbox>
          </Form.Item>
        )}
        {!custom && (
          <div className="model-mode-box">
            <Form.Item
              name={["options", "thinking"]}
              label="思考模式"
              extra={
                capability?.thinking_required
                  ? "官方限制：此模型始终开启思考，可通过推理强度调整。"
                  : !capability?.thinking
                    ? "此模型不支持思考。上游强制流式传输，页面在完成后显示文本回复。"
                    : provider === "deepseek"
                      ? "开启后不发送 Temperature；页面只显示最终回复。"
                      : "开启后会增加思考耗时与 Token 消耗；页面只显示最终回复。"
              }
            >
              <Select
                disabled={!capability?.thinking || capability.thinking_required}
                options={[
                  { value: "enabled", label: "开启思考" },
                  { value: "disabled", label: "关闭思考" },
                ]}
                onChange={(value) => {
                  if (value === "disabled")
                    form.setFieldsValue({
                      options: {
                        reasoning_effort: null,
                        thinking_budget: null,
                      },
                    });
                }}
              />
            </Form.Item>
            {!!capability?.efforts.length && (
              <Form.Item
                name={["options", "reasoning_effort"]}
                label="推理强度"
                extra={
                  provider === "glm"
                    ? "留空使用官方默认 max。"
                    : "留空使用官方默认 high。"
                }
              >
                <Select
                  allowClear
                  disabled={options.thinking !== "enabled"}
                  placeholder="使用官方默认"
                  options={capability.efforts.map((value) => ({
                    value,
                    label: value,
                  }))}
                  onClear={() =>
                    form.setFieldValue(["options", "reasoning_effort"], null)
                  }
                />
              </Form.Item>
            )}
            {capability?.budget && (
              <Form.Item
                name={["options", "thinking_budget"]}
                label="思考预算（Token）"
                extra={
                  budgetLimit
                    ? `思考预算必须为正文保留至少 16384 Token；当前上限下最大 ${budgetMax}。留空使用官方默认 32768。`
                    : "仅限制推理部分，完整输出上限在高级设置中配置；留空使用官方默认。"
                }
              >
                <InputNumber
                  disabled={options.thinking !== "enabled"}
                  min={1}
                  max={budgetMax}
                  precision={0}
                />
              </Form.Item>
            )}
            {!!capability?.modalities.length && (
              <Form.Item
                name={["options", "input_modalities"]}
                label="允许的多模态输入"
                extra="这是本机输入许可；请求会转换为官方内容格式，不向服务商发送虚构的功能开关。"
              >
                <Checkbox.Group
                  options={capability.modalities.map((value) => ({
                    value,
                    label: modalityLabels[value],
                  }))}
                  onChange={(values) => {
                    if (!values.includes("video"))
                      form.setFieldValue(["options", "video_fps"], null);
                    if (!values.includes("image"))
                      form.setFieldValue(["options", "image_detail"], null);
                  }}
                />
              </Form.Item>
            )}
            {provider === "deepseek" &&
              options.input_modalities.includes("image") && (
                <Form.Item
                  name={["options", "image_detail"]}
                  label="图像精度"
                  extra="low 会缩小图像以节省 Token；其余模式保留原始尺寸。"
                >
                  <Select
                    allowClear
                    placeholder="官方默认 auto"
                    options={["auto", "low", "high", "original"].map(
                      (value) => ({ value, label: value }),
                    )}
                    onClear={() =>
                      form.setFieldValue(["options", "image_detail"], null)
                    }
                  />
                </Form.Item>
              )}
            {provider === "qwen" &&
              capability?.budget &&
              options.input_modalities.includes("video") && (
                <Form.Item
                  name={["options", "video_fps"]}
                  label="视频抽帧频率（FPS）"
                  extra="0.1–10；留空使用官方默认 2。更高频率会增加输入 Token。"
                >
                  <InputNumber min={0.1} max={10} step={0.1} />
                </Form.Item>
              )}
          </div>
        )}
        <Collapse
          className="advanced-model-settings"
          defaultActiveKey={custom ? ["advanced"] : []}
          items={[
            {
              key: "advanced",
              label: "高级设置 · 名称、地址、输出与地域",
              forceRender: true,
              children: (
                <>
                  <Form.Item
                    name="name"
                    label="配置名称"
                    rules={[
                      {
                        required: true,
                        whitespace: true,
                        message: "请输入配置名称",
                      },
                    ]}
                  >
                    <Input maxLength={80} />
                  </Form.Item>
                  {custom ? (
                    <Form.Item
                      name="base_url"
                      label="服务地址"
                      rules={[
                        {
                          required: true,
                          whitespace: true,
                          message: "请输入服务地址",
                        },
                      ]}
                      extra="远端须为 HTTPS；只允许本机 HTTP。地址不得包含凭据或查询参数。"
                    >
                      <Input
                        placeholder="https://your-provider.example/v1"
                        maxLength={500}
                      />
                    </Form.Item>
                  ) : (
                    <p className="endpoint">
                      官方地址自动配置。{officialEndpoint}
                    </p>
                  )}
                  {provider === "qwen" && (
                    <>
                      <Form.Item
                        name={["options", "region"]}
                        label="百炼地域"
                        extra="北京与新加坡密钥不通用，系统不会自动切换地域。"
                      >
                        <Select
                          options={[
                            { value: "beijing", label: "华北 2 · 北京" },
                            { value: "singapore", label: "新加坡" },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item
                        name={["options", "workspace_id"]}
                        label="业务空间 ID"
                        extra="可选；填写后使用该地域的业务空间专属 maas 地址；留空使用仍受支持的 DashScope 地址。"
                        rules={[
                          {
                            pattern:
                              /^([a-z0-9][a-z0-9-]{0,61}[a-z0-9]|[a-z0-9])?$/,
                            message: "请输入有效的小写业务空间 ID",
                          },
                        ]}
                      >
                        <Input maxLength={63} />
                      </Form.Item>
                    </>
                  )}
                  <Form.Item
                    name="output_policy"
                    label="输出策略"
                    extra="本次升级默认采用官方文档最大输出：长回复更完整，但可能增加费用与耗时。"
                  >
                    <Radio.Group
                      options={[
                        {
                          value: "maximum",
                          label: custom ? "最大输出（需已填写服务商上限）" : "最大输出（官方目录）",
                        },
                        { value: "custom", label: "自定义上限" },
                      ]}
                    />
                  </Form.Item>
                  {outputPolicy === "maximum" && !custom && capability && (
                    <p className="muted">
                      官方文档输出上限 {capability.max_output} Token，请求参数
                      {capability.output_param}
                      {capability.live_output_verified
                        ? "；整数边界已通过真实调用验证。"
                        : "；整数边界尚未经真实调用验证，以服务商实际接受为准。"}
                    </p>
                  )}
                  {outputPolicy === "maximum" && custom && (
                    <div className="form-grid">
                      <Form.Item
                        name={["options", "max_output"]}
                        label="服务商输出上限（Token）"
                        rules={[{ required: true, message: "启用最大输出前请填写服务商明确的上限" }]}
                        extra="自定义接口无法推断上限；未填写时保持自定义策略。"
                      >
                        <InputNumber min={1024} max={393216} precision={0} />
                      </Form.Item>
                      <Form.Item
                        name={["options", "context_limit"]}
                        label="上下文上限（Token，可选）"
                        extra="用于在输入较长时保留输出空间；不确定可留空。"
                      >
                        <InputNumber min={1024} max={2097152} precision={0} />
                      </Form.Item>
                    </div>
                  )}
                  <div className="form-grid">
                    <Form.Item
                      name="temperature"
                      label="Temperature"
                      rules={[{ required: true }]}
                      extra={
                        provider === "deepseek" &&
                        options.thinking === "enabled"
                          ? "思考时不发送"
                          : undefined
                      }
                    >
                      <InputNumber
                        min={0}
                        max={provider === "glm" ? 1 : 2}
                        step={0.1}
                        disabled={
                          provider === "deepseek" &&
                          options.thinking === "enabled"
                        }
                      />
                    </Form.Item>
                    {outputPolicy === "custom" ? (
                      <Form.Item
                        name="max_tokens"
                        label="自定义输出 Token"
                        rules={[{ required: true }]}
                        extra={
                          !custom && capability
                            ? `不能超过官方上限 ${capability.max_output}。`
                            : undefined
                        }
                      >
                        <InputNumber min={64} max={customMax} precision={0} />
                      </Form.Item>
                    ) : (
                      <Form.Item name="max_tokens" hidden>
                        <InputNumber />
                      </Form.Item>
                    )}
                    <Form.Item
                      name="timeout"
                      label="测试超时（秒）"
                      rules={[{ required: true }]}
                      extra="仅用于手动连接测试；长回复生成不再受此总时限约束。"
                    >
                      <InputNumber min={5} max={120} />
                    </Form.Item>
                  </div>
                  {profile?.legacy_max_tokens != null &&
                    outputPolicy === "maximum" &&
                    profile.legacy_max_tokens !== profile.max_tokens && (
                      <p className="muted">
                        迁移说明：原自定义上限 {profile.legacy_max_tokens} Token
                        已保留，可切换回“自定义上限”恢复。
                      </p>
                    )}
                </>
              ),
            },
          ]}
        />
        <Form.Item name="active" label="设为活动模型" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Alert
          type="info"
          showIcon
          title="保存不会触发外部请求"
          description={
            initializeEncryption
              ? "点击保存将初始化本机加密存储并写入配置，无需重启。请私下备份项目 .env（包含加密根密钥），勿对外分享。"
              : "保存后可在下方试用与验证。密钥、配额、地域和模型权限仍由服务商校验。"
          }
        />
      </Form>
    </Modal>
  );
}
