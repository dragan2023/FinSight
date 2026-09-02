import { useRef, useState } from "react";
import { Alert, Button, Checkbox, Form, Input, Select, Space, Tag } from "antd";
import { modalityLabels, streamChat } from "../../api/models";
import type {
  ChatInput,
  EffectiveOutput,
  ModelProfile,
  Modality,
  ModelStreamEvent,
} from "../../api/models";
import ErrorNotice from "../../components/ErrorNotice";
import type { ApiError } from "../../api/client";

interface RunState {
  content: string;
  modelLabel: string | null;
  output: EffectiveOutput | null;
  reasoningChars: number;
  done: null | {
    content: string;
    truncated: boolean;
    abnormal: boolean;
    message?: string;
  };
}

function asApiError(error: unknown, fallback: string): ApiError {
  if (error instanceof Error && error.name === "ApiError") {
    return error as ApiError;
  }
  return {
    name: "ApiError",
    message: fallback,
    category: "stream",
    request_id: null,
    retryable: true,
  } as ApiError;
}

export default function ModelPlayground({
  profiles,
  ready,
  activeId,
}: {
  profiles: ModelProfile[];
  ready: boolean;
  activeId: string | null;
}) {
  const [form] = Form.useForm<ChatInput>();
  const [target, setTarget] = useState("active");
  const [kind, setKind] = useState<Modality | "text">("text");
  const [confirmed, setConfirmed] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [run, setRun] = useState<RunState>({
    content: "",
    modelLabel: null,
    output: null,
    reasoningChars: 0,
    done: null,
  });
  const abortRef = useRef<AbortController | null>(null);
  const selected = profiles.find(
    (profile) => profile.id === (target === "active" ? activeId : target),
  );
  const targetReady = selected
    ? selected.key_status === "configured"
    : target === "active" && ready;

  const reset = () => {
    setError(null);
    setRun({ content: "", modelLabel: null, output: null, reasoningChars: 0, done: null });
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  };

  const send = async (values: ChatInput) => {
    if (!confirmed || !targetReady) return;
    const controller = new AbortController();
    abortRef.current = controller;
    reset();
    setRunning(true);
    let failure: ApiError | null = null;
    try {
      await streamChat(
        {
          prompt: values.prompt.trim(),
          ...(kind !== "text" && values.media
            ? { media: { ...values.media, kind } }
            : {}),
        },
        {
          signal: controller.signal,
          onEvent: (event: ModelStreamEvent) => {
            if (event.type === "error") {
              failure = {
                name: "ApiError",
                message: event.message,
                category: event.category,
                request_id: null,
                retryable: false,
              } as ApiError;
              return;
            }
            setRun((previous) => {
              switch (event.type) {
                case "meta":
                  return { ...previous, modelLabel: event.model_label, output: event.output };
                case "delta":
                  return { ...previous, content: previous.content + event.content };
                case "reasoning":
                  return { ...previous, reasoningChars: previous.reasoningChars + event.chars };
                default:
                  return { ...previous, done: { ...event } };
              }
            });
          },
        },
        target === "active" ? undefined : target,
      );
    } catch (requestError: unknown) {
      if (!controller.signal.aborted) {
        failure = asApiError(requestError, "与模型服务的流式连接中断。");
      }
    } finally {
      abortRef.current = null;
      setRunning(false);
      if (failure) setError(failure);
    }
  };

  return (
    <section className="probe-panel playground">
      <div className="section-heading">
        <h2>模型试用</h2>
        <Tag variant="filled">流式输出 · 不保存对话</Tag>
      </div>
      <p>
        直接验证当前参数和多模态输入，回复以流式方式逐步显示。思考进度只显示字数，
        不显示思考正文，也不会进入任何日志或存储。不执行工具、不进行投资研究流程。
      </p>
      <Form
        form={form}
        layout="vertical"
        disabled={running}
        onFinish={(values) => {
          void send(values);
        }}
        initialValues={{
          prompt: "请用一句话介绍你自己。",
          media: { audio_format: "wav" },
        }}
      >
        <div className="playground-controls">
          <Select
            aria-label="试用模型"
            value={target}
            disabled={running}
            onChange={(value) => {
              setTarget(value);
              setKind("text");
              setConfirmed(false);
              form.setFieldValue(["media", "url"], "");
              stop();
              reset();
            }}
            options={[
              { value: "active", label: "当前活动模型" },
              ...profiles.map((profile) => ({
                value: profile.id,
                label: profile.name,
              })),
            ]}
          />
          <Select
            aria-label="输入类型"
            value={kind}
            disabled={running}
            onChange={(value) => {
              setKind(value);
              setConfirmed(false);
              stop();
              reset();
            }}
            options={[
              { value: "text", label: "纯文本" },
              ...(selected?.options.input_modalities ?? []).map((value) => ({
                value,
                label: modalityLabels[value] + " + 文本",
              })),
            ]}
          />
        </div>
        <Form.Item
          name="prompt"
          label="提示词"
          rules={[
            { required: true, whitespace: true, message: "请输入提示词" },
          ]}
        >
          <Input.TextArea
            rows={3}
            maxLength={4000}
            showCount
            onChange={() => {
              stop();
              reset();
            }}
          />
        </Form.Item>
        {kind !== "text" && (
          <>
            <Form.Item
              name={["media", "url"]}
              label={modalityLabels[kind] + " HTTPS URL"}
              rules={[
                { required: true, message: "请输入素材 URL" },
                {
                  validator: async (_, value: string) => {
                    if (!value) return;
                    try {
                      const url = new URL(value);
                      if (
                        url.protocol !== "https:" ||
                        url.username ||
                        url.password ||
                        url.hash
                      )
                        throw new Error();
                    } catch {
                      throw new Error("请输入不含凭据与片段的 HTTPS URL");
                    }
                  },
                },
              ]}
              extra="本页支持单个公开素材 URL；由模型服务商读取，本机不下载、不预览。请仅提交有权分享的素材，不支持本地文件上传。"
            >
              <Input
                maxLength={4096}
                autoComplete="off"
                placeholder="https://…"
                onChange={() => {
                  setConfirmed(false);
                  stop();
                  reset();
                }}
              />
            </Form.Item>
            {kind === "audio" && (
              <Form.Item name={["media", "audio_format"]} label="音频格式">
                <Select
                  options={[
                    { value: "wav", label: "WAV" },
                    { value: "mp3", label: "MP3" },
                  ]}
                />
              </Form.Item>
            )}
          </>
        )}
        <Space orientation="vertical" size="middle">
          <Checkbox
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          >
            我确认将提示词及素材 URL 发送至所选服务商，调用可能计费。
          </Checkbox>
          <Space>
            <Button
              type="primary"
              htmlType="submit"
              disabled={!confirmed || !targetReady || running}
            >
              发送试用请求
            </Button>
            {running && (
              <Button danger onClick={stop}>
                停止生成
              </Button>
            )}
          </Space>
        </Space>
      </Form>
      {running && (
        <p role="status" className="probe-hint">
          模型正在生成，思考或多模态输入可能耗时较长；停止生成会关闭上游请求且不自动重放。
        </p>
      )}
      {error && (
        <div className="form-notice">
          <ErrorNotice error={error} />
        </div>
      )}
      {(run.content || run.done || run.output) && (
        <div className="model-reply" aria-live="polite">
          <div className="section-heading">
            <h3>模型回复</h3>
            <span>
              {run.modelLabel}
              {run.output
                ? " · " +
                  run.output.param +
                  " " +
                  run.output.value +
                  "（" +
                  (run.output.policy === "maximum" ? "最大输出" : "自定义") +
                  "）"
                : ""}
            </span>
          </div>
          {run.reasoningChars > 0 && (
            <p className="probe-hint">
              思考进行中：已生成 {run.reasoningChars} 字符（正文不显示）。
            </p>
          )}
          {run.done?.abnormal && (
            <Alert type="error" title={run.done.message ?? "本次回复未正常完成。"} />
          )}
          {run.done && !run.done.abnormal && run.done.truncated && (
            <Alert type="warning" title="输出达到上限，以下回复不完整。" />
          )}
          <div className="reply-text">{run.done ? run.done.content : run.content}</div>
        </div>
      )}
    </section>
  );
}
