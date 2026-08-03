# 풍력 입지·인허가 검토 모듈 — 운영 가이드

## 0. 설치 및 기동

```powershell
cd C:\AI_PJT\260618

# 1) DB 마이그레이션 생성·적용
docker compose exec backend python manage.py makemigrations windsite
docker compose exec backend python manage.py migrate

# 2) 법령·판정규칙·인허가절차 시드
docker compose exec backend python manage.py seed_windsite

# 3) 프론트 타입검사 (선택)
docker compose exec frontend npx tsc --noEmit
```

접속: http://localhost:5173 → 좌측 메뉴 **풍력 입지검토**

---

## 1. API 인증키 발급 체크리스트 (대표님 직접 수행 필요)

회원가입에 본인인증·사업자정보가 필요해 대행이 불가합니다. 발급 후 `.env`에 값만 넣으면
코드 수정 없이 자동 연동됩니다. **키가 없으면 해당 항목은 `UNKNOWN`(확인 필요)으로 표시**되며,
시스템은 절대 추측 판정하지 않습니다.

### 1-1. V-World (브이월드) — 우선순위 ★★★

가장 효용이 큽니다. 토지이용규제·지적 두 항목이 한 번에 연동됩니다.

1. https://www.vworld.kr 회원가입
2. 오픈API → 인증키 발급신청 (활용 목적: 내부 업무 시스템)
3. **사용 도메인에 `localhost` 등록** ← 누락하면 호출이 거부됩니다
4. `.env`에 입력

```
VWORLD_API_KEY=발급받은키
VWORLD_DOMAIN=localhost
```

### 1-2. 공공데이터포털 (data.go.kr) — 우선순위 ★★

1. https://www.data.go.kr 회원가입
2. **API별로 개별 활용신청**이 필요합니다 (한 번에 전체 승인 아님)
   - 기상청 지상관측(ASOS) 자료
   - 국가유산청 국가유산 정보
   - 산림청 산사태위험등급 (제공 여부 확인 필요)
3. 마이페이지 → 인증키(일반 인증키, Decoding) 복사

```
DATA_GO_KR_KEY=발급받은키
KMA_API_KEY=발급받은키
HERITAGE_API_KEY=발급받은키
HERITAGE_URL=승인된_엔드포인트_URL
```

> 엔드포인트 URL은 활용신청 승인 후 상세페이지의 "요청주소"를 그대로 넣으십시오.

### 1-3. 환경공간정보서비스 (EGIS) — 우선순위 ★★

생태자연도·환경보호구역 담당. **좌표 기반 공개 REST 제공 여부를 확인하지 못했습니다.**
제공된다면 아래에 URL을 넣고, 제공되지 않으면 해당 항목은 수기 조회로 운영하십시오.

```
EGIS_API_KEY=
EGIS_ECOMAP_URL=
EGIS_PROTECTED_URL=
```

### 1-4. 산림청 (산사태위험등급)

```
FOREST_API_KEY=
FOREST_LANDSLIDE_URL=
```

---

## 2. 공개 API가 없어 자동화 불가한 항목

아래 3개는 인증키를 넣어도 자동 판정되지 않습니다. **기관 협의 절차**로 처리하십시오.
화면에는 "공개 API 없음"으로 표시되고, 필요한 조치가 안내됩니다.

| 항목 | 처리 방법 |
|---|---|
| 군사기지·비행안전구역, 레이더 전파영향 | 지자체 경유 관할부대 질의 → 표면높이 초과 시 관할부대심의위원회 협의 |
| 전력계통 연계 여유도 | 한전ON에서 인근 변전소 접속 가능 용량 조회 → 한전 계통연계 사전검토 신청 |
| KIER 풍력자원지도 (허브고도 풍속) | 웹에서 직접 조회 → 사업 확정 전 현장 풍황계측(통상 1년 이상) |

변전소 좌표를 확보하시면 `GridConnectionProvider(substations=[...])`에 넣어
최근접 거리 자동 산출이 가능합니다.

---

## 3. ⚠️ 법령 데이터 재검증 필요 목록 (중요)

시드 데이터는 2026-08 기준 웹 조사 결과입니다. 조사 환경에서 **국가법령정보센터(law.go.kr) 등
정부 1차 사이트 접근이 차단되어 상당수 조문을 원문 대조하지 못했습니다.**

