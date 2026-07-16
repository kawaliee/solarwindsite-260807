-- =====================================================================
-- 재생E AI Agent — PostgreSQL Schema (PoC: RAG 챗봇 + 계약)
-- 재무모델/대시보드는 미포함(추후 동일 패턴으로 확장)
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ---------- 공통 / 워크스페이스 ----------
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255) UNIQUE NOT NULL,
    name          VARCHAR(100) NOT NULL,
    department    VARCHAR(100),
    role          VARCHAR(20)  NOT NULL DEFAULT 'admin',  -- admin | member(추후)
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(150) NOT NULL,
    slug        VARCHAR(150) UNIQUE NOT NULL,
    description TEXT,
    settings    JSONB NOT NULL DEFAULT '{}',
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         VARCHAR(150) NOT NULL,
    description  TEXT,
    is_shared    BOOLEAN NOT NULL DEFAULT false,   -- 공용 프로젝트(반출 목적지)
    icon         VARCHAR(50),
    color        VARCHAR(20),
    created_by   UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_projects_workspace ON projects(workspace_id);

-- ---------- RAG : 문서 / 청크 ----------
CREATE TABLE documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID REFERENCES projects(id) ON DELETE SET NULL,  -- NULL = 전사 공용 코퍼스
    title             VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    file_type         VARCHAR(10)  NOT NULL,    -- pdf | docx | xlsx
    storage_uri       VARCHAR(1000) NOT NULL,   -- 원문 열람 경로
    file_size         BIGINT,
    page_count        INT,
    checksum          VARCHAR(128),             -- 중복 적재 방지
    status            VARCHAR(20) NOT NULL DEFAULT 'uploaded', -- uploaded|parsing|embedding|indexed|failed
    metadata          JSONB NOT NULL DEFAULT '{}',
    uploaded_by       UUID REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    indexed_at        TIMESTAMPTZ
);
CREATE INDEX idx_documents_project ON documents(project_id);
CREATE INDEX idx_documents_status  ON documents(status);

CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT  NOT NULL,
    content         TEXT NOT NULL,
    page_number     INT,                         -- 출처 위치(페이지)
    section_title   VARCHAR(300),                -- 조항/제목
    char_start      INT,
    char_end        INT,
    bbox            JSONB,                        -- PDF 좌표(뷰어 하이라이트)
    sheet_name      VARCHAR(200),                 -- Excel 위치
    cell_range      VARCHAR(100),
    token_count     INT,
    qdrant_point_id UUID NOT NULL,                -- Qdrant 포인트 매핑
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chunks_document ON document_chunks(document_id);
CREATE UNIQUE INDEX uq_chunks_qdrant ON document_chunks(qdrant_point_id);

-- ---------- RAG : 대화 ----------
CREATE TABLE conversations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID REFERENCES projects(id) ON DELETE SET NULL,
    title             VARCHAR(300),
    use_internal_docs BOOLEAN NOT NULL DEFAULT true,  -- 사내 문서 참조 토글(대화별)
    is_shared         BOOLEAN NOT NULL DEFAULT false,
    created_by        UUID REFERENCES users(id),
    last_message_at   TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conv_project ON conversations(project_id);

