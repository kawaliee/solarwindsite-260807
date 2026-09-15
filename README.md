# 재생에너지 입지타당성 검토 시스템

좌표를 찍으면 공공 API 20여 종을 병렬 조회해 입지 규제를 자동 스크리닝하고,
인허가 로드맵과 docx 보고서를 생성하는 Django + React 시스템입니다.

- **풍력 입지검토** — 호기 배치선 단위 판정 · 이격거리 조례 · 재현바람장 풍황
- **태양광 입지검토** — 필지 단위 정밀판정 · 가용면적 산출 · 일사량
- **운영관리 Dashboard** — 전국 사업장 분포 (프런트 전용)

풍력·태양광은 같은 엔진을 쓰고 `EnergyProfile`로 갈립니다.

---

## 설계 원칙

이 저장소에서 가장 중요한 규칙 하나입니다.

> **추측하지 않는다.** 조회에 실패하거나 판정 기준이 없으면 `UNKNOWN`으로 남기고,
> 왜 모르는지를 함께 표기한다.

입지 검토는 잘못된 "가능" 판정 하나가 사업 손실로 이어집니다. 그래서 레이어 ID·속성명은
전부 서버 응답으로 실측 확인한 값만 쓰고, 확인되지 않은 코드는 검출 사실만 알린 뒤
판정을 보류합니다. 판정 문구에는 조회 범위의 한계(필지 단위인지, 거리가 나오는지)를
반드시 적습니다.

---

## 빠른 시작

### 1. 환경변수

```bash
cp .env.example .env
```

`.env`를 열어 값을 채웁니다. **키는 소스에 절대 쓰지 않습니다.**

| 구분 | 키 | 없으면 |
|---|---|---|
| 필수 | `POSTGRES_*`, `DJANGO_SECRET_KEY` | 기동 불가 |
| 입지검토 핵심 | `VWORLD_API_KEY`, `VWORLD_DOMAIN` | 규제 레이어 대부분 `UNKNOWN` |
| 입지검토 확장 | `DATA_GO_KR_KEY`, `ECO_API_KEY`, `FOREST_API_KEY`, `KMA_APIHUB_KEY`, `KEPCO_API_KEY` | 해당 항목만 `UNKNOWN` |
| 법령 | `LAW_API_OC` | 공용 데모 계정(`test`)으로 동작 |

발급 방법은 [docs/WINDSITE_API_KEYS.md](docs/WINDSITE_API_KEYS.md)에 정리돼 있습니다.

> `VWORLD_API_KEY`는 도메인 검증이 걸려 있습니다. `VWORLD_DOMAIN`을 실제 서비스
> 도메인으로 맞추십시오. `LAW_API_OC`는 랜덤 키가 아니라 **신청 이메일의 `@` 앞부분**이며,
> 호출 서버의 공인 IP를 open.law.go.kr에 등록해야 인증을 통과합니다.

### 2. 기동

```bash
docker compose up -d
```

PostgreSQL 16 · Redis · Django · Vite가 함께 뜹니다.
프런트는 <http://localhost:5173>, API는 <http://localhost:8000/api> 입니다.

