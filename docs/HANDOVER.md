# 이관 런북 (HANDOVER)

> GitHub 저장소는 **코드만** 담습니다. 운영 데이터(`backend/media/` 1.1GB, `.env` 비밀키)는
> `.gitignore`로 제외되어 push되지 않습니다. 따라서 `git clone`만으로는 시스템이 구동되지 않습니다.
> 아래 절차대로 **코드(Git) + 데이터 번들**을 함께 이관해야 재현됩니다.

## 구성 요소 두 갈래

| 갈래 | 내용 | 이관 수단 |
|---|---|---|
| 코드 | backend/frontend/스키마/스크립트 | GitHub `kawaliee/260618` (`git clone`) |
| 데이터 | `backend/media/*`(원본 문서), `.env`(API 키) | **오프-깃 번들** — `scripts/backup_data.ps1`로 생성 |

## A. 백업(넘기는 쪽)

```powershell
# 프로젝트 루트에서
powershell -ExecutionPolicy Bypass -File scripts\backup_data.ps1
# -> ..\260618_backup\260618_data_<타임스탬프>.tar (media + .env 포함) 생성
```

생성된 번들을 안전한 채널(사내 스토리지/암호화 USB 등)로 전달하십시오.
**비밀키가 들어있으므로 공개 클라우드/이메일 평문 전송 금지.**

## B. 복원(받는 쪽) — 새 PC에서 처음부터 재현

```powershell
# 1) 코드
git clone https://github.com/kawaliee/260618.git
cd 260618

# 2) 데이터 (번들 경로는 실제 위치로)
powershell -ExecutionPolicy Bypass -File scripts\restore_data.ps1 -Bundle <번들.tar 경로>
#    -> backend/media 와 .env 복원

# 3) (선택) .env가 없다면 템플릿으로 생성 후 값 채우기
#    Copy-Item .env.example .env   # 그 뒤 실제 키 입력

# 4) 인프라 기동
docker compose up -d

# 5) RAG 색인 동기화
docker compose exec backend python manage.py ingest_media
```

## C. 검증 (E2E)

```powershell
docker compose exec backend python manage.py shell -c "from services.rag import search_documents; from services.llm import generate_answer; r=search_documents('태평 pjt ppa 계약가격과 보증사항'); a=generate_answer('태평 pjt ppa 계약가격과 보증사항', context=r['context']); print(a['content'])"
```

- 기대: 태평 PJT의 PPA 계약가격·보증사항이 내부 문서(RAG 컨텍스트) 근거로 발췌되어 융합 응답됨 (구체 수치는 DB 문서 기준이며 소스/문서에 하드코딩하지 않음)
- 서비스 포트: frontend `5173`, backend `8000`, postgres `5433`, qdrant `6333/6334`, redis `6379`

## D. 데이터 무결성 참고 (미결)

`ingest_media` 시 일부 문서가 색인되지 않습니다(개발엔 지장 없음, DB 정비 시 처리 예정):
- **레거시 `.doc` 4건**: 파서 확장(변환 경로) 필요
- **DRM 암호화 5건**(계약변경합의서, PF 자금인출절차, EVENT REPORT, 투자위원회 FID 승인 등): 복호화본 교체 필요
- **절단/손상 8건**(태평 투심위·이사회·FID Key-terms 등): 원본 재수급 필요 (전송 중 절단, 파서 복구 불가)

자세한 분류는 이관 검증 로그 참조.
