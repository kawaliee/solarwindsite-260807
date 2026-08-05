# windsite 3단계 — 공간분석 기반 전환

3단계의 핵심은 **"조회했다"에서 "재현 가능한 수치로 판정한다"로의 전환**입니다.
기존 구현은 규제 레이어를 호출하기는 했으나, 레이어 ID와 속성명이 추측값이라
호출은 되어도 판정이 성립하지 않았습니다. 이번 단계에서 그 근거를 서버 실측으로 교체하고,
도형을 받아 **최근접 거리(m)** 를 산출하도록 전면 개편했습니다.

관련 문서: [실측 근거](WINDSITE_VWORLD_LAYERS.md) · [운영 가이드](WINDSITE_API_KEYS.md)

---

## 1. 적용 절차

```bash
# 0) 이미지 재빌드 — 공간연산 스택(shapely·pyproj·pyshp·dbfread·matplotlib)과
#    지도용 한글 폰트(fonts-nanum)가 추가되었습니다
docker compose build backend worker

# 1) 마이그레이션
docker compose exec backend python manage.py migrate

# 2) 법령·판정규칙·조례·인허가절차 (기존)
docker compose exec backend python manage.py seed_windsite

# 3) 규제 레이어 정의 — V-World 실측 결과 기반
docker compose exec backend python manage.py seed_windsite_layers

# 4) 국가유산 공간데이터 적재 (29,039건 · 최초 1회, 약 1~2분)
docker compose exec backend python manage.py load_spatial_shp \
    --zip data/heritage/heritage_designated.zip \
    --prefix heritage --category 국가유산 --source 국가유산청 \
    --source-url https://www.khs.go.kr --replace
docker compose exec backend python manage.py load_spatial_shp \
    --zip data/heritage/heritage_change_criteria.zip \
    --prefix heritage --category 국가유산 --source 국가유산청 \
    --source-url https://www.khs.go.kr --replace
```

V-World 레이어 목록을 다시 실측하려면 (연 1회 또는 오류 발생 시):

```bash
python scripts/vworld_probe.py
```

---

## 2. 무엇이 바뀌었나

### 2.1 레이어 정의를 DB로 (`RegulationLayer`)

레이어 ID·명칭 속성·판정 메타를 코드에서 DB로 옮겼습니다. **44개 레이어**가 등록되며,
레이어를 추가·수정할 때 코드를 고치지 않습니다.

값의 출처를 상태로 구분해 보관합니다.

| `probe_status` | 뜻 |
|---|---|
| `OK` | GetFeature 표본 응답에서 **속성값까지 확인** |
| `SCHEMA` | 표본 지점에 피처가 없어 값은 미확인. DescribeFeatureType이 **선언한 속성** 사용 |
| `NO_NAME` | 서버가 명칭 속성을 제공하지 않는 레이어 → 구역명 없이 저촉 여부만 판정 |
| `UNRESOLVED` / `ERROR` | 확정 실패 → **추측하지 않고 판정 보류(UNKNOWN)** |

현재 상태: 표본값 확인 17 · 서버 스키마 25 · 명칭속성 없음 2.

### 2.2 최근접 거리 산출

`geometry=true`로 도형을 받아 EPSG:5179(UTM-K)에서 거리를 계산합니다.
교차 여부만 보던 기존 방식으로는 불가능했던 표현이 가능해졌습니다.

```
교육환경보호구역 경계로부터 58m 지점입니다 — 절대보호구역, 상대보호구역.
국가유산 지정/보호구역 경계로부터 617m 지점입니다 — 역사문화환경보존지역, …
```

`proximity_m`이 설정된 레이어는 임계 밖 피처를 저촉으로 보지 않고, 최근접 거리만 보고합니다.

### 2.3 필지 폴리곤 기반 면적 산출

검토 원의 기하 면적이 아니라 **필지 폴리곤 ∩ 검토 원**의 실면적을 지목별로 집계합니다.
조회 상한(1회 1,000건)에 걸리면 페이지를 끝까지 넘기며, 그래도 남으면 결과에 명시합니다
(무언의 절단 금지).

### 2.4 국가유산 — API 의존 제거

`HERITAGE_API_KEY`가 없어 항상 UNKNOWN이던 항목을 국가유산청 SHP 직접 적재로 대체했습니다.
지정·등록유산·보호구역 11,637건 + 현상변경 허용기준 17,402건.
**현상변경 허용기준 구역 안에 있으면** 난이도를 CRITICAL로 올립니다.

### 2.5 전력계통·정온시설 — OSM Overpass

한전이 공개하지 않는 변전소·송전선로를 OSM에서 탐색하고, 조례 이격거리를 반지름으로 하는
**동심원 분석**을 수행합니다.

```
반경 2,000m 내 정온시설 55개소. 최근접은 고려병원(hospital) 343m.
주거밀집지역 1,200m 이내 42개소 / 800m 이내 29개소.
```