### 3. 초기 데이터

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_windsite          # 법령·판정규칙·인허가절차
docker compose exec backend python manage.py seed_windsite_layers   # V-World 레이어 정의
docker compose exec backend python manage.py seed_solar             # 태양광 법령·절차
```

국가유산 SHP처럼 용량이 큰 자료는 별도로 내려받아 적재합니다.

```bash
docker compose exec backend python manage.py load_spatial_shp --help
```

---

## 풍력 입지타당성 검토

### 판정 항목

좌표 하나로 아래를 병렬 조회합니다(`ThreadPoolExecutor`, 동시 6).

| 분류 | 항목 | 출처 |
|---|---|---|
| 규제/법령 | 용도지역·지구, 필지 지역지구, 개발제한구역, 상수원보호구역 등 | V-World WFS · NED |
| 산림 | 산지구분(보전/준보전), 산림보호구역, 백두대간, 산사태위험등급 | V-World NED · 생활안전지도 WMS |
| 환경 | 생태자연도, 생태·경관보전지역, 국립공원 | 공공데이터포털 · 국립생태원 WFS |
| 안전/문화재 | **군사기지·비행안전구역**, 국가유산·현상변경 허용기준, 국가유산조사구역 | V-World NED · 국가유산청 SHP/WMS |
| 지자체 조례 | 이격거리 조례, 정온시설 동심원 분석 | 자치법규 OPEN API · OSM · V-World 건물 |
| 인프라 | 변전소·송전선로 거리, 계통 여유용량 | OSM Overpass · 한전 분산전원 연계정보 |
| 사업성 | 토지 소유구분(국·공유지), 토지특성, 풍황 | V-World NED · 기상청 ASOS |

현재 시드 기준 규제 레이어 **53종**, 조례 **22건**, 법령 원문 **71개 조문**,
공간 피처 **29,039건**이 적재돼 있습니다.

### 특징적인 구현

**군사기지·비행안전구역** — 공개 API가 없다고 알려져 있으나 토지이용계획
(`getLandUseAttr`)에 `UNE` 코드군으로 실려 있습니다. 접경지 7곳과 공군·해군기지
13곳을 실측해 코드 19종을 확보했습니다. 판별은 **코드 접두**로 합니다 — 명칭으로
거르면 교육환경보호구역인 `UOA120 상대보호구역`이 오탐됩니다.

**지자체 조례 자동 수집** — 조례가 DB에 없는 지자체를 만나면 검토 실행 중
자치법규 OPEN API로 그 자리에서 수집합니다. 별표가 태양광·풍력 2열 비교표인 경우
머리글에서 열 순서를 판별해 **풍력 열만** 뽑습니다. 이 처리가 없으면 태양광 수치를
집어가 판정이 뒤집힙니다(삼척시 주거밀집 2,000m를 500m로 읽던 실제 사례).

**정온시설 이중 소스** — OSM만 보면 산간·농어촌에서 통째로 빕니다. V-World 건물
레이어를 병행 조회해 좌표로 중복 제거합니다. V-World 건물은 용도 정보가 없으므로
주거로 단정하지 않고 '용도 미확인'으로 구분해 표기합니다.

**신뢰도와 미확인 사유** — 판정 결과(`status`)와 근거의 단단함(`confidence`)은
다른 축입니다. `UNKNOWN`에는 사유가 붙습니다.

| 사유 | 뜻 | 재시도 |
|---|---|---|
| `NO_KEY` | 인증키 미설정 | 키 등록 후 가능 |
| `FETCH` | 조회 실패 | ○ |
| `NO_DATA` | 자료 미구축 | ✕ |
| `NO_RULE` | 판정 기준 없음 | ✕ |
| `BY_DESIGN` | 자동 판정 대상 아님(예: 풍황 실측) | ✕ |

### API

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/windsite/evaluate/` | 입지 검토 실행 |
| POST | `/api/windsite/compare/` | 후보지 비교 (최대 5곳) |
| POST | `/api/windsite/report/` | 검토 보고서 docx 다운로드 (지도 4종 포함) |
| POST | `/api/windsite/geocode/` | 주소↔좌표 + 행정구역 |
| GET | `/api/windsite/laws/` `/permits/` `/ordinances/` `/config/` | 참조 데이터·연동 현황 |

```bash
curl -X POST http://localhost:8000/api/windsite/evaluate/ \
  -H 'Content-Type: application/json' \
  -d '{"lat":36.6620,"lng":129.1580,"radius_m":100,"capacity_mw":20}'
```

`sido`/`sigungu`를 생략하면 좌표를 역지오코딩해 자동으로 채웁니다. 조례 조회 기준이라
특례시(`수원시 장안구` → `수원시`)와 단층제(세종)는 정규화합니다.

---

## 운영 명령

