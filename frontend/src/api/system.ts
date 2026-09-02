import { request } from "./client";

export interface SystemStatus {
  app: string;
  version: string;
  stage: string;
  local_only: boolean;
  database: string;
  market_data: { status: string; key_configured: boolean };
  model: {
    source: "profile" | "environment" | "none";
    status: string;
    model_label: string | null;
    active_profile_id: string | null;
    profile_count: number;
  };
  encryption: {
    status: "ready" | "missing" | "invalid";
    can_save: boolean;
    can_initialize: boolean;
  };
  live_tests: { api: boolean; llm: boolean };
  integrations: { environment: { finance: boolean; llm: boolean } };
}

export const getSystemStatus = () =>
  request<SystemStatus>({ url: "/system/status", timeout: 10_000 }, true);
export const systemQuery = {
  queryKey: ["system"],
  queryFn: getSystemStatus,
  staleTime: 10_000,
};
