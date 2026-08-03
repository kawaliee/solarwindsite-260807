# Claude Code 인계 프롬프트

아래 내용을 **그대로 복사해서** Claude Code에 첫 메시지로 붙여넣으십시오.
(터미널에서 `cd C:\AI_PJT\260618` 후 `claude` 실행)

---

```
이 프로젝트는 재생에너지 AI Agent 시스템이다. 직전 세션(Cowork)에서 작업한 내용을 이어받아
검증하고 완성하는 것이 이번 작업의 목표다. **처음부터 새로 만들지 마라. 이미 코드가 디스크에 있다.**

## 0. 먼저 읽을 것
- CLAUDE.md (프로젝트 마스터 가이드, RAG 개조 이력 — 임의 롤백 금지)
- docs/HANDOVER.md (이관 런북)
- docs/WINDSITE_API_KEYS.md (이번에 추가된 풍력 모듈 운영 가이드 — 미검증 법령 목록 포함)

## 1. 직전 세션에서 완료된 작업 (커밋 안 된 상태)

### (A) UI 다크 테마 전환
frontend/src/index.css의 :root 토큰을 다크 커맨드센터 팔레트로 교체하고,
하드코딩된 밝은 색을 보정 블록으로 덮었다. Topbar/ChatView의 하드코딩 stroke는
currentColor로 변경했다.

### (B) 운영관리 Dashboard (frontend/src/features/ops/)
- koreaMap.data.ts — 실측 17개 시·도 SVG 경계 + 위경도↔SVG 좌표 변환(project/unproject)
  + 시군구 229개 근사 좌표표
- sites.ts — 사업장 데이터 계층. fetchOpsSites()만 API 호출로 바꾸면 DB 연동됨
- KoreaMap.tsx, OpsView.tsx

### (C) 풍력 입지타당성 검토 모듈 (신규)
백엔드 backend/apps/windsite/ (22개 파일):
- schemas.py — 판정 결과 dataclass (Status/Difficulty/Confidence)
- providers/ — 10개 데이터 레이어 어댑터. API 키 없으면 UNKNOWN 반환
- engine.py — 종합 판정 (IMPOSSIBLE 우선, UNKNOWN은 감점)
- permits.py — 인허가 로드맵 생성 (부지 조건·용량별 분기)
- models.py — LawReference / RegulationRule / LocalOrdinance / PermitStep / SiteEvaluation
- management/commands/seed_windsite.py — 법령 16건, 판정규칙 24건, 조례 6건, 인허가 20단계
- views.py, urls.py

프론트 frontend/src/features/windsite/:
- WindSiteView.tsx (메인), SitePicker.tsx (지도 클릭 선택), types.ts, api.ts

배선: backend/config/settings.py(INSTALLED_APPS + API 키 설정), backend/config/urls.py,
frontend/src/App.tsx, Sidebar.tsx, Topbar.tsx, .env.example

## 2. 이번에 해야 할 일 (우선순위 순)

### [1순위] 동작 검증 — 아직 한 번도 실행해보지 못했다
직전 세션은 샌드박스라 docker/npm/tsc를 실행할 수 없었다. 문법 검사와 부분 타입검사만 했다.
다음을 직접 실행해서 실제로 도는지 확인하고, 깨지는 부분을 고쳐라.

  docker compose exec backend python manage.py makemigrations windsite
  docker compose exec backend python manage.py migrate
  docker compose exec backend python manage.py seed_windsite
  docker compose exec frontend npx tsc --noEmit
  docker compose restart backend frontend

그 다음 API를 직접 호출해 응답 스키마를 확인하라:
  curl -X POST http://localhost:8000/api/windsite/evaluate/ -H "Content-Type: application/json" ^
    -d "{\"lat\":36.1234,\"lng\":128.5678,\"radius_m\":500,\"sido\":\"경상북도\",\"sigungu\":\"청도군\",\"capacity_mw\":60}"

브라우저(http://localhost:5173)에서 좌측 메뉴 "풍력 입지검토"와 "운영관리 Dashboard"가
정상 렌더링되는지 확인하라. 다크 테마가 기존 화면(대화/계약)에서도 깨지지 않았는지 함께 본다.

### [2순위] ★★★ 법령 데이터 원문 검증 — 이번 작업의 핵심
직전 세션은 샌드박스 네트워크 정책 때문에 law.go.kr, moleg.go.kr, fcis.forest.go.kr,
eiass.go.kr 등 정부 1차 사이트에 접근하지 못했다. 그래서 법령 시드 데이터의 상당수가
2차 자료 기반이며 confidence='LOW'로 표시되어 있다.

너는 사용자 네트워크에서 실행되므로 접근이 가능할 수 있다. 국가법령정보센터에서 아래를
원문 대조하고, seed_windsite.py의 데이터와 confidence/verified_at을 갱신하라.
**확인하지 못한 것은 LOW로 남겨두고 절대 추측으로 채우지 마라.**

최우선 검증 대상:
1. 환경영향평가법 시행령 [별표3]·[별표4]의 풍력 항목 원문 — 법정 규모 기준선
2. 전기사업법 제61조 공사계획 인가/신고의 10,000kW 기준 (현재 단일 2차 출처만 확보)
3. 전기사업법 시행령 — 발전사업허가 3,000kW 소관 구분의 조번호
4. 산지관리법 시행령 [별표3의2]·[별표4] — 풍력이 산지일시사용허가 대상인지,
   평균경사도 25도 기준이 풍력에도 적용되는지, 표고 기준 수치
5. 국토계획법 시행령 [별표1의2] — 용도지역별 개발행위허가 면적 상한, 도시계획위원회 심의 요건
6. 매장유산법 시행령 제4조 — 지표조사 대상 사업 면적 기준
7. 사방사업법 사방지 지정해제 조번호
8. 자연환경보전법 제34조 및 환경부 「육상풍력 개발사업 환경성평가 지침」 원문
   (생태자연도 2·3등급/별도관리지역의 구체적 허용 요건, 야간 소음 45dB(A) 기준)
9. 지자체 조례 3건(전남 화순군/해남군, 경북 청도군)의 현행 원문 — elis.go.kr 또는
   국가법령정보센터 자치법규. 현재 시드값은 언론보도 기준이라 개정 이력 반영이 안 됐을 수 있다.

추적 필요:
- 신에너지 및 재생에너지 개발·이용·보급 촉진법 시행령 개정안(2026-07-21~08-03 입법예고,
  2026-09-18 시행 예정 보도). 풍력 이격거리 전국 표준화 — 하한은 발전기 높이의 2배,
  상한은 주거지역 최대 1,500m·도로 최대 500m 범위에서 조례 위임.
  확정 여부를 확인하고, 확정됐다면 LocalOrdinance 데이터 구조에 반영 방안을 제시하라.

### [3순위] 미완성 부분 보완
- 주소 → 좌표 지오코딩이 없다. 현재는 지도 클릭 또는 위경도 직접 입력만 가능하다.
  V-World 지오코딩 API 어댑터를 추가하라 (VWORLD_API_KEY 사용).
- 좌표 → 행정구역(시·도/시·군·구) 자동 역지오코딩이 없어 사용자가 직접 입력해야 한다.
  이걸 자동화하면 조례 조회가 자동으로 걸린다.
- SitePicker는 전국 스케일 SVG라 클릭 정밀도가 약 1km다. V-World 또는 카카오 지도 SDK로
  배경지도를 교체하는 방안을 검토하고, 필요 의존성과 작업량을 먼저 보고하라.
- 변전소 좌표 DB가 없어 계통 연계 거리 계산이 동작하지 않는다.
  GridConnectionProvider(substations=[...])에 넣을 데이터 확보 방안을 제안하라.

## 3. 반드시 지킬 규칙

1. **추측 금지.** 법령·조문·수치를 지어내지 마라. 확인 못 한 것은 confidence='LOW'로 두고
   note에 "미확인"이라고 명시하라. 이 시스템은 실제 사업 인허가 판단에 쓰인다.
2. **판정 기준 하드코딩 금지.** 이격거리·등급 기준은 반드시 DB(RegulationRule/LocalOrdinance)에
   둔다. 코드에 숫자를 박지 마라.
3. **UNKNOWN을 없애려 하지 마라.** 데이터가 없으면 UNKNOWN이 정답이다. 억지로 POSSIBLE로
   바꾸지 마라.
4. CLAUDE.md에 기록된 RAG 최적화 4개 항목(O&M 카테고리 매핑, CHUNK 0 가드,
   Top-15 랭킹, LLM 팩트 융합)을 임의로 롤백하지 마라.
5. **기밀 수치 하드코딩 금지.** 계약가격·대주단·지분 등은 소스/커밋에 남기지 않는다.
6. .env는 .gitignore 되어 있다. 절대 커밋하지 마라. API 키를 로그나 커밋 메시지에 노출하지 마라.

## 4. 첫 작업으로 해줄 것

1. 프로젝트 루트의 `_to_delete/` 폴더를 삭제해라 (직전 세션이 남긴 stale git lock 파일이다).
2. `git status`로 현재 변경사항을 확인하고, 위 (A)(B)(C) 작업을 **의미 단위로 나눠서**
   커밋해라. backend/services/rag.py의 기존 수정본은 내용을 확인한 뒤 별도 커밋으로 분리하라.
3. 그다음 [1순위] 동작 검증부터 진행하라.
```

---

## 참고 — 전환 시 주의사항

| 항목 | 내용 |
|---|---|
| 작업 폴더 | `C:\AI_PJT\260618` (오늘 만든 `260803`은 폐기 대상 복사본입니다) |
| 커밋 상태 | 수정 10개 파일 + 신규 4개 디렉토리가 **미커밋** 상태입니다 |
| `.env` | `.gitignore`에 포함되어 있어 안전합니다 (24행) |
| `_to_delete/` | 이 세션이 남긴 stale git lock 파일. Claude Code가 삭제하도록 프롬프트에 넣었습니다 |
| 원래 프롬프트 재사용 | **하지 마십시오.** 코드가 이미 디스크에 있어 중복 구현·충돌이 발생합니다 |
