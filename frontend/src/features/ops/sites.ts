/**
 * 운영사업장 데이터 계층
 * ---------------------------------------------------------------
 * 현재는 임시(하드코딩) 데이터를 반환하지만, 추후 운영사업장 DB가 추가되면
 * `fetchOpsSites()` 내부만 API 호출로 교체하면 화면 수정 없이 연동된다.
 *
 * ⚠️ 수치 관련 주의
 *  - capacityMw / cod 등은 사내 문서 파일명에서 확인 가능한 값만 기입했다.
 *  - 확인되지 않은 값은 임의로 채우지 않고 null(미상)로 두었다.
 *  - 실제 값은 운영사업장 DB 연동 시 채워진다.
 */
import { lookupSigungu, normalizeProvince, project } from './koreaMap.data';

/** 사업 단계 */
export type SiteStatus = 'operating' | 'construction' | 'development';

export const STATUS_LABEL: Record<SiteStatus, string> = {
  operating: '운영중',
  construction: '건설중',
  development: '개발중',
};

/** 상태별 표시 색 (CSS 변수명) */
export const STATUS_COLOR: Record<SiteStatus, string> = {
  operating: 'var(--ok)',
  construction: 'var(--warn)',
  development: 'var(--accent)',
};

export interface OpsSite {
  id: string;
  /** 사업장명 */
  name: string;
  /** 소속 PJT */
  project: string;
  /** 발전 유형 */
  energy: '태양광' | '풍력';
  /** 시·도 */
  province: string;
  /** 시군구 */
  sigungu: string;
  /** 정확한 좌표를 알면 여기에 입력 (없으면 시군구 근사 좌표 사용) */
  lat?: number;
  lng?: number;
  /** 설비용량(MW). 미확인이면 null */
  capacityMw: number | null;
  status: SiteStatus;
  /** 상업운전개시일(COD). 미확인이면 null */
  cod: string | null;
  /** 비고 (근거 문서 등) */
  note?: string;
}

/**
 * 임시 사업장 목록
 * 근거: backend/media 하위 프로젝트 폴더 및 문서 파일명
 */
const MOCK_SITES: OpsSite[] = [
  {
    id: 'dangjin-1',
    name: '당진행복솔라',
    project: '당진PJT 1단계',
    energy: '태양광',
    province: '충청남도',
    sigungu: '당진시',
    capacityMw: null,
    status: 'operating',
    cod: null,
    note: '준공 완료 · 월간 운영보고서 발행 중',
  },
  {
    id: 'dangjin-2-1',
    name: '당진대호솔라 1차',
    project: '당진PJT 2단계',
    energy: '태양광',
    province: '충청남도',
    sigungu: '당진시',
    capacityMw: 48.552,
    status: 'development',
    cod: null,
    note: '송전용전기설비 이용계약 체결(2025.12) · 개발행위허가 진행',
  },
  {
    id: 'dangjin-2-2',
    name: '당진대호솔라 2차',
    project: '당진PJT 2단계',
    energy: '태양광',
    province: '충청남도',
    sigungu: '당진시',
    capacityMw: 30.527,
    status: 'development',
    cod: null,
    note: '송전용전기설비 이용계약 체결(2025.12) · 개발행위허가 진행',
  },
  {
    id: 'hongseong',
    name: '홍성빛나래솔라',
    project: '홍성PJT',
    energy: '태양광',
    province: '충청남도',
    sigungu: '홍성군',
    capacityMw: 100,
    status: 'development',
    cod: null,
    note: '염해부지 · 주민수용성 확보 및 인허가 용역 진행',
  },
  {
    id: 'taepyeong',
    name: '신안증도태양광',
    project: '태평PJT',
    energy: '태양광',
    province: '전라남도',
    sigungu: '신안군',
    capacityMw: null,
    status: 'construction',
    cod: null,
    note: 'FID 승인(2025.06) · EPC/O&M/PF 주요 계약 체결',
  },
];

/** 지도에 그릴 때 필요한 화면 좌표가 계산된 사업장 */
export interface PositionedSite extends OpsSite {
  x: number;
  y: number;
  /** 좌표 출처 — exact: 사업장 lat/lng, approx: 시군구 근사값 */
  coordSource: 'exact' | 'approx';
}

/**
 * 사업장 목록에 지도 좌표를 계산해 붙인다.
 * lat/lng가 있으면 그 값을, 없으면 시군구 근사 좌표를 사용한다.
 */
export function positionSites(sites: OpsSite[]): PositionedSite[] {
  const out: PositionedSite[] = [];
  for (const s of sites) {
    let lng = s.lng;
    let lat = s.lat;
    let coordSource: 'exact' | 'approx' = 'exact';

    if (lng == null || lat == null) {
      const found = lookupSigungu(s.province, s.sigungu);
      if (!found) continue; // 좌표를 못 찾으면 지도에 표시하지 않음
      lng = found.lng;
      lat = found.lat;
      coordSource = 'approx';
    }
    const { x, y } = project(lng, lat);
    out.push({ ...s, x, y, coordSource });
  }
  return out;
}

/** 사업장이 위치한 시·도 집합 (지도 하이라이트용) */
export function activeProvinces(sites: OpsSite[]): Set<string> {
  const set = new Set<string>();
  for (const s of sites) {
    const p = normalizeProvince(s.province);
    if (p) set.add(p);
  }
  return set;
}

export interface OpsSummary {
  total: number;
  operating: number;
  construction: number;
  development: number;
  /** 용량이 확인된 사업장의 합계(MW) */
  knownCapacityMw: number;
  /** 용량 미확인 사업장 수 */
  unknownCapacityCount: number;
  provinceCount: number;
}

/** KPI 집계 */
export function summarize(sites: OpsSite[]): OpsSummary {
  const known = sites.filter(s => s.capacityMw != null);
  return {
    total: sites.length,
    operating: sites.filter(s => s.status === 'operating').length,
    construction: sites.filter(s => s.status === 'construction').length,
    development: sites.filter(s => s.status === 'development').length,
    knownCapacityMw: known.reduce((sum, s) => sum + (s.capacityMw ?? 0), 0),
    unknownCapacityCount: sites.length - known.length,
    provinceCount: activeProvinces(sites).size,
  };
}

/**
 * 사업장 목록 조회.
 *
 * ▶ 운영사업장 DB 연동 시 이 함수 본문만 교체하면 된다.
 *   예)  const res = await fetch(`${API_BASE}/ops/sites/`);
 *        return (await res.json()).results as OpsSite[];
 */
export async function fetchOpsSites(): Promise<OpsSite[]> {
  return MOCK_SITES;
}
