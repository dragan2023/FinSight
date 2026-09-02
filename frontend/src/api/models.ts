import { request } from "./client";
import { postEventStream } from "./eventStream";

export type ProviderId = "custom" | "deepseek" | "glm" | "qwen";
export type Modality = "image" | "video" | "audio";
export interface ModelOptions {
  thinking: "enabled" | "disabled";
  reasoning_effort: "low" | "high" | "max" | null;
  thinking_budget: number | null;
  input_modalities: Modality[];
  region: "beijing" | "singapore";
  workspace_id: string;
  video_fps: number | null;
  image_detail: "auto" | "low" | "high" | "original" | null;
  context_limit: number | null;
  max_output: number | null;
}
export interface ModelCapability {
  id: string;
  name: string;
  thinking: boolean;
  thinking_required?: boolean;
  efforts: Array<"low" | "high" | "max">;
  budget: boolean;
  modalities: Modality[];
  stream_required: boolean;
  context: number | null;
  max_output: number;
  output_param: "max_tokens" | "max_completion_tokens";
  live_output_verified: boolean;
  limits_source: string;
}
export interface ProviderSpec {
  id: Exclude<ProviderId, "custom">;
  name: string;
  base_url: string;
  default_model: string;
  description: string;
  docs_url: string;
  models: ModelCapability[];
}
export interface ProviderCatalog {
  reviewed_at: string;
  providers: ProviderSpec[];
}
export const modalityLabels: Record<Modality, string> = {
  image: "图像",
  video: "视频",
  audio: "音频",
};
export function defaultOptions(model?: ModelCapability): ModelOptions {
  return {
    thinking: model?.thinking ? "enabled" : "disabled",
    reasoning_effort: null,
    thinking_budget: model?.budget ? 32768 : null,
    input_modalities: model?.modalities ?? [],
    region: "beijing",
    workspace_id: "",
    video_fps: null,
    image_detail: null,
    context_limit: null,
    max_output: null,
  };
}
export const getProviders = () =>
  request<ProviderCatalog>({ url: "/models/providers" });

export type OutputPolicy = "maximum" | "custom";
export interface EffectiveOutput {
  param: "max_tokens" | "max_completion_tokens";
  value: number;
  requested: number;
  policy: OutputPolicy;
  estimated_input_tokens: number | null;
  reason: string | null;
}
export interface ModelProfile {
  id: string;
  name: string;
  base_url: string;
  model: string;
  provider: ProviderId;
  options: ModelOptions;
  temperature: number;
  max_tokens: number;
  output_policy: OutputPolicy;
  legacy_max_tokens: number | null;
  effective_output: EffectiveOutput;
  timeout: number;
  active: boolean;
  key_status: "configured" | "empty" | "locked";
  masked_api_key: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileInput {
  provider: ProviderId;
  options: ModelOptions;
  initialize_encryption?: boolean;
  name: string;
  base_url?: string;
  model: string;
  api_key?: string;
  clear_api_key: boolean;
  temperature: number;
  max_tokens: number;
  output_policy: OutputPolicy;
  timeout: number;
  active: boolean;
}

export type ProbeMode = "connectivity" | "chat" | "tools";
export interface ProbeResult {
  mode: ProbeMode;
  supported: boolean;
  message: string;
  model_label: string;
}
export const getProfiles = () => request<ModelProfile[]>({ url: "/models" });
export const saveProfile = (data: ProfileInput, id?: string) =>
  request<ModelProfile>({
    url: id ? `/models/${id}` : "/models",
    method: id ? "PUT" : "POST",
    data,
  });
export const activateProfile = (id: string) =>
  request<ModelProfile>({
    url: `/models/${id}/activate`,
    method: "POST",
    data: {},
  });
export const deleteProfile = (id: string) =>
  request<void>({ url: `/models/${id}`, method: "DELETE", data: {} });
export const probeModel = (mode: ProbeMode, id?: string) =>
  request<ProbeResult>({
    url: id ? `/models/${id}/test` : "/models/test",
    method: "POST",
    data: { mode, confirm_external: true },
  });

export interface ChatInput {
  prompt: string;
  media?: { kind: Modality; url: string; audio_format?: "wav" | "mp3" };
}
export interface ChatResult {
  content: string;
  model_label: string;
  truncated: boolean;
}
export const chatModel = (data: ChatInput, id?: string) =>
  request<ChatResult>({
    url: id ? `/models/${id}/chat` : "/models/chat",
    method: "POST",
    data: { ...data, confirm_external: true },
  });

// -- streaming (stage 01): one merged protocol shared with stage 03 tasks ---

export interface StreamMeta {
  type: "meta";
  model_label: string;
  output: EffectiveOutput;
}
export interface StreamDelta {
  type: "delta";
  content: string;
}
export interface StreamReasoning {
  type: "reasoning";
  chars: number;
}
export interface StreamDone {
  type: "done";
  content: string;
  finish: "stop" | "length" | null;
  truncated: boolean;
  abnormal: boolean;
  message?: string;
}
export interface StreamError {
  type: "error";
  category: string;
  message: string;
}
export type ModelStreamEvent =
  | StreamMeta
  | StreamDelta
  | StreamReasoning
  | StreamDone
  | StreamError;

export function streamChat(
  data: ChatInput,
  handlers: {
    signal?: AbortSignal;
    onEvent: (event: ModelStreamEvent) => void;
  },
  id?: string,
): Promise<void> {
  return postEventStream<ModelStreamEvent>(
    id ? `/api/models/${id}/chat/stream` : "/api/models/chat/stream",
    { ...data, confirm_external: true },
    handlers,
  );
}
