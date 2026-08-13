/** 풍력 입지·인허가 검토 — 타입 정의 (백엔드 schemas.py와 1:1 대응) */

export type FeasibilityStatus = 'POSSIBLE' | 'CONDITIONAL' | 'IMPOSSIBLE' | 'UNKNOWN';
export type Difficulty = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW';

export const STATUS_LABEL: Record<FeasibilityStatus, string> = {
  POSSIBLE: '가능',
  CONDITIONAL: '조건부 가능',
  IMPOSSIBLE: '불가',
  UNKNOWN: '확인 필요',
};

export const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  LOW: '낮음',
  MEDIUM: '보통',
  HIGH: '높음',
  CRITICAL: '치명',
};

/**
 * 신뢰도는 **판정의 근거가 얼마나 단단한가**이지, 판정이 나왔는지 여부가 아니다.
 * 종전 'LOW: 미검증'은 판정 실패로 읽혀, 판정이 나온 항목까지 실패로 오인됐다.
 */
export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  HIGH: '원문 확인',
  MEDIUM: '교차 확인',
  LOW: '참고 수준',
};

export const CONFIDENCE_HINT: Record<Confidence, string> = {
  HIGH: '법령·조례 원문을 직접 대조한 값입니다.',
  MEDIUM: '공공 API 응답을 근거로 산출했습니다.',
  LOW: '보조 지표이거나 자료 누락 가능성이 있어 실사·실측으로 확인이 필요합니다.',
};

/** UNKNOWN이 된 이유 — 재시도로 풀리는 것과 아닌 것을 구분한다 */
export type UnknownReason = 'NO_KEY' | 'FETCH' | 'NO_DATA' | 'NO_RULE' | 'BY_DESIGN';

export const UNKNOWN_REASON_LABEL: Record<UnknownReason, string> = {
  NO_KEY: '인증키 미설정',
  FETCH: '조회 실패',
  NO_DATA: '자료 없음',
  NO_RULE: '판정 기준 없음',
  BY_DESIGN: '자동 판정 대상 아님',
};

export const UNKNOWN_REASON_HINT: Record<UnknownReason, string> = {
  NO_KEY: '.env에 인증키를 등록하면 자동 판정됩니다.',
  FETCH: '일시적 오류일 수 있습니다. 다시 실행하면 판정될 수 있습니다.',
  NO_DATA: '해당 지점에 자료가 구축되지 않았습니다. 재시도해도 달라지지 않습니다.',
  NO_RULE: '자료는 받았으나 판정 기준이 없어 임의 판단하지 않았습니다.',
  BY_DESIGN: '현장 실측 등 자동화로 대체할 수 없는 항목입니다.',
};

export interface AnalysisItem {
  category: string;
  item_name: string;
  status: FeasibilityStatus;
  reason: string;
  difficulty: Difficulty;
  law: string;
  article: string;
  confidence: Confidence;
  source_url: string;
  data_source: string;
  action_required: string;
  /** 판정 근거 원자료 — 최근접 거리·필지 면적·조회된 구역명 등 (어댑터마다 형태가 다름) */
  raw?: Record<string, unknown>;
  /** status가 UNKNOWN일 때의 사유. 그 외에는 빈 문자열 */
  unknown_reason?: string;
}

export interface PermitStep {
  order: number;
  phase: string;
  name: string;
  authority: string;
  law: string;
  article: string;
  statutory_days: number | null;
  depends_on: string[];
  applicable: boolean;
  applicability_reason: string;
  confidence: Confidence;
  source_url: string;
  note: string;
}

export interface LawRef {
  name: string;
  category: string;
  purpose: string;
  key_articles: string;
  confidence: Confidence;
  source_url: string;
  verified_at: string | null;
  note: string;
}

export interface EvaluationResult {
  site_info: {
    address: string;
    coordinates: { lat: number; lng: number };
    radius_m: number;
    total_area_m2: number;
  };
  overall_feasibility: {
    score: number;
    grade: FeasibilityStatus;
    summary: string;
  };
  analysis_items: AnalysisItem[];
  permit_roadmap: PermitStep[];
  applicable_laws: LawRef[];
  data_gaps: string[];
  evaluated_at: string;
}

export interface ProviderConfigRow {
  item_name: string;
  category: string;
  data_source: string;
  required_settings: string[];
  /** 없어도 동작하지만 있으면 판정이 깊어지는 키 (예: 계통 여유용량) */
  optional_settings: string[];
  /** 필수 키가 모두 채워졌는지. 키를 아예 쓰지 않는 어댑터는 null */
  configured: boolean | null;
  missing: string[];
  missing_optional: string[];
  active_keys: string[];
}

/** 지오코딩 — POST /geocode/ */
export interface GeocodeResult {
  lat: number;
  lng: number;
  /** 지번주소 (좌표→주소 방향) */
  address?: string;
  /** 도로명주소 — 지점에 따라 없을 수 있다 */
  road_address?: string;
  /** 주소→좌표 방향에서 정제된 주소 */
  matched?: string;
  sido: string;
  /** 조례 조회 기준으로 정규화된 값 ('수원시 장안구' → '수원시') */
  sigungu: string;
  /** 정규화 전 원본 */
  sigungu_full?: string;
}

