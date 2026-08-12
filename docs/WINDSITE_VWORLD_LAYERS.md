# V-World 레이어 실측 근거 (windsite)

> 이 문서는 **추측이 아니라 V-World 서버 응답**으로 확정한 레이어 ID·속성명 기록입니다.
> 재현: `python scripts/vworld_probe.py` → 산출물 `backend/data/vworld/`
>
> | 산출물 | 내용 |
> |---|---|
> | `wfs_capabilities.xml` | WFS GetCapabilities 원본 |
> | `layers.json` | 제공 레이어 **177건** 전체 (ID ↔ 제목) |
> | `describe/*.xml` | 레이어별 DescribeFeatureType 원본 |
> | `probe_result.json` | 요약 — 시드(`seed_windsite_layers`)의 입력 |

실측일: 2026-08-05 · 표본 지점: 광주 도심 / 전남 화순 산간 / 경북 청도 산간 (buffer 2,000m)

---

## 1. 실측으로 드러난 기존 코드의 오류

| 항목 | 기존 코드 가정 | 실측 결과 |
|---|---|---|
| 용도지역 레이어 | `LT_C_UQ111` = "용도지역지구도" 단일 레이어 | **틀림.** `lt_c_uq111`은 **"도시지역"** 하나뿐. 용도지역은 `uq111`(도시)·`uq112`(관리)·`uq113`(농림)·`uq114`(자연환경보전) **4개 레이어로 분리** 제공 |
| 구역명 속성 | `dgm_nm` / `DGM_NM` / `prpos_area_dstrc_nm` | **틀림.** `lt_c_uq*`·`ud801`·`uo301`·`agrixue101` 계열은 모두 **`uname`**. `dgm_nm`을 쓰는 건 `upisuq171` 뿐 |
| 지목 판별 | 연속지적 `jibun`에 `'임야'` 등 **정식 지목명**이 들어있다고 가정 | **틀림.** 실제 값은 `"66 도"` 형태로 **지목 부호 1글자**(도=도로). 정식 명칭 매칭은 항상 실패 |

→ 기존 `LandUseRegulationProvider` / `CadastralProvider`는 **호출은 되지만 판정이 성립하지 않는 상태**였습니다.

## 2. 확정된 레이어 (Data API 조회 성공)

`uname` 속성에 구역명이 담깁니다. 공통 부가 속성: `sido_name`, `sigg_name`, `dyear`(지정연도), `dnum`.

| 레이어 ID | 제목 | 실측 `uname` 예 |
|---|---|---|
| `lt_c_uq111` | 도시지역 | 제1종일반주거지역 |
| `lt_c_uq112` | 관리지역 | (조회됨) |
| `lt_c_uq113` | 농림지역 | 농림지역 |
| `lt_c_uq114` | 자연환경보전지역 | (표본 지점 미해당) |
| `lt_c_uq121` | 경관지구 | 자연경관지구 |
| `lt_c_uq123` | 고도지구 | (화순에서 조회됨) |
| `lt_c_uq128` | 취락지구 | (조회됨) |
| `lt_c_uq162` | 도시자연공원구역 | (조회됨) |
| `lt_c_ud801` | 개발제한구역 | 개발제한구역 |
| `lt_c_uf901` | 백두대간보호지역 | **핵심구역** (소백산 36.96,128.48에서 확인) |
| `lt_c_uo301` | 국가유산 지정/보호구역 | **역사문화환경보존지역** |
| `lt_c_uo101` | 교육환경보호구역 | (조회됨) |
| `lt_c_agrixue101` | 농업진흥지역도 | 농업진흥구역 |
| `lt_c_agrixue102` | 영농여건불리농지도 | (조회됨) |
| `lt_c_upisuq171` | 개발행위허가제한지역 | `dgm_nm`=개발행위허가제한지역 |
| `lt_c_wgisnpgug` | 국립자연공원 | `park_name`=무등산 |
| `lt_c_fsdifrsts` | 산림입지도 | `name`=갈색약건산림토양, `toyanghyun` |
| `lt_c_kfdrssigugrade` | 산불위험예측지도 | 속성 49개 |
| `lp_pa_cbnd_bubun` | 연속지적도 | `pnu`,`jibun`,`addr`,`jiga` |
| `lt_c_spbd` | 도로명주소건물 | `buld_nm`,`gro_flo_co`,`rd_nm` — **정온시설 이격거리 산정 기초** |
| `lt_l_sprd` | 도로명주소도로 | `rn` — **도로 이격거리 산정 기초** (MultiLineString) |
| `lt_c_aisprhc` | 비행금지구역 | `prh_lbl_4`=비행금지구역, `prh_lbl_2/3`=상/하한고도 (서울 P-73A 확인) |

