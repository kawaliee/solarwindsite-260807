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
  configured: boolean | null;
  missing: string[];
}
