import { request } from "./client";

export interface SourceCapability {
  id: string;
  label: string;
}

export interface SourceInfo {
  id: string;
  name: string;
  role: string;
  trust_level: string;
  trust_label: string;
  pricing: string;
  pricing_label: string;
  auto_call: boolean;
  capabilities: SourceCapability[];
  enabled: boolean;
  status_label: string;
  detail: string;
}

export interface SourcesResponse {
  disclaimer: string;
  sources: SourceInfo[];
}

export const getSources = () =>
  request<SourcesResponse>({ url: "/sources", timeout: 10_000 });

export const sourcesQuery = {
  queryKey: ["sources"],
  queryFn: getSources,
  staleTime: 30_000,
};