> ⚠️ OSM에는 **접속 가능 용량(계통 여유도)이 없습니다.** 한전 계통연계 사전검토를 대체하지 않으며,
> 결과 문구와 보고서에 이 한계를 명시합니다.

### 2.6 종합 점수 산식 변경

레이어가 10개에서 44개로 늘면서 기존 '항목별 감점 단순합'은 조건부 항목이 몇 건만 나와도
0점으로 포화됐습니다. **데이터를 더 많이 볼수록 점수가 나빠지는** 셈이라 산식을 바꿨습니다.

```
score = 100 - ( 가장 불리한 항목의 감점
              + 3 × (나머지 조건부 항목 수)
              + 2 × (미확인 항목 수) )
```

등급 판정도 "미확인이 전체의 절반"이 아니라 **"판정된 것보다 확인 못 한 것이 많으면 UNKNOWN"**
으로 바꿨습니다.

### 2.7 보고서(docx) + 지도 4종

`POST /api/windsite/report/` → Word 문서. 지도는 배경 타일 없이 **검토에 실제 사용한 벡터
데이터만** 그립니다(판정 근거가 아닌 그림을 섞지 않기 위함). 좌표계가 미터 단위라 축척이 정확합니다.

1. 지적 현황도 (지목별 색상)
2. 규제 구역 중첩도 (저촉·근접 레이어만)
3. 주변 현황도 (건물·도로)
4. 이격거리 동심원도 (조례 반경 + 정온시설)

### 2.8 다필지 비교

`POST /api/windsite/compare/` — 최대 5개 후보지를 같은 기준으로 검토하고 순위표를 만듭니다.
비교 항목: 등급·점수·가용면적·전용 필요 면적·최근접 변전소·조례 저촉·미확인 건수.

---

## 3. 실측으로 드러난 기존 코드의 오류

| 항목 | 기존 가정 | 실측 |
|---|---|---|
| 용도지역 레이어 | `LT_C_UQ111` = 용도지역지구도 | `uq111`은 **도시지역** 하나. 용도지역은 4개 레이어로 분리 |
| 구역명 속성 | `dgm_nm` / `prpos_area_dstrc_nm` | 대부분 **`uname`**. `dgm_nm`은 `upisuq171`뿐 |
| 지목 판별 | `jibun`에 '임야' 등 정식 명칭 | 실제는 `"66 도"` — **지목 부호 1글자**. 정식 명칭 매칭은 항상 실패 |

→ 세 어댑터 모두 "호출은 되지만 판정이 성립하지 않는" 상태였습니다.

---

## 4. 신규/변경 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/windsite/compare/` | 후보지 비교 (최대 5곳) |
| POST | `/api/windsite/report/` | 검토 보고서 docx 다운로드 |

`evaluate` 응답의 `analysis_items[].raw`가 추가 직렬화됩니다 — 최근접 거리·필지 면적·
조회된 구역명 등 판정 근거가 담기며, 보고서 재생성과 화면 상세 표시가 이 값을 씁니다.

---

## 5. 남은 한계 (다음 단계 후보)

| 항목 | 현재 | 필요한 것 |
|---|---|---|
| 생태자연도 · 환경보호지역 | EGIS 키 미발급 → UNKNOWN | EGIS 공개 REST 확인 또는 SHP 조달 |
| 보전산지(산지구분도) | V-World 미제공 | 산림청 데이터 조달 |
| 산사태위험등급 | 산림청 키 미발급 → UNKNOWN | 인증키 발급 |
| 군사·비행안전 | 공개 API 부재 | 기관 협의 (자동화 불가) |
| 계통 여유도 | OSM은 위치만 | 한전 사전검토 (자동화 불가) |
| 법령 원문 검증 | 다수 `confidence=LOW` | 국가법령정보센터 원문 대조 |
| Overpass 사용량 제한 | 직렬화 + 재시도로 완화 | 자체 인스턴스 또는 `OVERPASS_URL`에 대체 인스턴스 지정 |

---

## 6. 검증 기록 (2026-08-05)

Docker 미기동 환경이라 **로컬 sqlite + 실제 외부 API 호출**로 E2E를 확인했습니다.
컨테이너에서는 위 1절 절차를 그대로 실행해 재확인이 필요합니다.

- 마이그레이션 생성·적용, `seed_windsite`, `seed_windsite_layers` 정상
- 국가유산 SHP 29,039건 적재 (지정 7초 / 현상변경 24초)
- 화순읍 좌표 검토 1회 = 44개 레이어 + 10개 어댑터, 소요 약 1분 50초
- 보고서 docx 1.1MB · 지도 4종 · 표 12개 · 목차 10장 생성 확인
- 지도 한글 렌더링 확인 (컨테이너는 `fonts-nanum` 설치로 대응)
