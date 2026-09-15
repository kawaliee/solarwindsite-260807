# CLAUDE.md — 재생에너지 입지타당성 검토 시스템 개발 지침

Claude Code가 이 저장소를 열었을 때 구조와 규칙을 바로 파악하기 위한 안내입니다.

---

## 1. 이 저장소는 무엇인가

좌표를 찍으면 공공 공간정보 API 20여 종을 병렬 조회해 입지 규제를 자동
스크리닝하고, 인허가 로드맵과 docx 보고서를 내는 시스템입니다.

- **풍력** — 호기 배치선 단위 판정, 이격거리 조례, 재현바람장 풍황, 유효지역
- **태양광** — 필지 단위 정밀판정, 가용면적 산출, 일사량
- **운영관리 Dashboard** — 전국 사업장 분포 (프런트 전용, 백엔드 의존 없음)

풍력·태양광은 **같은 엔진**을 쓰고 `apps/windsite/energy.py`의 `EnergyProfile`로
갈립니다. 한쪽에 기능을 넣을 때 다른 쪽에 어떻게 비치는지 반드시 확인하십시오.

---

## 2. 가장 중요한 규칙

> **추측하지 않는다.** 조회에 실패하거나 판정 기준이 없으면 `UNKNOWN`으로 남기고,
> **왜 모르는지**를 함께 적는다.

입지 검토는 잘못된 "가능" 판정 하나가 사업 손실로 이어집니다. 이 저장소에서
**조회 안 됨과 제약 없음은 전혀 다른 사실**이며, 절대 같은 문구로 내지 않습니다.

이어지는 규칙들은 전부 이 원칙에서 나옵니다.

### 2.1. 실측하지 않은 것을 코드에 쓰지 않는다

레이어 ID·속성명·API 응답 형식은 **서버 응답으로 직접 확인한 값만** 씁니다.
문서에 적힌 대로 동작하지 않는 API가 많습니다. 실제로 겪은 예:

- V-World 일부 레이어는 데이터 API(`req/data`)로는 거절되고 WFS로만 나옵니다.
- WFS에 `SRSNAME=EPSG:5179`를 주면 응답이 **이미 미터 좌표**입니다. 여기에
  `to_metric`을 또 걸면 좌표가 발산합니다.
- 기상청 재현바람장 API는 `itv`가 10 또는 30만 유효하고, 3일 요청이 끝에서
  잘려 돌아옵니다(112/144행). 받은 마지막 시각부터 이어 받아야 채워집니다.

확인되지 않은 코드는 **검출 사실만 알리고 판정을 보류**합니다.

### 2.2. 법령은 원문으로 확인한다

조문을 인용하기 전에 `lawapi.py`로 국가법령정보 원문을 조회해 대조하십시오.
폐지된 조문에 근거한 절차가 실제로 발견된 적이 있습니다.

`verify_laws --apply`가 `confidence`를 관리합니다. **시드 명령이 이 값을 덮어쓰면
안 됩니다** — 시드가 선언한 경우에만 설정하십시오.

> `LAW_API_OC`는 랜덤 키가 아니라 신청 이메일의 `@` 앞부분이며, 호출 서버의
> **공인 IP를 open.law.go.kr에 등록**해야 통과합니다. IP가 바뀌면 재등록이 필요합니다.

### 2.3. 판정 문구에 조회 범위의 한계를 적는다

필지 단위인지 구역 전체인지, 거리가 실제로 측정된 값인지, 대표 지점 한 곳의
값인지를 밝힙니다. 보고서를 받는 사람이 범위를 오해하면 판정이 맞아도 소용없습니다.

---

## 3. 빌드 및 주요 명령

### 컨테이너
```bash
docker compose up -d                      # 전체 기동
docker compose logs -f backend            # 백엔드 로그
docker compose exec backend python manage.py check
```

### 초기 데이터
```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_windsite          # 풍력 법령·절차
docker compose exec backend python manage.py seed_solar             # 태양광 법령·절차
docker compose exec backend python manage.py seed_windsite_layers   # V-World 레이어
```

### 운영
```bash
docker compose exec backend python manage.py verify_laws --apply              # 법령 현행 대조
docker compose exec backend python manage.py sync_ordinances --sigungu 화순군 --apply
docker compose exec backend python manage.py sync_korec                       # 전기위원회 재결례
docker compose exec -d backend python manage.py collect_rawwind --plan <UUID> # 재현바람장
```

> `collect_rawwind`는 좌표·고도당 약 1시간 걸립니다. 반드시 `-d`로 띄우십시오.
> 이미 99% 이상 받아 둔 구간은 건너뜁니다(`--force`로 재수집).

---

## 4. 구조

```
backend/apps/windsite/
  providers/       데이터 소스 어댑터 — 하나가 한 자료원을 맡는다
  engine.py        병렬 실행 · 플래그 도출 · 점수 산출
  energy.py        EnergyProfile — 풍력/태양광 분기의 단일 지점
  available.py     가용면적 산출 · 항목 병합
  screening.py     구역 스크리닝 (제약없음/조건부/배제)
  geo.py           EPSG:5179 공간연산 (shapely · pyproj)
  ordinances.py    조례 수집 · 이격거리 추출
  lawapi.py        국가법령정보 OPEN API
  permits.py       인허가 절차 · 플래그 조건
  rawwind.py       기상청 재현바람장 · 유효지역 계산
  coast.py         유효지역 해역 제외
  area_report.py   docx 보고서 조립
  report_cards.py  PART 2 항목별 카드
  maps.py          지도 렌더링 (matplotlib)
frontend/src/features/windsite/    풍력·태양광 공용 화면
frontend/src/features/ops/         운영관리 Dashboard
```

좌표계는 **EPSG:5179(UTM-K)** 가 미터 연산 기준입니다. 생태자연도·산사태
래스터 등 일부 자료는 EPSG:5186이라 재투영합니다. PostGIS 없이 bbox 1차 필터 +
shapely 정밀 연산으로 처리합니다.

---

## 5. 후속 개발 규칙

- **임의 순위 보정 금지** — 특정 결과를 위로 올리려고 메타데이터나 필터를
  하드코딩으로 강제하지 마십시오.
- **보고서는 버튼으로만** — 수정·검증 중 docx를 디스크에 만들지 마십시오. 느려집니다.
  카드 렌더링은 메모리에서 확인합니다.
- **인증키를 소스에 쓰지 않는다** — 전부 `.env`에서 읽습니다. 키가 없으면 해당
  항목은 자동으로 `UNKNOWN` 판정됩니다.
- **사업지 좌표·사업명 등 사내 정보를 커밋에 남기지 않는다.**
- 원본 공간데이터(`backend/data/` 대용량)는 `.gitignore` 처리돼 있습니다. 재수급
  방법은 각 디렉터리의 README를 보십시오.

---

## 6. 문서

| 문서 | 내용 |
|---|---|
| [docs/WINDSITE_API_KEYS.md](docs/WINDSITE_API_KEYS.md) | 공공 API 키 발급·등록 |
| [docs/WINDSITE_VWORLD_LAYERS.md](docs/WINDSITE_VWORLD_LAYERS.md) | V-World 레이어 ID·속성명 실측 기록 |
| [docs/WINDSITE_PHASE3.md](docs/WINDSITE_PHASE3.md) | 구현 이력과 남은 한계 |
| [docs/풍력입지검토시스템_시스템기획서.md](docs/풍력입지검토시스템_시스템기획서.md) | 시스템 설계 |
| [docs/태양광입지검토_작업분담.md](docs/태양광입지검토_작업분담.md) | 태양광 확장 범위 |