**geometry 반환 확인** — `geometry=true` 파라미터로 `MultiPolygon`/`MultiLineString` 좌표를 받습니다.
따라서 **교차 여부뿐 아니라 최근접 거리 산출이 가능**합니다.

## 3. 표본 지점에 해당 피처가 없어 미확인 (레이어 자체는 제공됨)

`lt_c_uq124`(방화지구) · `uq125`(방재지구) · `uq126`(보호지구) · `uq129`(개발진흥지구) ·
`uq130`(특정용도제한지구) · `uf151`(산림보호구역) · `um221`(야생동식물보호) · `um301`(대기환경규제지역) ·
`um710`(상수원보호) · `um901`(습지보호지역) · `wgisarwet`(습지보호구역) · `wgisnpdo`·`wgisnpgun`(도립/군립자연공원) ·
`uo501`(전통사찰보존) · `up201`(재해위험지구) · `up401`(급경사재해예방지역) · `tfismpa`(해양보호구역) ·
`lt_c_lhblpn`(토지이용계획도) · 군사·항공 계열(`aisresc`,`aisctrc`,`aisatzc`,`aismoac`,`aiscatc`,`aisacmc`)

→ 상태 `NOT_FOUND`는 **레이어 부재가 아니라 해당 지점에 피처가 없다는 뜻**입니다.
   `uf901`·`aisprhc`처럼 해당 지점을 찾아 넣으면 정상 조회됨을 확인했습니다.

## 4. Data API로는 조회 불가 (WFS 목록에는 있으나 `data` 파라미터 거부)

`lt_c_adsido` · `lt_c_adsigg` · `lt_c_ademd` · `lt_c_adri` · `dt_d160`(토지소유공간정보)

응답: `data 파라미터의 값이 유효한 범위를 넘었습니다`

→ 지점 하나의 행정구역 판별은 `geocode.py`의 **역지오코딩 API**로 처리합니다.

> **정정 (2026-08)** — 조회 불가한 것은 **데이터 API(`req/data`)뿐**입니다.
> **WFS 엔드포인트(`req/wfs`)로는 `lt_c_adsigg`가 정상 조회됩니다.**
> 사업구역을 관할 지자체별로 나누려면 경계 **폴리곤**이 필요한데
> 역지오코딩으로는 얻을 수 없어, `jurisdiction.py`가 이 경로를 씁니다.
>
> ```
> GET https://api.vworld.kr/req/wfs
>     SERVICE=WFS  REQUEST=GetFeature  VERSION=1.1.0
>     TYPENAME=lt_c_adsigg          ← 소문자만 통함 (대문자는 거부)
>     SRSNAME=EPSG:5179  BBOX=minx,miny,maxx,maxy
>     OUTPUT=application/json
> ```
> - VERSION은 **1.1.0**이어야 합니다. 2.0.0은 ServiceExceptionReport를 돌려줍니다
> - 속성: `sig_cd`(5자리 코드, PNU 앞 5자리와 동일) · `sig_kor_nm` · `full_nm`
> - 좌표계가 **EPSG:5179 그대로** 와서 재투영이 필요 없습니다
> - 한 지자체가 여러 피처로 쪼개져 옵니다(삼척시 5건) — `sig_cd`로 합쳐야 합니다
> - 시군구 경계는 **육지만** 덮습니다. 해안 구역은 바다만큼 미포함으로 남습니다

## 5. V-World가 제공하지 않아 별도 조달이 필요한 레이어

| 항목 | 조달 방법 |
|---|---|
| 생태자연도 | 환경공간정보서비스(EGIS) — 공개 REST 미확인 |
| 보전산지(산지구분도) | 산림청 — V-World 목록에 없음 (`fsdifrsts`는 토양도라 대체 불가) |
| 산사태위험등급 | 산림청 산사태정보시스템 |
| 변전소·송전선로 | 한전 미공개 → **OSM Overpass**로 대체 조달 (8단계) |
| 국가유산 지정구역 상세·현상변경 허용기준 | 국가유산청 SHP 직접 적재 (5·6단계) — `backend/data/heritage/` |
