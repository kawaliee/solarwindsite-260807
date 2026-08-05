/** 사업 Fact-sheet API 클라이언트 */
import type { FactSheet, FactSheetSchema, FactSheetSummary, PublishResult } from './types'

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers as Record<string, string>) },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({} as { detail?: string }));
    throw new Error(err.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export type FactSheetPayload = Partial<
  Pick<FactSheet, 'name' | 'aliases' | 'spc_name' | 'stage' | 'as_of_date' | 'author' | 'pjt_folder' | 'data' | 'project'>
>;

export const factsheetApi = {
  schema: () => req<FactSheetSchema>('/factsheets/schema/'),

  list: () => req<{ count: number; results: FactSheetSummary[] }>('/factsheets/'),

  get: (id: string) => req<FactSheet>(`/factsheets/${id}/`),

  create: (payload: FactSheetPayload) =>
    req<FactSheet>('/factsheets/', { method: 'POST', body: JSON.stringify(payload) }),

  update: (id: string, payload: FactSheetPayload) =>
    req<FactSheet>(`/factsheets/${id}/`, { method: 'PATCH', body: JSON.stringify(payload) }),

  remove: async (id: string) => {
    const res = await fetch(`${API_BASE}/factsheets/${id}/`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`삭제 실패 (${res.status})`);
  },

  markdown: (id: string) =>
    req<{ filename: string; markdown: string }>(`/factsheets/${id}/markdown/`),

  downloadUrl: (id: string) => `${API_BASE}/factsheets/${id}/markdown/?download=1`,

  publish: (id: string, pjtFolder?: string) =>
    req<PublishResult>(`/factsheets/${id}/publish/`, {
      method: 'POST',
      body: JSON.stringify({ pjt_folder: pjtFolder || undefined }),
    }),
};
