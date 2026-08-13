/** 풍력 입지·인허가 검토 API 클라이언트 */
import type {
  AreaResult,
  CompareCandidate,
  CompareResult,
  GeocodeResult,
  LatLng,
  LawRef,
  PermitStep,
  ProviderConfigRow,
  SitePlan,
  SiteProject,
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

export const windsiteApi = {
  /**
   * 사업지 검토 — 지점 1개 이상(배치선) 또는 구역 폴리곤.
   *
   * 지점 1개면 예전 '지점 검토'와 같은 결과가 나온다. 같은 엔진을 같은
   * 인자로 부르므로 판정이 완전히 일치한다(실측 확인).
   * withItems를 주면 규제 62개 항목까지 함께 낸다 — 지점이 많으면 느리다.
   */
  evaluateArea: (body: Record<string, unknown>) =>
    req<AreaResult>('/evaluate-area/', {
      method: 'POST',
      body: JSON.stringify(body),
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

  /* ── 검토 프로젝트·배치안 ─────────────────────────────────────── */

  /** 사업 목록 — 배치안까지 함께 온다 */
  projects: () => req<{ count: number; results: SiteProject[] }>('/projects/'),

  patchProject: (id: string, body: Partial<Pick<SiteProject, 'name' | 'description'>>) =>
    req<SiteProject>(`/projects/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),

  deleteProject: (id: string) =>
    req<{ ok: boolean; deleted_plans: number }>(`/projects/${id}/`, { method: 'DELETE' }),

  /**
   * 배치안 저장. project(사업명)가 처음 보는 이름이면 사업도 함께 만든다 —
   * 저장하려면 사업부터 만들라고 하면 지도를 찍어둔 채 화면을 두 번 오간다.
   */
  savePlan: (body: Record<string, unknown>) =>
    req<SitePlan>('/plans/', { method: 'POST', body: JSON.stringify(body) }),

  patchPlan: (id: string, body: Record<string, unknown>) =>
    req<SitePlan>(`/plans/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),

  deletePlan: (id: string) =>
    req<{ ok: boolean }>(`/plans/${id}/`, { method: 'DELETE' }),

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
