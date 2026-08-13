/** 풍력 입지·인허가 검토 API 클라이언트 */
import type {
  AreaResult,
  CompareCandidate,
  CompareResult,
  EvaluationResult,
  GeocodeResult,
  LatLng,
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

  evaluateArea: (ring: LatLng[], permitDate = '') =>
    req<AreaResult>('/evaluate-area/', {
      method: 'POST',
      body: JSON.stringify({ ring, permit_date: permitDate }),
    }),

  /** 발전기 배치선 검토 — turbines는 1호기부터 순서대로 */
  evaluateLayout: (
    turbines: LatLng[], turbineRadiusM: number, corridorRadiusM: number,
    permitDate = '',
  ) =>
    req<AreaResult>('/evaluate-area/', {
      method: 'POST',
      body: JSON.stringify({
        turbines,
        turbine_radius_m: turbineRadiusM,
        corridor_radius_m: corridorRadiusM,
        permit_date: permitDate,
      }),
    }),

  laws: () => req<{ count: number; results: LawRef[] }>('/laws/'),

  permits: (capacityMw?: number | null) =>
    req<{ count: number; results: PermitStep[] }>(
      `/permits/${capacityMw ? `?capacity_mw=${capacityMw}` : ''}`,
    ),

  config: () => req<{ results: ProviderConfigRow[]; note: string }>('/config/'),

  /**
   * 지오코딩 — 좌표 → 주소·행정구역 (또는 주소 → 좌표).
   * 지도를 클릭했을 때 사업지 주소와 시·도/시·군·구를 자동으로 채우는 데 쓴다.
   * 시·군·구는 조례 조회 기준이므로 백엔드에서 정규화된 값이 온다
   * (예: '수원시 장안구' → '수원시').
   */
  geocode: (p: { lat: number; lng: number } | { address: string }) =>
    req<GeocodeResult>('/geocode/', { method: 'POST', body: JSON.stringify(p) }),

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
  /**
   * 사업구역·배치선 제약도 보고서 — evaluate-area와 같은 body를 보낸다.
   * signal로 화면을 즉시 풀고, cancelAreaReport로 서버 작업까지 멈춘다.
   */
  downloadAreaReport(body: Record<string, unknown>, signal?: AbortSignal): Promise<void> {
    return download('/windsite/area-report/', body, '풍력구역검토', signal);
  },

  /**
   * 진행 중인 보고서 생성 중단.
   *
   * fetch만 끊으면 서버는 계속 돈다 — Django 동기 뷰는 클라이언트가 끊긴
   * 것을 모른다. 이 호출이 있어야 남은 외부 조회가 실제로 멈춘다.
   */
  /** 진행 상황 — 서버가 Redis에 남긴 값을 읽는다 */
  areaReportProgress(jobId: string) {
    return req<{ percent: number; stage: string; done: number; total: number;
                 running: boolean }>(
      `/area-report/progress/?job_id=${encodeURIComponent(jobId)}`);
  },

  cancelAreaReport(jobId: string) {
    return req<{ ok: boolean }>('/area-report/cancel/', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  },

  async downloadReport(p: EvaluateParams & { with_maps?: boolean }): Promise<void> {
    return download('/windsite/report/', p, '풍력입지검토');
  },
};

/** docx 내려받기 공통 — 파일명은 Content-Disposition(RFC 5987)에서 읽는다 */
async function download(path: string, body: unknown, fallbackName: string,
                        signal?: AbortSignal): Promise<void> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    // 499 = 사용자 중단. 오류가 아니므로 조용히 끝낸다.
    if (res.status === 499) return;
    if (!res.ok) {
      const err = await res.json().catch(() => ({} as { detail?: string }));
      throw new Error(err.detail || `보고서 생성 실패 (${res.status})`);
    }

    // 파일명은 Content-Disposition의 RFC 5987 형식(filename*=UTF-8''…)에 담겨 온다
    const disp = res.headers.get('Content-Disposition') || '';
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disp)?.[1];
    const filename = encoded
      ? decodeURIComponent(encoded)
      : `${fallbackName}_${new Date().toISOString().slice(0, 10)}.docx`;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
