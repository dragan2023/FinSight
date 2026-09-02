import { useState } from "react";
import { Checkbox, Form, Input, Modal, Switch } from "antd";
import { saveIntegrationProfile } from "../../api/apiConfig";
import type { IntegrationProfile, IntegrationProfileInput, IntegrationSpec } from "../../api/apiConfig";
import ErrorNotice from "../../components/ErrorNotice";

interface Props {
  spec: IntegrationSpec;
  profile?: IntegrationProfile;
  initializeEncryption?: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export default function ApiProviderEditor({
  spec,
  profile,
  initializeEncryption = false,
  onClose,
  onSaved,
}: Props) {
  const [form] = Form.useForm<IntegrationProfileInput>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const clear = Form.useWatch("clear_api_key", form);

  const submit = async (values: IntegrationProfileInput) => {
    setSaving(true);
    setError(null);
    try {
      await saveIntegrationProfile(
        {
          provider: spec.id,
          name: values.name?.trim(),
          api_key: values.clear_api_key ? undefined : values.api_key?.trim() || undefined,
          clear_api_key: values.clear_api_key ?? false,
          enabled: values.enabled ?? true,
          credential_version: profile?.credential_version,
          initialize_encryption: initializeEncryption && !profile,
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
      title={profile ? `编辑 ${spec.name}` : `配置 ${spec.name}`}
      onCancel={saving ? undefined : onClose}
      onOk={() => form.submit()}
      okText="保存凭据"
      cancelText="取消"
      confirmLoading={saving}
      closable={!saving}
      mask={{ closable: !saving }}
      cancelButtonProps={{ disabled: saving }}
      width={560}
      style={{ top: 32 }}
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
          name: profile?.name ?? spec.name,
          api_key: "",
          clear_api_key: false,
          enabled: profile?.enabled ?? true,
        }}
      >
        {error ? <div className="form-notice"><ErrorNotice error={error} /></div> : null}
        <p className="probe-hint">{spec.credential.hint}</p>
        <Form.Item
          name="name"
          label="配置名称"
          rules={[{ required: true, whitespace: true, message: "请输入配置名称" }]}
        >
          <Input maxLength={80} />
        </Form.Item>
        <Form.Item
          name="api_key"
          label={spec.credential.label}
          required={!profile}
          rules={[
            {
              validator: async (_, value: string) => {
                if (!profile && !value?.trim()) throw new Error(`请输入${spec.credential.label}`);
              },
            },
          ]}
          extra={
            profile
              ? "留空保留原凭据；显式清除才会删除。凭据加密保存，不回显、不写入浏览器存储。"
              : "凭据加密保存到本机数据库；保存不会发起外部请求。"
          }
        >
          <Input.Password
            disabled={clear}
            autoComplete="new-password"
            maxLength={4096}
            placeholder={
              profile?.key_status === "configured" ? "已配置 · 留空不修改" : `输入${spec.credential.label}`
            }
          />
        </Form.Item>
        {profile && (
          <Form.Item name="clear_api_key" valuePropName="checked">
            <Checkbox onChange={() => form.setFieldValue("api_key", "")}>
              显式清除已保存的凭据
            </Checkbox>
          </Form.Item>
        )}
        <Form.Item name="enabled" label="启用此服务" valuePropName="checked" extra="停用后适配器不得再使用该凭据发起外部请求。">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  );
}
