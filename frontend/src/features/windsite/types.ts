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

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  HIGH: '원문 확인',
  MEDIUM: '교차 확인',
  LOW: '미검증',
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
