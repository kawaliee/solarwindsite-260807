/** 풍력 입지·인허가 검토 API 클라이언트 */
import type {
  CompareCandidate,
  CompareResult,
  EvaluationResult,
  LawRef,
  PermitStep,
  ProviderConfigRow,
} from './types'

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

  /** 후보지 비교 (최대 5곳) */
  compare: (candidates: CompareCandidate[]) =>
    req<CompareResult>('/compare/', {
      method: 'POST',
      body: JSON.stringify({ candidates }),
    }),

  /**
   * 검토 보고서(docx) 내려받기.
   * 응답이 JSON이 아니라 파일이라 req()를 쓰지 않고 직접 처리한다.
   */
  async downloadReport(p: EvaluateParams & { with_maps?: boolean }): Promise<void> {
    const res = await fetch(`${API_BASE}/windsite/report/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({} as { detail?: string }));
      throw new Error(err.detail || `보고서 생성 실패 (${res.status})`);
    }

    // 파일명은 Content-Disposition의 RFC 5987 형식(filename*=UTF-8''…)에 담겨 온다
    const disp = res.headers.get('Content-Disposition') || '';
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disp)?.[1];
    const filename = encoded
      ? decodeURIComponent(encoded)
      : `풍력입지검토_${new Date().toISOString().slice(0, 10)}.docx`;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
