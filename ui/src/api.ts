export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Status =
  "pending" | "approved" | "corrected" | "rejected" | "skipped" | "superseded";
export type Action = "approve" | "edit" | "reject" | "skip" | "choose";
export interface FieldChange {
  from: Json;
  to: Json;
}
export interface Review {
  id: string;
  item_id: string;
  reviewer: string | null;
  action: Action;
  choice: Json;
  reason_codes: string[];
  reason_text: string | null;
  corrected_output: Json;
  judge_correct: boolean | null;
  note: string | null;
  created_at: string;
  fields: Record<string, Json> | null;
  field_diff: Record<string, FieldChange> | null;
}
export interface FieldDefinition {
  name: string;
  kind: "enum" | "number" | "text";
  label?: string | null;
  path?: string | null;
  choices?: string[] | Record<string, string[]> | null;
  depends_on?: string | null;
  allow_custom?: boolean;
  nullable?: boolean;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  integer?: boolean;
  multipliers?: number[] | null;
  suggestions?: string[] | null;
}
export interface Asset {
  sha256: string | null;
  role: "input" | "output" | "context";
  mime: string;
  width: number | null;
  height: number | null;
  url: string;
  order: number;
  video_ts: number | null;
  bbox: [number, number, number, number] | null;
  kind: "blob" | "path";
  source_path: string | null;
  filename: string | null;
  availability: "available" | "missing" | "not_allowed";
}
export interface Item {
  id: string;
  project: string;
  fn_name: string | null;
  input: Json;
  output: Json;
  confidence: number | null;
  judge_label: string | null;
  judge_reason: string | null;
  judge_metadata: Record<string, Json>;
  routed_by: "low_conf" | "audit" | "manual";
  status: Status;
  tags: Record<string, string>;
  trace_id: string | null;
  created_at: string;
  assets: Asset[];
  options: Json[] | null;
  expires_at: string | null;
  fields: FieldDefinition[] | null;
  proposed_fields: Record<string, Json> | null;
  external_id: string | null;
  reviews?: Review[];
}
export interface ItemDetail extends Item {
  reviews: Review[];
}
export interface Project {
  name: string;
  threshold: number | null;
  target_miss_rate: number;
  audit_rate: number;
  fields: FieldDefinition[] | null;
}
export interface Queue {
  items: Item[];
  total: number;
}
export interface Filters {
  project: string;
  status: string;
  min: string;
  max: string;
}
export interface ReviewBody {
  action: Action;
  choice?: Json;
  reason_codes?: string[];
  reason_text?: string;
  corrected_output?: Json;
  fields?: Record<string, Json>;
  judge_correct: boolean | null;
  note?: string;
}
export interface ReasonCode {
  project: string;
  code: string;
  label: string | null;
  polarity: string | null;
}
const urlKey = new URLSearchParams(window.location.search).get("api_key");
let apiKey = urlKey ?? "";
try {
  if (urlKey !== null) localStorage.setItem("hil_api_key", urlKey);
  else apiKey = localStorage.getItem("hil_api_key") ?? "";
} catch {
  /* Storage may be unavailable in private browser contexts. */
}
async function request<T>(
  path: string,
  init?: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
    },
  });
  if (!response.ok)
    throw new Error(
      `Request failed (${response.status}): ${await response.text()}`,
    );
  return response.json() as Promise<T>;
}
export const api = {
  reasonCodes: (project: string, signal?: AbortSignal) =>
    request<ReasonCode[]>(
      `/v1/projects/${encodeURIComponent(project)}/reason-codes`,
      undefined,
      signal,
    ),
  projects: (signal?: AbortSignal) =>
    request<Project[]>("/v1/projects", undefined, signal),
  queue: (filters: Filters, offset: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: "50", offset: String(offset) });
    if (filters.project) params.set("project", filters.project);
    if (filters.status) params.set("status", filters.status);
    if (filters.min) params.set("min_confidence", filters.min);
    if (filters.max) params.set("max_confidence", filters.max);
    return request<Queue>(`/v1/items?${params}`, undefined, signal);
  },
  item: (id: string, signal?: AbortSignal) =>
    request<ItemDetail>(
      `/v1/items/${encodeURIComponent(id)}`,
      undefined,
      signal,
    ),
  review: (id: string, body: ReviewBody) =>
    request<Item>(`/v1/items/${encodeURIComponent(id)}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
function withKey(url: string): string {
  return apiKey
    ? `${url}${url.includes("?") ? "&" : "?"}api_key=${encodeURIComponent(apiKey)}`
    : url;
}
/** Playable URL for any asset; path references stream from the serving machine. */
export function assetUrl(asset: Asset): string {
  return asset.kind === "path" || !asset.sha256
    ? withKey(asset.url)
    : imageUrl(asset.sha256);
}
export function imageUrl(sha: string): string {
  return `/v1/blobs/${encodeURIComponent(sha)}${apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : ""}`;
}
