"""
기상청 API허브 — 관측지점 목록 실측 프로브
---------------------------------------------------------------
ASOS 관측소의 **지점번호·명칭·위경도·표고**를 확보한다.
이 표가 있어야 검토 지점에서 최근접 관측소를 자동 선정할 수 있다.

  https://apihub.kma.go.kr/api/typ01/url/stn_inf.php?inf=SFC&tm=..&authKey=..

인증키 발급 및 활용신청
  1) https://apihub.kma.go.kr → 회원가입 → 로그인 → 마이페이지 → 인증키
     ※ 공공데이터포털(data.go.kr)과 **별개 사이트**입니다. 키가 다릅니다.
  2) .env 에 `KMA_APIHUB_KEY=발급받은키` 등록
  3) **API별 활용신청이 필요합니다.** 가입만으로는 모든 API가 403입니다(실측 확인).
     관측 → 지상관측 → "지점정보(stn_inf)" 활용신청

인증 파라미터는 `authKey` 입니다 (403 응답으로 확정 — 401은 키 자체가 무효일 때).

사용:  python scripts/kma_station_probe.py
산출:  backend/data/kma/stations.json  (시드 입력)
       backend/data/kma/stn_inf_raw.txt (원본 응답 — 근거 보존)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'backend' / 'data' / 'kma'
BASE = 'https://apihub.kma.go.kr/api/typ01/url/stn_inf.php'

#: 인증 파라미터명 후보 — 실제 키로 호출해 되는 쪽을 고른다 (더미 키로는 구분 불가)
KEY_PARAM_CANDIDATES = ('authKey', 'apiKey', 'serviceKey')


def load_key() -> str:
    p = ROOT / '.env'
    if not p.exists():
        sys.exit('.env 파일이 없습니다.')
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('KMA_APIHUB_KEY=') and not line.startswith('#'):
            v = line.split('=', 1)[1].strip()
            if v:
                return v
    sys.exit('KMA_APIHUB_KEY가 비어 있습니다. '
             'https://apihub.kma.go.kr 에서 발급받아 .env에 등록하십시오.')


def _decode(raw: bytes) -> str:
    """
    응답은 **CP949**로 내려온다 (실측 확인). UTF-8로 읽으면 지점명이 전부 깨진다.
    오류 응답(JSON)은 UTF-8이라 둘 다 시도한다.
    """
    for enc in ('cp949', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def fetch(key_param: str, key: str, tm: str) -> tuple[int, str]:
    params = {'inf': 'SFC', 'stn': '', 'tm': tm, 'help': '1', key_param: key}
    url = f'{BASE}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'windsite-kma-probe/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, _decode(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _decode(e.read())


def parse_stations(text: str) -> list[dict]:
    """
    공백 구분 텍스트를 파싱한다. 헤더 주석(#)이 컬럼 순서를 알려준다.

      STN_ID  LON  LAT  STN_SP  HT  HT_PA  HT_TA  HT_WD  HT_RN  STN_ID  STN_KO  STN_EN ...

    HT     관측지점 해발고도(m)
    HT_WD  **풍향·풍속계 관측높이(m)** — 허브고도 환산의 기준이 되므로 반드시 보관한다
    """
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split()
        if len(parts) < 12 or not parts[0].isdigit():
            continue
        try:
            lon, lat = float(parts[1]), float(parts[2])
        except (ValueError, IndexError):
            continue
        if not (33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.5):
            continue

        def num(idx: int) -> float | None:
            try:
                v = float(parts[idx])
            except (ValueError, IndexError):
                return None
            return None if v <= -999 else v

        name = next((p for p in parts[9:] if re.search(r'[가-힣]', p)), '')
        rows.append({
            'stn_id': parts[0],
            'name': name,
            'lat': lat,
            'lng': lon,
            'alt_m': num(4),            # HT — 지점 해발고도
            'anemometer_h_m': num(7),   # HT_WD — 풍속계 관측높이
        })
    return rows


def main() -> None:
    key = load_key()
    OUT.mkdir(parents=True, exist_ok=True)

    for kp in KEY_PARAM_CANDIDATES:
        status, body = fetch(kp, key, '202508010000')
        head = re.sub(r'\s+', ' ', body[:120])

        # 403은 "키는 유효하나 해당 API를 활용신청하지 않음"이다.
        # 401(키 오류)과 구분해서 안내해야 엉뚱한 곳을 고치지 않는다.
        if status == 403:
            print(f'[403] 인증 파라미터 = {kp} (키는 유효합니다)')
            print('      해당 API가 활용신청되지 않았습니다.')
            print('      https://apihub.kma.go.kr 로그인 → 관측 → 지상관측 →')
            print('      "지점정보(stn_inf)" 활용신청 후 다시 실행하십시오.')
            return

        if status == 200 and '인증키' not in body:
            print(f'[OK] 인증 파라미터 = {kp}')
            (OUT / 'stn_inf_raw.txt').write_text(body, encoding='utf-8')
            stations = parse_stations(body)
            print(f'     관측지점 {len(stations)}건 파싱')
            if stations:
                for s in stations[:5]:
                    print(f"       {s['stn_id']:>4} {s['name']:<8} "
                          f"{s['lat']:.4f},{s['lng']:.4f} "
                          f"표고 {s['alt_m']}m 풍속계 {s['anemometer_h_m']}m")
                (OUT / 'stations.json').write_text(
                    json.dumps(stations, ensure_ascii=False, indent=1), encoding='utf-8')
                print(f'\n산출 → {OUT / "stations.json"}')
            else:
                print('     ⚠️ 파싱 결과가 비었습니다. 원본을 확인하십시오 → '
                      f'{OUT / "stn_inf_raw.txt"}')
            return
        print(f'[{status}] {kp}: {head}')

    print('\n모든 후보가 401(유효하지 않은 인증키)입니다. '
          'KMA_APIHUB_KEY 값을 다시 확인하십시오.')


if __name__ == '__main__':
    main()
