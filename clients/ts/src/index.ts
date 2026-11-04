export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Status =
  "pending" | "approved" | "corrected" | "rejected" | "skipped" | "superseded";
export type Action = "approve" | "edit" | "reject" | "skip" | "choose";
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
}
export interface Asset {
  sha256: string;
  role: string;
  mime: string;
  width: number | null;
  height: number | null;
  url: string;
  order: number;
  video_ts: number | null;
  bbox: [number, number, number, number] | null;
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
  judge_metadata: Json;
  routed_by: "low_conf" | "audit" | "manual";
  status: Status;
  tags: Record<string, string>;
  trace_id: string | null;
  created_at: string;
  assets: Asset[];
  options: Json[] | null;
  expires_at: string | null;
  reviews?: Review[];
}
export interface Project {
  name: string;
  threshold: number | null;
  target_miss_rate: number;
  audit_rate: number;
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
  judge_correct?: boolean | null;
  reviewer?: string;
  note?: string;
}
export interface ReasonCode { project: string; code: string; label: string | null; polarity: string | null }

export interface AssetUpload {
  data_base64: string;
  mime?: string;
  role?: "input" | "output" | "context";
  filename?: string;
  video_ts?: number;
  bbox?: [number, number, number, number];
}
export interface CreateItem {
  project: string;
  input: Json;
  output: Json;
  confidence?: number | null;
  judge_label?: string;
  judge_reason?: string;
  tags?: Record<string, string>;
  trace_id?: string;
  fn_name?: string;
  options?: Json[] | null;
  expires_at?: string | null;
  assets?: AssetUpload[];
}
export interface ListItems {
  project?: string;
  status?: Status[];
  min_confidence?: number;
  max_confidence?: number;
  limit?: number;
  offset?: number;
  include_expired?: boolean;
}
export interface SupersedeItems {
  project: string;
  trace_id?: string;
  tag_key?: string;
  tag_value?: string;
}
export interface TagThreshold { tag_key: string; tag_value: string; threshold: number }
const segment = encodeURIComponent;
/** Hand-written against openapi.yaml. Requires a runtime with fetch (Node 18+). */
export class HilEvalClient {
  private baseUrl: string;
  private apiKey?: string;
  constructor({ baseUrl, apiKey }: { baseUrl: string; apiKey?: string }) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }
  private async request<T>(path: string, method = "GET", body?: unknown, binary = false): Promise<T> {
    const response = await fetch(this.baseUrl + path, {
      method,
      headers: {
        ...(this.apiKey ? { "X-API-Key": this.apiKey } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`Request failed (${response.status}): ${await response.text()}`);
    if (response.status === 204) return undefined as T;
    return (binary ? response.arrayBuffer() : response.json()) as Promise<T>;
  }
  health() { return this.request<{ ok: boolean }>("/healthz"); }
  listProjects() { return this.request<Project[]>("/v1/projects"); }
  listItems(filters: ListItems = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (Array.isArray(value)) value.forEach(v => query.append(key, v));
      else if (value !== undefined) query.set(key, String(value));
    }
    return this.request<Queue>(`/v1/items${query.size ? `?${query}` : ""}`);
  }
  getItem(id: string) { return this.request<Item>(`/v1/items/${segment(id)}`); }
  createItem(body: CreateItem) { return this.request<Item>("/v1/items", "POST", body); }
  deleteItem(id: string) { return this.request<void>(`/v1/items/${segment(id)}`, "DELETE"); }
  reviewItem(id: string, body: ReviewBody) { return this.request<Item>(`/v1/items/${segment(id)}/review`, "POST", body); }
  supersedeItems(body: SupersedeItems) { return this.request<{ superseded: number }>("/v1/items/supersede", "POST", body); }
  listReasonCodes(project: string) { return this.request<ReasonCode[]>(`/v1/projects/${segment(project)}/reason-codes`); }
  registerReasonCode(project: string, body: { code: string; label?: string; polarity?: string }) {
    return this.request<ReasonCode>(`/v1/projects/${segment(project)}/reason-codes`, "POST", body);
  }
  getTagThreshold(project: string, tag?: string) {
    const query = tag === undefined ? "" : `?${new URLSearchParams({ tag })}`;
    return this.request<{ threshold: number | null }>(`/v1/projects/${segment(project)}/threshold${query}`);
  }
  setTagThreshold(project: string, body: TagThreshold) { return this.request<{ threshold: number }>(`/v1/projects/${segment(project)}/threshold`, "PUT", body); }
  getBlob(sha256: string) { return this.request<ArrayBuffer>(`/v1/blobs/${segment(sha256)}`, "GET", undefined, true); }
}
