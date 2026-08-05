/** 사업 Fact-sheet — 백엔드 schema.py 와 1:1 대응하는 타입 */

export type FieldType = 'text' | 'textarea' | 'number' | 'date' | 'select' | 'table';

export interface ColumnDef {
  key: string;
  label: string;
  type: Exclude<FieldType, 'table' | 'textarea'>;
  options?: string[];
  unit?: string;
  width?: string;
}

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  unit?: string;
  options?: string[];
  hint?: string;
  placeholder?: string;
  star?: boolean;
  status?: boolean;
  columns?: ColumnDef[];
  rows?: Record<string, string>[];
}

export interface GroupDef {
  title: string;
  fields: FieldDef[];
}

export interface SectionDef {
  id: string;
  no: number;
  title: string;
  icon: string;
  stage: 'dev' | 'build' | 'ops' | null;
  subtitle?: string;
  note?: string;
  fields?: FieldDef[];
  groups?: GroupDef[];
}

export interface StageDef {
  key: 'dev' | 'build' | 'ops';
  no: string;
  title: string;
  desc: string;
}

export interface FactSheetSchema {
  version: string;
  stages: StageDef[];
  confidence_choices: string[];
  sections: SectionDef[];
}

/** 셀 하나 = 값 + 확정도 */
export interface Cell {
  v?: string | Record<string, string>[];
  s?: string;
}

/** { [sectionId]: { [fieldKey]: Cell, _note?: string } } */
export type SheetData = Record<string, Record<string, Cell | string>>;

export interface Completeness {
  filled: number;
  total: number;
  ratio: number;
}

export interface FactSheetSummary {
  id: string;
  project: string | null;
  project_name: string;
  name: string;
  aliases: string[];
  spc_name: string;
  stage: 'dev' | 'build' | 'ops';
  as_of_date: string | null;
  author: string;
  pjt_folder: string;
  published_at: string | null;
  published_path: string;
  completeness: Completeness;
  capacity_ac: string;
  created_at: string;
  updated_at: string;
}

export interface FactSheet extends FactSheetSummary {
  data: SheetData;
  schema_version: string;
  markdown: string;
}

export interface PublishResult {
  published_path: string;
  published_at: string;
  bytes: number;
  ingest_command: string;
  note: string;
}

export const STAGE_LABEL: Record<string, string> = {
  dev: '개발',
  build: '건설',
  ops: '운영',
};

/** 확정도 순환 순서 — 마지막은 미지정(빈 문자열)으로 되돌아간다 */
export const CONFIDENCE_CYCLE = ['확정', '예상', '미정', '해당없음', ''];

export const CONFIDENCE_CLASS: Record<string, string> = {
  '확정': 'ok',
  '예상': 'est',
  '미정': 'tbd',
  '해당없음': 'na',
  '': 'none',
};