각 레코드에는 `confidence` 값이 붙어 있으며, 화면에도 **미검증 / 교차 확인 / 원문 확인** 배지로
표시됩니다. 아래 항목은 실무 적용 전 반드시 원문을 확인하고 `verified_at`을 갱신하십시오.

### 최우선 검증 (판정 결과에 직접 영향)

| 항목 | 확인할 내용 | 확인처 |
|---|---|---|
| 환경영향평가 대상 규모 | 시행령 **별표3·별표4**의 풍력 항목 원문 및 법정 기준선 | law.go.kr |
| 공사계획 인가/신고 기준 | 10,000kW 기준의 법조문 (단일 출처만 확인됨) | 전기사업법 제61조 |
| 산지 경사도 기준 | 시행령 별표4 평균경사도 25도가 풍력에도 적용되는지, 표고 기준 수치 | 산지관리법 |
| 발전사업허가 소관 구분 | 3,000kW 기준을 규정하는 시행령 조번호 | 전기사업법 시행령 |
| 지자체 이격거리 조례 | 화순·해남·청도 3개 지자체 수치 (개정 이력 있음) | elis.go.kr |

### 추적 필요 (제도 변경 예정)

- **신재생에너지법 시행령 개정안** — 2026-07-21~08-03 입법예고. 풍력 이격거리를 전국 표준화
  (하한: 발전기 높이의 2배 / 상한: 주거지역 최대 1,500m·도로 최대 500m 범위에서 조례 위임).
  **2026-09-18 시행 예정으로 보도**되었으나 미확정. 확정 시 다수 지자체 조례가 재개정될 것으로
  예상되므로 `LocalOrdinance` 데이터 전면 갱신이 필요합니다.

### 확인된 사항 (참고)

- **기후에너지환경부 출범 2025-10-01** — 산업통상자원부의 에너지 인허가 기능(발전사업허가,
  공사계획 인가 등)이 이관되었습니다. 시드 데이터에 반영 완료.
- **해상풍력 특별법 시행 2026-03-26** — **육상풍력에는 적용되지 않습니다.** 따라서 본 모듈의
  인허가 로드맵은 기존 개별 인허가 체계를 전제로 구성했습니다.

---

## 4. 데이터 갱신 방법

판정 기준은 코드가 아닌 DB에 있으므로, 조례 개정이나 법령 확인 시 **데이터만 수정**하면 됩니다.

```powershell
# Django admin 또는 shell 사용
docker compose exec backend python manage.py shell
```

```python
from apps.windsite.models import LocalOrdinance
from datetime import date

o = LocalOrdinance.objects.get(sigungu='청도군', target='RESIDENTIAL')
o.distance_m = 1500
o.article = '제○○조'          # 원문 확인한 조문
o.confidence = 'HIGH'          # 원문 대조 완료
o.verified_at = date.today()
o.save()
```

시드 파일(`backend/apps/windsite/management/commands/seed_windsite.py`)을 직접 수정한 뒤
`seed_windsite`를 재실행해도 됩니다 (`update_or_create` 방식이라 안전하게 재실행 가능).

---

## 5. API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/windsite/evaluate/` | 입지타당성 검토 실행 |
| GET | `/api/windsite/permits/?capacity_mw=60` | 인허가 로드맵 |
| GET | `/api/windsite/laws/` | 관련 법령 목록 |
| GET | `/api/windsite/ordinances/?sigungu=청도군` | 지자체 조례 |
| GET | `/api/windsite/config/` | 데이터 연동 현황 |
| GET | `/api/windsite/evaluations/` | 검토 이력 |

검토 실행 예시:

```json
POST /api/windsite/evaluate/
{
  "lat": 36.1234, "lng": 128.5678, "radius_m": 500,
  "address": "경상북도 청도군 ○○면 산 ○○번지",
  "sido": "경상북도", "sigungu": "청도군",
  "capacity_mw": 60
}
```

---

## 6. 설계 원칙 (수정 시 유지할 것)

1. **추측 금지** — 데이터가 없으면 `UNKNOWN`. 절대 임의 판정하지 않습니다.
2. **근거 동반** — 모든 판정에 법령·조문·출처·검증수준을 함께 실어 보냅니다.
3. **기준의 데이터화** — 이격거리·등급 기준을 코드에 하드코딩하지 않습니다.
4. **점수는 참고치** — 종합 점수는 상대적 리스크 지표이며 법적 판단이 아닙니다.
   화면과 API 응답 모두 이를 전제로 문구를 구성했습니다.