CREATE TABLE messages (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id    UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role               VARCHAR(12) NOT NULL,    -- user | assistant | system
    content            TEXT NOT NULL,
    used_internal_docs BOOLEAN NOT NULL DEFAULT false,
    model              VARCHAR(80),
    token_usage        JSONB,
    status             VARCHAR(12) NOT NULL DEFAULT 'done', -- pending|done|failed
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conv ON messages(conversation_id);

-- 출처(인용) — UI 칩/원문 위치 핵심
CREATE TABLE message_sources (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id        UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    document_id       UUID REFERENCES documents(id) ON DELETE SET NULL,
    document_chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    display_title     VARCHAR(500) NOT NULL,    -- 전체 파일명(호버 팝업)
    short_label       VARCHAR(20),              -- 표시용 5자(칩)
    page_number       INT,                       -- 위치 표기/이동
    location_label    VARCHAR(120),              -- 예: "p.12 · 제3조"
    score             DOUBLE PRECISION,
    rank              INT,
    snippet           TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sources_message ON message_sources(message_id);

CREATE TABLE message_attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id      UUID REFERENCES messages(id) ON DELETE SET NULL,
    filename        VARCHAR(500) NOT NULL,
    file_type       VARCHAR(10),
    storage_uri     VARCHAR(1000) NOT NULL,
    file_size       BIGINT,
    parsed_text_uri VARCHAR(1000),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 대화 공유/반출 → 공용 프로젝트 방
CREATE TABLE conversation_shares (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id      UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    shared_to_project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    share_type           VARCHAR(10) NOT NULL DEFAULT 'copy', -- copy | move | link
    shared_by            UUID REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 계약 ----------
CREATE TABLE contract_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(20) UNIQUE NOT NULL,  -- JDA, SPA, SHA, EPC ...
    name_ko         VARCHAR(150) NOT NULL,
    name_en         VARCHAR(200),
    category        VARCHAR(50),                  -- 개발/인수/건설/운영/금융/자문/공통
    description     TEXT,
    template_body   TEXT,                          -- 표준 양식(placeholder)
    key_term_schema JSONB NOT NULL DEFAULT '[]',  -- 입력 필드 정의
    standard_clauses JSONB NOT NULL DEFAULT '[]',
    review_checklist JSONB NOT NULL DEFAULT '[]',
    version         VARCHAR(20) DEFAULT 'v1',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contract_drafts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID REFERENCES projects(id) ON DELETE SET NULL,
    template_id       UUID NOT NULL REFERENCES contract_templates(id),
    title             VARCHAR(300),
    key_terms         JSONB NOT NULL DEFAULT '{}',
    generated_content TEXT,
    output_file_uri   VARCHAR(1000),               -- Word 산출물
    status            VARCHAR(15) NOT NULL DEFAULT 'draft', -- draft|generating|completed|failed
    created_by        UUID REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contract_reviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE SET NULL,
    template_id         UUID REFERENCES contract_templates(id),  -- 추정 유형
    title               VARCHAR(300),
    source_document_uri VARCHAR(1000),             -- 검토 대상 원본
    review_instruction  TEXT,
    summary             TEXT,
    output_file_uri     VARCHAR(1000),             -- Word 산출물
    status              VARCHAR(15) NOT NULL DEFAULT 'pending', -- pending|reviewing|completed|failed
    created_by          UUID REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contract_review_findings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id         UUID NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_ref        VARCHAR(100),       -- 조항 위치(예: 제12조)
    severity          VARCHAR(10) NOT NULL DEFAULT 'mid',  -- high | mid | low
    category          VARCHAR(30),        -- 독소조항 | 불리조항 | 누락 | 오류
    finding           TEXT NOT NULL,      -- 지적 내용
    suggestion        TEXT,               -- 수정 방향
    source_clause_ref VARCHAR(100),       -- 표준양식 근거
    order_index       INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_findings_review ON contract_review_findings(review_id);

-- ---------- 표준 계약서 종류 시드 ----------
INSERT INTO contract_templates (code, name_ko, name_en, category) VALUES
 ('JDA',     '공동개발협약',            'Joint Development Agreement',     '개발'),
 ('SPA',     '지분/자산 매매계약',      'Sale & Purchase Agreement',       '인수'),
 ('SHA',     '주주간 협약',             'Shareholders Agreement',          '지배구조'),
 ('EPC',     '설계·조달·시공 도급계약', 'EPC Contract',                    '건설'),
 ('OM',      '운영·유지보수 계약',      'O&M Agreement',                   '운영'),
 ('OE',      '감리·발주자엔지니어 계약','Supervision / Owner''s Engineer', '건설'),
 ('FIN',     '금융약정',                'Financing Agreement (PF)',        '금융'),
 ('DD',      '실사 자문용역(FDD/TDD/LDD)','Due Diligence Advisory',        '자문'),
 ('DSA_SLA', '직접계약/서비스수준협약', 'Direct Agreement / SLA',          '금융/운영'),
 ('ADMIN',   '사무위탁 계약',           'Administrative Service Agreement','경영관리'),
 ('LEASE',   '(토지) 임대차 계약',      'Land Lease Agreement',            '개발'),
 ('GSVC',    '일반용역 계약',           'General Service Agreement',       '공통'),
 ('PSVC',    '인허가용역 계약',         'Permit/Licensing Service',        '개발'),
 ('PPA_REC', '전력판매/REC 거래계약',   'PPA / REC Agreement',             '매출'),
 ('NDA',     '비밀유지계약',            'Non-Disclosure Agreement',        '공통'),
 ('MOU',     '양해각서',                'Memorandum of Understanding',     '공통');
