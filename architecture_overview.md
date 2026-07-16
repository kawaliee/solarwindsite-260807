# 재생E AI Agent 시스템 아키텍처 개요

현재 개발 중인 AI Agent는 **사용자가 문서를 기반으로 질문하고 답변을 얻을 수 있는 RAG(검색 증강 생성) 시스템**입니다. 프론트엔드, 백엔드, 데이터베이스가 각각 명확한 역할을 나누어 담당하고 있습니다.

---

## 1. 전체 시스템 구성도 (상위 구조)

시스템은 크게 3개의 계층(Layer)으로 나뉩니다.

1. **사용자 인터페이스 (Frontend)**: 화면에 채팅창과 메뉴를 보여주는 역할
2. **서버 및 비즈니스 로직 (Backend)**: 프론트엔드의 요청을 처리하고, 문서를 읽고(파싱/OCR), AI(LLM)와 통신하는 핵심 두뇌 역할
3. **데이터베이스 (Database)**: 채팅 기록(RDB)과 문서의 의미(Vector DB)를 기억하는 저장소

```mermaid
graph LR
    User[사용자] <--> Frontend[프론트엔드\nReact/Vite]
    Frontend <--> Backend[백엔드\nDjango/Python]
    Backend <--> DB_Postgres[(PostgreSQL\n채팅/문서 정보)]
    Backend <--> DB_Qdrant[(Qdrant\n벡터 DB)]
    Backend <--> Redis[(Redis\n비동기 큐)]
    Backend <--> LLM[OpenRouter API\n(GPT-4o 등)]
```

---

## 2. 계층별 상세 구조 및 사용 기술 (하위 구조)

### 2.1 프론트엔드 (Frontend)
사용자가 직접 보는 화면입니다. 빠르고 가벼운 React 기반으로 구축되었습니다.

- **핵심 기술**: React (Vite), TypeScript, 순수 CSS (`index.css`)
- **주요 폴더 및 파일**:
  - `frontend/src/App.tsx`: 메인 화면 뼈대 (사이드바와 메인 콘텐츠 영역 분리)
  - `frontend/src/components/`: 화면의 조각들
    - `Sidebar.tsx`: 왼쪽 메뉴 및 로고 영역
    - `ChatBox.tsx`: 대화창 및 사용자 입력 영역
  - `frontend/src/api/client.ts`: 백엔드와 통신(데이터를 주고받는)을 담당하는 파일
  - `frontend/src/index.css`: 전체 디자인(글꼴, 색상, 애니메이션 등)을 정의하는 스타일시트

### 2.2 백엔드 (Backend)
AI Agent의 '두뇌' 역할을 합니다. 문서를 분석하고, 질문에 답을 찾아냅니다.

- **핵심 기술**: Python, Django (웹 프레임워크), Django REST Framework
- **핵심 모듈 (`backend/services/` 폴더)**:
  - `rag.py`: 사용자의 질문을 받아서 DB에서 관련 문서를 찾고, AI에게 답변을 지시하는 총괄 매니저.
  - `parser.py`: 한글, 워드, 엑셀, PDF 문서를 텍스트로 읽어내고, **"제N조" 단위(의미 단위)로 텍스트를 자르는(Chunking)** 역할.
  - `ocr_service.py`: 스캔된 이미지 PDF에서 글자를 강제로 읽어내는(OCR) 기술 (`pytesseract` 사용).
  - `embedding.py`: 잘라낸 텍스트 덩어리들을 AI가 이해할 수 있는 숫자(벡터)로 변환.
  - `qdrant_service.py`: 변환된 숫자(벡터)를 Qdrant DB에 넣거나 찾는 역할.
  - `llm.py`: 질문과 찾은 문서를 조합하여 최종 답변을 생성하는 역할 (OpenRouter API 활용).

### 2.3 데이터베이스 (Database)
데이터의 성격에 따라 3가지 저장소를 나누어 씁니다.

1. **PostgreSQL (관계형 DB)**
   - 역할: 회원 정보, 대화 기록(채팅방 목록, 주고받은 메시지), 업로드된 문서의 기본 정보(파일명 등) 저장.
2. **Qdrant (벡터 DB)**
   - 역할: 문서의 '의미'를 숫자로 저장. 사용자가 "태양광 사업 수익성"이라고 질문하면, 단어가 똑같지 않아도 의미가 가장 비슷한 문서 구절을 매우 빠르게 찾아냅니다.
3. **Redis (캐시 및 메시지 브로커)**
   - 역할: 수백 페이지의 PDF를 파싱할 때 시간이 오래 걸리므로, 이 작업을 뒤로 빼서(백그라운드) 천천히 처리할 수 있도록 돕는 임시 대기열 역할.

---

## 3. 작동 흐름 예시 (문서 질문 시)

1. **질문 입력**: 사용자가 프론트엔드(React) 채팅창에 "풍력 발전소 계약 조건이 뭐야?"라고 입력.
2. **백엔드 전달**: 프론트엔드가 백엔드(Django)의 API로 질문을 전송.
3. **의미 변환**: 백엔드는 질문을 숫자로 변환(Embedding).
4. **문서 검색**: Qdrant DB에서 질문 숫자와 가장 비슷한 의미를 가진 문서 구절(청크)을 3~5개 가져옴.
5. **AI 답변 생성**: 백엔드는 가져온 문서 구절을 참고 자료로 삼아 LLM(AI)에게 "이 자료를 보고 질문에 답해줘"라고 요청 (`rag.py` → `llm.py`).
6. **결과 출력**: AI가 만든 답변이 프론트엔드로 전달되어 화면에 표시됨.

---

## 4. 인프라 및 환경 (Docker)

모든 프로그램은 **Docker(도커)**라는 컨테이너 기술 위에서 돌아갑니다.
- `docker-compose.yml` 파일 하나로 프론트엔드, 백엔드, 워커(백그라운드 작업), Postgres, Qdrant, Redis 6개의 '가상 컴퓨터(컨테이너)'를 한 번에 띄워서 서로 통신하게끔 구성되어 있습니다.
