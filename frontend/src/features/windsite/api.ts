/** 입지·인허가 검토 API 클라이언트 — 풍력·태양광 공용
 *
 * 엔드포인트는 하나다. 에너지원은 요청 본문(구역 검토·보고서)이나
 * 질의문자열(법령·인허가)의 energy 로 넘긴다. 값을 넣지 않으면 풍력이다.
 */
import type {
  AreaResult,
  CompareCandidate,
  CompareResult,
  GeocodeResult,
  LatLng,
  LawRef,
  ParcelInfo,
  ScreenResult,
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

  /**
   * 클릭 좌표 → 그 좌표가 놓인 필지.
   *
   * found=false와 예외를 구분해 다룬다. 앞은 '여기엔 필지가 없다'(도로·하천
   * 등)이고 뒤는 '물어보지 못했다'이다. 뭉뚱그리면 화면이 없는 사실을
   * 단정하게 된다.
   */
  parcel: (p: { lat: number; lng: number }) =>
    req<{ found: boolean; parcel?: ParcelInfo; reason?: string; detail?: string }>(
      '/parcel/', { method: 'POST', body: JSON.stringify(p) }),

  /**
   * 화면 범위 필지 스크리닝 — 4등급 채색.
   *
   * 화면이 넓으면 too_wide=true가 온다. 오류가 아니라 '더 확대하라'는
   * 상태라, 화면에서 빨간 문구로 띄우지 않는다.
   */
  screen: (bounds: [number, number, number, number], energy: string = 'SOLAR',
           opts: { shapes?: 'candidates' | 'all'; signal?: AbortSignal } = {}) =>
    req<ScreenResult>('/screen/', {
      method: 'POST',
      // shapes='candidates'면 후보가 아닌 필지의 도형을 서버가 빼고 보낸다.
      // 실측상 좌표의 54%라, 같은 대역폭으로 두 배 넓은 화면을 볼 수 있다.
      body: JSON.stringify({ bounds, energy, shapes: opts.shapes ?? 'candidates' }),
      signal: opts.signal,
    }),

  laws: (energy: string = 'WIND') =>
    req<{ count: number; results: LawRef[] }>(`/laws/?energy=${energy}`),

  permits: (capacityMw?: number | null, energy: string = 'WIND') =>
    req<{ count: number; results: PermitStep[] }>(
      `/permits/?energy=${energy}${capacityMw ? `&capacity_mw=${capacityMw}` : ''}`,
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
    // 파일명은 서버가 Content-Disposition으로 준다. 아래는 그마저 없을 때의
    // 대비값이라, 사내 표기와 같은 모양으로 맞춘다.
    //   태양광 입지타당성 검토 보고서_장흥 염해농지 태양광_260825
    const label = body.energy === 'SOLAR' ? '태양광' : '풍력';
    const project = String(body.project_name || '').trim();
    const prefix = [`${label} 입지타당성 검토 보고서`, project]
      .filter(Boolean).join('_');
    return download('/windsite/area-report/', body, prefix, signal);
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
  projects: (energy?: string) =>
    req<{ count: number; results: SiteProject[] }>(
      `/projects/${energy ? `?energy=${energy}` : ''}`),

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
    // 대비 파일명의 날짜도 서버(`_report_filename`)와 같은 YYMMDD로 맞춘다.
    const d = new Date();
    const stamp = [d.getFullYear() % 100, d.getMonth() + 1, d.getDate()]
      .map(n => String(n).padStart(2, '0')).join('');
    const filename = encoded
      ? decodeURIComponent(encoded)
      : `${fallbackName}_${stamp}.docx`;

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