/** 후보지 비교 — POST /compare/ */
export interface ComparisonRow {
  rank: number;
  label: string;
  score: number;
  grade: FeasibilityStatus;
  coordinates: { lat: number; lng: number };
  radius_m: number;
  usable_area_m2: number | null;
  total_parcel_area_m2: number | null;
  parcel_count: number | null;
  conversion_needed: Record<string, number>;
  nearest_substation: { name: string; distance_m: number; voltage?: number } | null;
  nearest_quiet_facility: { name: string; kind: string; distance_m: number } | null;
  ordinance_breaches: string[];
  blockers: string[];
  critical_conditions: string[];
  unknown_count: number;
  summary: string;
}

export interface CompareResult {
  comparison: ComparisonRow[];
  results: { label: string; result: EvaluationResult }[];
  note: string;
  compared_at: string;
}

export interface CompareCandidate {
  label?: string;
  lat?: number;
  lng?: number;
  address?: string;
  radius_m?: number;
  capacity_mw?: number | null;
  sido?: string;
  sigungu?: string;
}

// ── 사업구역(폴리곤) 제약도 ────────────────────────────────────────
// 점 검토는 항목별 가부를 내지만, 수천 ha 구역은 어딘가 반드시 규제에
// 걸리므로 가부가 성립하지 않는다. 대신 면적이 어떻게 나뉘는지를 낸다.

export type LatLng = [number, number];

export interface AreaBlock {
  area_m2: number;
  ha: number;
  ratio: number;
}

export interface AreaReason {
  layer: string;
  status: FeasibilityStatus;
  area_m2: number;
  ha: number;
  ratio: number;
  /** 산출 근거가 잠정적임 (예: 건물 용도 미확인 상태의 이격 버퍼) */
  provisional?: boolean;
  /** 구역 전체를 덮어 위치를 가르지 못하는 레이어 */
  blanket?: boolean;
}

export interface AreaJurisdiction {
  code: string;
  sido: string;
  sigungu: string;
  full_name: string;
  area_m2: number;
  ratio: number;
  ordinance_state: 'HAS_RULES' | 'NO_RULE' | 'NOT_FOUND' | 'UNVERIFIED';
  rule_count: number;
  max_distance_m: number;
}

export interface AreaLayout {
  /** 1호기부터 순서대로 */
  turbines: LatLng[];
  turbine_radius_m: number;
  corridor_radius_m: number;
  turbine_area_m2: number;
  /** 발전기 원에 삼켜지지 않고 남은 연결선 구간 면적 */
  corridor_area_m2: number;
}

export interface GrandfatherOrdinance {
  sigungu: string;
  ordinance: string;
  article: string;
  /** 조례 전체의 최신 시행일 */
  effective_date: string;
  /** 소급 여부를 가르는 날짜. 경과조치를 담은 개정의 시행일이다 */
  cutoff_date: string;
  /** true면 경과조치 부칙에서 얻은 날짜, false면 최신 시행일로 대신한 것 */
  cutoff_is_transition: boolean;
  /** IMMEDIATE | AFTER_DAYS | EXPLICIT | PROMULGATED */
  cutoff_basis: string;
  /** 발전사업허가일이 기준일보다 앞선다는 사실. 면제 확정이 아니다 */
  permit_earlier: boolean;
  addenda: string;
}

export interface Grandfathering {
  permit_date: string;
  review_required: boolean;
  ordinances: GrandfatherOrdinance[];
  note: string;
  /** 조례 이격을 적용하지 않을 경우의 제약없음 면적 (참고용) */
  free_if_exempt_m2?: number;
  ordinance_area_m2?: number;
}

export interface AreaResult {
  ring: LatLng[];
  total: AreaBlock;
  blocked: AreaBlock;
  conditional: AreaBlock;
  free: AreaBlock;
  pending: AreaBlock;
  /** 아무 레이어에도 걸리지 않는 면적 */
  available_strict: AreaBlock;
  /** 위 + 조건부 (협의·저감으로 진행 가능한 범위) */
  available_with_consultation: AreaBlock;
  by_reason: AreaReason[];
  zoning: AreaReason[];
  blanket: AreaReason[];
  /** 배치선 검토일 때만 채워진다 */
  layout: AreaLayout | null;
  grandfathering: Grandfathering | null;
  /** with_items 요청 시에만 채워진다 */
  items?: AreaItems;
  jurisdictions: AreaJurisdiction[];
  jurisdiction_meta: Record<string, unknown>;
  /** 조회하지 못한 레이어 — 있으면 못 본 제약이 있다는 뜻이다 */
  fetch_failures: string[];
  notes: string[];
  overlays: { blocked: LatLng[][]; conditional: LatLng[][]; free: LatLng[][] };
  evaluated_at: string;
}

export const ORDINANCE_STATE_LABEL: Record<AreaJurisdiction['ordinance_state'], string> = {
  HAS_RULES: '조례 적용',
  NO_RULE: '이격 조례 없음(확인됨)',
  NOT_FOUND: '조례 미확인',
  UNVERIFIED: '조회 실패',
};

/** 지점별 62개 항목 — with_items로 요청했을 때만 채워진다 */
export interface AreaItem {
  category: string;
  item_name: string;
  status: FeasibilityStatus;
  reason: string;
  law: string;
  article: string;
  unknown_reason: string;
  /** 가장 나쁜 판정이 나온 지점 번호 */
  worst_no: number | null;
  /** 그 판정에 해당하는 지점 수 / 전체 지점 수 */
  hits: number;
  total: number;
  per_point: Record<string, FeasibilityStatus>;
}

export interface AreaItemPoint {
  no: number;
  address: string;
  lat: number;
  lng: number;
  grade: FeasibilityStatus;
  score: number;
  summary: string;
}

export interface AreaItems {
  merged: AreaItem[];
  points: AreaItemPoint[];
}
