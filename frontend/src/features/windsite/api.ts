/** 풍력 입지·인허가 검토 API 클라이언트 */
import type { EvaluationResult, LawRef, PermitStep, ProviderConfigRow } from './types'

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/windsite${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers as Record<string, string>) },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({} as { detail?: string }));
    throw new Error(err.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export interface EvaluateParams {
  lat: number;
  lng: number;
  radius_m: number;
  address?: string;
  capacity_mw?: number | null;
  sido?: string;
  sigungu?: string;
}

export const windsiteApi = {
  evaluate: (p: EvaluateParams) =>
    req<EvaluationResult>('/evaluate/', { method: 'POST', body: JSON.stringify(p) }),

  laws: () => req<{ count: number; results: LawRef[] }>('/laws/'),

  permits: (capacityMw?: number | null) =>
    req<{ count: number; results: PermitStep[] }>(
      `/permits/${capacityMw ? `?capacity_mw=${capacityMw}` : ''}`,
    ),

  config: () => req<{ results: ProviderConfigRow[]; note: string }>('/config/'),
};
