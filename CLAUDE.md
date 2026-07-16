# CLAUDE.md — 재생에너지 거래 AI RAG 챗봇 이관 개발 지침서

이 파일은 Claude Code가 본 프로젝트를 열었을 때, 시스템 빌드 구조와 기존 RAG 엔진 개량 패치 이력을 1초 만에 학습하여 연속성 있게 협업하기 위한 마스터 가이드라인입니다.

---

## 1. 빌드 및 주요 명령 스크립트 (Build & Exec Commands)

Claude Code에서 후속 구현 검증 또는 컨테이너 기동 시 아래 표준 명령어를 사용하십시오.

### 🐳 도커 컨테이너 라이프사이클
- **전체 컨테이너 백그라운드 구동**: `docker compose up -d`
- **전체 컨테이너 정지 (볼륨 유지)**: `docker compose down`
- **백엔드 실시간 연산 로그 모니터링**: `docker compose logs -f backend`

### ⚡ RAG 데이터 파이프라인 관리
- **미디어 문서 디렉토리 색인/임베딩 동기화**: `docker compose exec backend python manage.py ingest_media`
- **로컬 RAG 파이프라인 E2E 정상성 검증**:
  ```bash
  docker compose exec backend python manage.py shell -c "from services.rag import search_documents; from services.llm import generate_answer; search_res = search_documents('태평 pjt ppa 계약가격과 보증사항'); ans = generate_answer('태평 pjt ppa 계약가격과 보증사항', context=search_res['context']); print('ANSWER:\n', ans['content'])"
  ```

---

## 2. RAG 시스템 최적화 패치 핵심 요약 (RAG Optimization History)

사용자의 질문 정합성을 보장하기 위해 핵심 RAG 모듈들에 이식 완료된 4대 개조 사양입니다. 임의 롤백을 금지합니다.

### 📌 2.1. O&M 카테고리 맵핑 일치화 (`OM` -> `OM`)
- **버그**: 질의 분석기(`query_analyzer.py`)가 카테고리를 `'O&M'`으로 추출하나, DB 메타데이터 상에는 `'OM'`으로 적재되어 RAG 검색 시 카테고리 필터링이 불일치 기각되던 현상.
- **조치**: [services/rag.py](file:///c:/AI_PJT/260618/backend/services/rag.py)에서 `'O&M'`을 `'OM'`으로 자동 정형 변환되도록 통일 완료.

### 📌 2.2. 초대형 3만 자 CHUNK 0 버퍼 포만 가드 탑재
- **버그**: 계약서 전반부에 조항 구분자(`제1조` 등)가 없어 서두가 3만 자짜리 초대형 CHUNK 0로 적재됨. 이로 인해 RAG 전송 한도(25,000자)를 100% 독점해 뒤이은 가격/보증 알맹이 청크들이 잘렸음.
- **조치**: 
  1. [backend/services/parser.py](file:///c:/AI_PJT/260618/backend/services/parser.py) 내에 서두가 1,500자를 초과할 때 1,200자 단위(오버랩 200자)로 슬라이싱 분할 적재하는 안전 파서를 도입해 89개 청크로 무손실 재적재 완수.
  2. [services/rag.py](file:///c:/AI_PJT/260618/backend/services/rag.py)의 컨텍스트 결합 루프에 `MAX_CHUNK_CHARS_IN_CONTEXT = 3000` (3,000자) 상한제 가드를 장착해 버퍼 포만을 원천 차단함.

### 📌 2.3. 복합 질문 시 가격/보증 랭킹 누락 해소 (Top-15 및 쿼리 확장)
- **버그**: "가격"과 "보증"을 동시에 질문 시, 보증 관련 키워드가 상위 8위 슬롯을 독차지해 PPA 고정단가(`CHUNK 28`)가 8위 밖으로 밀려나 누락되던 Recall 병목 발생.
- **조치**:
  1. Qdrant 1차 픽업량을 `top_k=50`으로 확장하여 Reranking 전의 후보군 모수를 다량 확보함.
  2. 챗봇 컨트롤러 뷰([backend/apps/chat/views.py](file:///c:/AI_PJT/260618/backend/apps/chat/views.py)) L77의 RAG 전송 슬롯 규제를 `10`에서 **`15`**로 완벽 상향하여 Rerank 후 전달 한도를 전면 개방함.
  3. `rag.py` 쿼리 보정 알고리즘에 `'상대방/당사자'`, `'ppa'`, `'가격/단가/정산단가'` 형태소 자동 확장을 결합해 검색 점수를 획기적으로 상승시킴.

### 📌 2.4. LLM 팩트 융합 및 SPC 별칭 가이드 주입 완료
- **조치**: [services/llm.py](file:///c:/AI_PJT/260618/backend/services/llm.py)의 동기식/스트리밍식 시스템 프롬프트에 **프로젝트 별칭 ↔ 정식 법인명 동의어 가이드**와, **계약가격·정산단가·보증사항을 [내부 참조 자료]에 실제 제시된 값·조건만 근거로 발췌하되 조건부·유보(서면합의 예정 등) 문구도 함께 밝히라**는 팩트 융합 규칙을 장착하여, 명칭 불일치로 인한 기각 없이 E2E 정합성을 복원함.
  - ⚠️ **보안**: 특정 계약가격·대주단·지분·금액 등 기밀 수치는 **소스에 하드코딩하지 않는다.** 답변 근거 값은 반드시 DB(RAG 컨텍스트)에서 인용하며, 소스/문서/커밋에 사내 기밀을 남기지 않는다.

---

## 3. 후속 구현 및 준수 규칙 (Development Rules)

- **임의 순위 보정 금지**: RAG 검색 스펙을 조율할 때, 투심위 보고서 등 특정 문서를 1위로 올리기 위해 메타데이터나 필터를 하드코딩식으로 강제 개조하지 마십시오.
- **DRM 암호화 파일 조치**: 현재 당진 1단계 O&M 위탁계약서는 암호화 파일(`[['EncryptedPackage']]`)로 잠겨 있습니다. 복호화된 docx로 덮어쓰고 `ingest_media`를 다시 호출해 수집하십시오.
- **협업 컨텍스트 준수**: 새로운 RAG 기능 개발 시, 기존의 `MAX_CHUNK_CHARS_IN_CONTEXT` 상한 정책과 Qdrant 50개 픽업 모델을 해치지 않고 조화롭게 설계하십시오.
