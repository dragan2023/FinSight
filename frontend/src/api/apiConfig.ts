import { request } from "./client";

export type IntegrationCategory = "finance_data" | "web_search" | "local";
export type ProfileStatus =
  | "unconfigured"
  | "saved_unverified"
  | "available"
  | "permission_denied"
  | "quota_limited"
  | "expired"
  | "locked"
  | "not_implemented";

export interface IntegrationCapability {
  id: string;
  name: string;
}
export interface IntegrationSpec {
  id: string;
  category: IntegrationCategory;
  name: string;
  purpose: string;
  docs_url: string;
  apply_url: string;
  credential: { kind: "api_key" | "token" | "none"; label: string; hint: string };
  implementation: "planned" | "available";
  stage: string;
  capabilities: IntegrationCapability[];
  notes: string;
}
export interface IntegrationCatalog {
  reviewed_at: string;
  environment: { finance: boolean; llm: boolean };
  providers: IntegrationSpec[];
}
export interface IntegrationTest {
  capability: string;
  status: "ok" | "permission_denied" | "quota_limited" | "failed" | "stale";
  message: string;
  tested_at: string;
  credential_version: number;
}
export interface IntegrationProfile {
  id: string;
  provider: string;
  name: string;
  enabled: boolean;
  options: Record<string, unknown>;
  credential_version: number;
  key_status: "configured" | "empty" | "locked";
  masked_api_key: string | null;
  status: ProfileStatus;
  implementation: "planned" | "available";
  tests: IntegrationTest[];
  created_at: string;
  updated_at: string;
}
export interface IntegrationProfileInput {
  provider: string;
  name: string;
  api_key?: string;
  clear_api_key?: boolean;
  enabled: boolean;
  options?: Record<string, unknown>;
  credential_version?: number;
  initialize_encryption?: boolean;
}
export interface ImportedResult {
  target: "integration" | "model";
  id: string;
  name: string;
  key_status: string;
  imported_from: string;
}

export const getIntegrations = () =>
  request<IntegrationCatalog>({ url: "/integrations/providers" });
export const getIntegrationProfiles = () =>
  request<IntegrationProfile[]>({ url: "/integrations/profiles" });
export const saveIntegrationProfile = (
  data: IntegrationProfileInput,
  id?: string,
) =>
  request<IntegrationProfile>({
    url: id ? `/integrations/profiles/${id}` : "/integrations/profiles",
    method: id ? "PUT" : "POST",
    data,
  });
export const deleteIntegrationProfile = (id: string, version: number) =>
  request<void>({
    url: `/integrations/profiles/${id}?credential_version=${version}`,
    method: "DELETE",
    data: {},
  });
export const importEnvironment = (service: "finance" | "llm") =>
  request<ImportedResult>({
    url: "/integrations/import-environment",
    method: "POST",
    data: { service },
  });

export const statusLabels: Record<ProfileStatus, string> = {
  unconfigured: "未配置",
  saved_unverified: "已保存未验证",
  available: "能力可用",
  permission_denied: "权限不足",
  quota_limited: "额度或限流",
  expired: "已过期",
  locked: "已锁定",
  not_implemented: "尚未实现",
};
