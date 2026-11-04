export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Status = 'pending' | 'approved' | 'corrected' | 'rejected' | 'skipped';
export type Action = 'approve' | 'edit' | 'reject' | 'skip';
export interface Review { id: string; item_id: string; reviewer: string | null; action: Action; corrected_output: Json; judge_correct: boolean | null; note: string | null; created_at: string }
export interface Asset { sha256: string; role: string; mime: string; width: number | null; height: number | null; url: string }
export interface Item { id: string; project: string; fn_name: string | null; input: Json; output: Json; confidence: number | null; judge_label: string | null; judge_reason: string | null; judge_metadata: Json; routed_by: 'low_conf' | 'audit' | 'manual'; status: Status; tags: string[]; trace_id: string | null; created_at: string; assets: Asset[]; reviews?: Review[] }
export interface Project { name: string; threshold: number; target_miss_rate: number; audit_rate: number }
export interface Queue { items: Item[]; total: number }
export interface Filters { project: string; status: string; min: string; max: string }
export interface ReviewBody { action: Action; corrected_output?: Json; judge_correct: boolean | null; note?: string }
const urlKey = new URLSearchParams(window.location.search).get('api_key');
let apiKey = urlKey ?? '';
try {
  if (urlKey !== null) localStorage.setItem('hil_api_key', urlKey);
  else apiKey = localStorage.getItem('hil_api_key') ?? '';
} catch { /* Storage may be unavailable in private browser contexts. */ }
async function request<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { ...init, signal, headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...(apiKey ? { 'X-API-Key': apiKey } : {}) } });
  if (!response.ok) throw new Error(`Request failed (${response.status}): ${await response.text()}`);
  return response.json() as Promise<T>;
}
export const api = {
  projects: (signal?: AbortSignal) => request<Project[]>('/v1/projects', undefined, signal),
  queue: (filters: Filters, offset: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '50', offset: String(offset) });
    if (filters.project) params.set('project', filters.project);
    if (filters.status) params.set('status', filters.status);
    if (filters.min) params.set('min_confidence', filters.min);
    if (filters.max) params.set('max_confidence', filters.max);
    return request<Queue>(`/v1/items?${params}`, undefined, signal);
  },
  item: (id: string, signal?: AbortSignal) => request<Item>(`/v1/items/${encodeURIComponent(id)}`, undefined, signal),
  review: (id: string, body: ReviewBody) => request<Item>(`/v1/items/${encodeURIComponent(id)}/review`, { method: 'POST', body: JSON.stringify(body) }),
};
export function imageUrl(sha: string): string {
  return `/v1/blobs/${encodeURIComponent(sha)}${apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : ''}`;
}
