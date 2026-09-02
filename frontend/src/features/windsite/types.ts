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
  /**
   * 검색한 자리가 속한 읍·면·동 경계 (주소→좌표 방향에서만 온다).
   *
   * 점만 찍어 주면 부지가 행정구역 어디에 걸치는지 알 수 없어, 경계를
   * 넘긴 자리를 사업지로 잡아도 눈치채지 못한다. 조회에 실패하면 null이며
   * 그때는 경계만 안 그려진다.
   */
  boundary?: AdminBoundary | null;
}

/** 읍·면·동 행정구역 경계 */
export interface AdminBoundary {
  /** '강원특별자치도 횡성군 둔내면' */
  full_name: string;
  sido: string;
  /** 조례 조회 기준 시·군·구 (읍면동 이름이 아니다) */
  sigungu: string;
  /** 읍·면·동 이름 */
  emd: string;
  rings: [number, number][][];
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

/**
 * 필지 한 필. 클릭 좌표를 연속지적으로 되돌린 결과다.
 *
 * `exact`가 false면 경계선을 눌러 **인접 필지로 대신 고른** 것이다.
 * 화면이 이 사실을 숨기면 남의 땅 판정을 이 사업지 것으로 읽게 된다.
 */
export interface ParcelInfo {
  pnu: string;
  addr: string;
  jibun: string;
  jimok: string;
  jiga: string;
  area_m2: number;
  rings: LatLng[][];
  lat: number;
  lng: number;
  exact: boolean;
}

/** 필지를 찾지 못한 좌표 — 이유를 구분해 받는다 */
export interface ParcelMiss {
  lat: number;
  lng: number;
  /** NO_PARCEL 필지가 없다 · FETCH 조회하지 못했다 */
  reason: 'NO_PARCEL' | 'FETCH';
  detail: string;
}

/** 필지 검토 결과 블록 — parcels 모드일 때만 채워진다 */
export interface AreaParcel {
  count: number;
  total_area_m2: number;
  by_jimok: Record<string, { count: number; area_m2: number }>;
  parcels: Omit<ParcelInfo, 'rings'>[];
  /** 경계 근처를 눌러 인접 필지로 대신한 PNU */
  inexact: string[];
  misses?: ParcelMiss[];
}

/** 스크리닝 채색 등급 — 판정 4단계를 그대로 따른다 (기획서 §3.4) */
/**
 * 앞의 넷은 **규제 축**, NOT_APPLICABLE은 **후보 적성 축**이다.
 * 배제는 '규제 때문에 안 됨', 대상 아님은 '애초에 후보가 아님'이라
 * 성격이 다르다. 뭉치면 왜 빠졌는지 알 수 없다.
 */
export type ScreenGrade = 'POSSIBLE' | 'CONDITIONAL' | 'IMPOSSIBLE'
  | 'UNKNOWN' | 'NOT_APPLICABLE';

/** 스크리닝으로 등급이 매겨진 필지 하나 */
export interface ScreenParcel {
  pnu: string;
  jibun: string;
  jimok: string;
  area_m2: number;
  grade: ScreenGrade;
  /** 그 등급이 된 이유. 비어 있으면 아무 제약에도 걸리지 않은 것 */
  reasons: string[];
  rings: LatLng[][];
  /** 폴리곤 내부가 보장된 대표점 — 정밀판정으로 넘길 좌표 */
  lat: number | null;
  lng: number | null;
}

export interface ScreenResult {
  /** 화면이 넓어 칠하지 못한 경우. 오류가 아니라 '더 확대하라'는 상태다 */
  too_wide?: boolean;
  detail?: string;
  parcels: ScreenParcel[];
  counts: Record<ScreenGrade, number>;
  area_km2: number;
  /** 후보 적성 판정에 쓴 최소 면적(m²) */
  min_area_m2: number;
  /** 후보 적성 판정에 쓴 건축물 수 */
  building_count: number;
  /** 상한에 걸려 일부 필지가 빠졌다 — '이게 전부'로 읽히면 안 된다 */
  truncated: boolean;
  fetch_failures: string[];
  notes: string[];
  jurisdictions: { sido: string; sigungu: string;
                   ordinance_state: AreaJurisdiction['ordinance_state'];
                   rule_count: number }[];
  /** 조례를 확인하지 못한 지자체 — 그 관할은 '가능'으로 칠하지 않는다 */
  unverified: string[];
}

export const SCREEN_GRADE_LABEL: Record<ScreenGrade, string> = {
  POSSIBLE: '가능',
  CONDITIONAL: '조건부',
  IMPOSSIBLE: '배제',
  UNKNOWN: '미확인',
  NOT_APPLICABLE: '대상 아님',
};

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
  /** 필지 검토일 때만 채워진다 */
  parcel: AreaParcel | null;
  grandfathering: Grandfathering | null;
  /** with_items 요청 시에만 채워진다 */
  items?: AreaItems;
  /** 발전시간·이용률 — 태양광 검토에서만 채워진다 */
  yield: AreaYield | null;
  /**
   * 구역 안 필지별 채색 — 태양광 구역 검토에서만 채워진다.
   *
   * 제약도(overlays)는 규제 레이어를 **면적**으로 칠하므로 필지 경계와
   * 무관하다. '이 구역의 30%가 조건부'는 알려 주지만 '이 필지가 되는가'는
   * 답하지 못해, 같은 구역을 필지 단위로 한 번 더 가른 결과를 함께 받는다.
   */
  screening: ScreenResult | null;
  jurisdictions: AreaJurisdiction[];
  jurisdiction_meta: Record<string, unknown>;
  /** 조회하지 못한 레이어 — 있으면 못 본 제약이 있다는 뜻이다 */
  fetch_failures: string[];
  notes: string[];
  /** 사업구역 경계(필지 기반) — 그린 폴리곤에서 도로·구거 등 대상 아님
   *  필지를 뺀, 판정·면적·보고서 지도가 실제로 쓰는 도형이다. */
  site_rings?: LatLng[][];
  /** 그린 구역에서 무엇이 얼마나 빠졌는가 */
  site_refine?: { parcel_count?: number; na_count?: number; na_m2?: number;
                  drawn_m2?: number; site_m2?: number; fallback?: string };
  overlays: { blocked: LatLng[][]; conditional: LatLng[][]; free: LatLng[][];
              /** 조례 이격 범위 — blocked·conditional에 이미 포함. 윤곽 표시용 */
              ordinance_house?: LatLng[][];
              ordinance_road?: LatLng[][];
              ordinance_road_uncertain?: LatLng[][] };
  /** 조례 이격을 조건부로 셌는가 — 발전사업허가일이 조례 시행일보다 앞선 경우 */
  ordinance_grandfathered?: boolean;
  /** 겹침 면에 커서를 올렸을 때 보여 줄 문구 — {overlay 열쇠: 한 줄} */
  overlay_labels?: Record<string, string>;
  /** 도로별 선·이격 범위 — 어느 도로로부터 얼마만큼 침범되는지 */
  road_detail?: RoadDetail[];
  /**
   * 환경성 평가 항목별 지도 미리보기(용도지역 구성·농업진흥지역도 등).
   *
   * 화면의 "지도 보기" 선택지를 이 목록으로 채운다. 보고서 캡처도 같은
   * 목록·같은 도형을 쓰므로, 화면에서 고른 항목이 그대로 보고서에 실린다.
   */
  env_layers?: EnvLayer[];
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
  /** 해당 호기 번호 — 몇 기인지보다 어느 기인지가 배치를 고칠 때 쓸모 있다 */
  hit_nos: number[];
  /** 사람이 읽는 형태 ('1~10호기 (전 호기)', '1·3~5호기 (4기)') */
  hit_label: string;
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

/** 발전량 산정 결과 — 후보 비교에서 그대로 꺼내 쓴다 */
export interface AreaYield {
  hours_per_day: number;
  capacity_factor: number;
  yield_kwh_kwp: number;
  gti_kwh: number;
  tilt_deg: number;
  pr: number;
  optimal_tilt_deg: number;
  annual_mwh?: number;
  inhouse_cf: number;
  inhouse_hours: number;
  basis: string;
}

export interface AreaItems {
  merged: AreaItem[];
  points: AreaItemPoint[];
}

/* ── 검토 프로젝트·배치안 ─────────────────────────────────────────── */

/** 배치안 저장 시점의 요약 — 규제는 개정되므로 evaluated_at과 함께 읽는다 */
export interface PlanSummary {
  score?: number;
  grade?: FeasibilityStatus;
  points?: number;
  total_ha?: number;
  free_ha?: number;
  blocked_ha?: number;
  conditional_ha?: number;
}

/** 배치안 — 호기 좌표 한 벌과 그때의 검토 조건 */
export interface SitePlan {
  /** 검토 방식 — 불러올 때 어느 모드로 되돌릴지가 갈린다 */
  mode: 'layout' | 'area' | 'parcel';
  id: string;
  project_id: string;
  project_name: string;
  name: string;
  note: string;
  /** 검토자(담당자). 로그인 계정과 다를 수 있어 따로 남긴다 */
  reviewer: string;
  turbines: LatLng[];
  turbine_count: number;
  turbine_radius_m: number;
  corridor_radius_m: number;
  capacity_mw: number | null;
  permit_date: string;
  sido: string;
  sigungu: string;
  summary: PlanSummary;
  /** 요약을 산출한 시점. 비어 있으면 판정 없이 좌표만 저장한 것이다 */
  evaluated_at: string;
  created_at: string;
  updated_at: string;
}

/** 검토 프로젝트 — 배치안을 묶는 단위 */
export interface SiteProject {
  /** 에너지원 — 풍력 배치안과 태양광 후보가 한 목록에 섞이지 않게 한다 */
  energy_type: 'WIND' | 'SOLAR' | 'ALL';
  id: string;
  name: string;
  description: string;
  sido: string;
  sigungu: string;
  plan_count: number;
  plans: SitePlan[];
  created_at: string;
  updated_at: string;
}


/**
 * 조례 도로 이격의 **도로 한 개** 몫.
 *
 * 합친 붉은 면 하나로는 "어느 도로가 원인인가"에 답할 수 없다. 도로는
 * 옮길 수 없으니, 원인을 알아야 배치를 어디로 물릴지 정해진다.
 */
export interface RoadDetail {
  name: string;
  /** 국가교통DB 도로등급 — 일반국도 · 지방도 · 시·군도 */
  rank: string;
  distance_m: number;
  /** 배제인가(국도·지방도) 조건부인가(시·군도 — 군도 여부 미확인) */
  blocked: boolean;
  /** 이 도로 때문에 구역에서 빠지는 면적 */
  area_m2: number;
  rings: LatLng[][];
  line: LatLng[][];
}

/**
 * 환경성 평가 항목 하나 — 지도로 낼 수 있는 구역 단위 규제 레이어.
 *
 * kind='zoning'은 국토계획법 4종 분류(도시·관리·농림·자연환경보전) 중
 * 하나다. 화면·보고서 모두 이 종류는 **한데 모아 한 장**("용도지역
 * 구성")으로 낸다. kind='item'은 실제로 저촉된 개별 규제(농업진흥지역
 * 등)이며, 항목마다 **따로** 한 장씩 낸다.
 */
export interface EnvLayer {
  kind: 'zoning' | 'item';
  name: string;
  /** kind='item'일 때만 있다 — IMPOSSIBLE·CONDITIONAL 등 */
  status: string | null;
  area_m2: number;
  ha: number;
  rings: LatLng[][];
}