```bash
# 조례 원문 대조 · 수집
docker compose exec backend python manage.py sync_ordinances --sigungu 화순군 --apply

# 법령 현행 여부 대조 (confidence 갱신)
docker compose exec backend python manage.py verify_laws --apply

# 전기위원회 재결례 수집
docker compose exec backend python manage.py sync_korec

# 재현바람장(기상청) 1년치 수집 — 좌표·고도당 약 1시간
docker compose exec -d backend python manage.py collect_rawwind --plan <배치안UUID>

# 로그
docker compose logs -f backend
```

---

## 구조

```
backend/
  apps/
    windsite/            입지타당성 검토
      providers/         데이터 소스 어댑터 (V-World·NED·OSM·WMS·SHP …)
      engine.py          병렬 실행 · 점수 산출
      ordinances.py      조례 수집·추출 서비스
      geo.py             EPSG:5179 공간연산 (shapely·pyproj)
      lawapi.py          국가법령정보 OPEN API
      area_report.py     docx 보고서 조립
      report_cards.py    PART 2 항목별 카드 · 지도
      rawwind.py         기상청 재현바람장 (발전사업허가 제출 자료)
      coast.py           유효지역 해역 제외 판정
    accounts/            로그인 · 사용자
frontend/src/features/   windsite (풍력·태양광 공용) · ops
docs/                    실측 기록 · API 키 발급 안내
```

좌표계는 **EPSG:5179(UTM-K)** 를 미터 연산 기준으로 씁니다. 일부 자료(생태자연도,
산사태 래스터)는 EPSG:5186이라 재투영합니다. PostGIS 없이 bbox 1차 필터 +
shapely 정밀 연산으로 처리합니다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [docs/WINDSITE_API_KEYS.md](docs/WINDSITE_API_KEYS.md) | 공공 API 키 발급·등록 방법 |
| [docs/WINDSITE_VWORLD_LAYERS.md](docs/WINDSITE_VWORLD_LAYERS.md) | V-World 레이어 ID·속성명 실측 기록 |
| [docs/WINDSITE_PHASE3.md](docs/WINDSITE_PHASE3.md) | 구현 이력과 남은 한계 |
| [CLAUDE.md](CLAUDE.md) | 개발 지침 · 준수 규칙 |
| [docs/풍력입지검토시스템_시스템기획서.md](docs/풍력입지검토시스템_시스템기획서.md) | 시스템 설계 |

---

## 알려진 한계

| 항목 | 상태 |
|---|---|
| 풍황 | 기상청 재현바람장으로 산출합니다. 고시 개정으로 발전사업허가 단계에서는 이 자료로 풍황계측기 설치를 갈음할 수 있으나, **투자 판단·발전량 예측에는 현장 실측이 필요합니다** — 모델 재현값이자 격자 대표값입니다 |
| 유효지역 해역 제외 | 육지 경계로 행정경계(읍·면·동)를 씁니다. 조위 기준 해안선과 수십 m 어긋날 수 있어 해안 인접 부지는 공유수면 관리청 확인이 필요합니다 |
| 군사 협의 | 구역 저촉은 판정하나 표면높이 초과 여부와 레이더 전파영향은 관할부대 협의 사항입니다 |
| 계통 여유용량 | 공표값은 신청 시점에 이미 선점됐을 수 있습니다. 한전 사전검토가 필요합니다 |
| 필지 단위 조회 | NED API는 필지별 호출이라 중심 필지 + 면적 상위 6개만 조회합니다. 결과에 조회 범위를 명시합니다 |
| Overpass 사용량 | 공개 인스턴스는 제한이 강합니다. `OVERPASS_URL`에 자체/대체 인스턴스를 지정할 수 있습니다 |

---

## 보안

- **인증키를 소스에 쓰지 마십시오.** 전부 `.env`에서 읽습니다.
- 사업지 좌표·사업명 등 사내 정보를 소스·문서·커밋 메시지에 남기지 마십시오.
- `.env`, `*api key*.txt`, 원본 공간데이터(`backend/data/` 대용량)는 `.gitignore`
  처리돼 있습니다. 공간데이터 재수급 방법은 각 디렉터리의 README를 보십시오.
