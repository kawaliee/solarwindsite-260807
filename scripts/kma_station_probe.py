"""
기상청 API허브 — 관측지점 목록 실측 프로브
---------------------------------------------------------------
ASOS 관측소의 **지점번호·명칭·위경도·표고**를 확보한다.
이 표가 있어야 검토 지점에서 최근접 관측소를 자동 선정할 수 있다.

  https://apihub.kma.go.kr/api/typ01/url/stn_inf.php?inf=SFC&tm=..&authKey=..

인증키 발급
  https://apihub.kma.go.kr → 회원가입 → 로그인 → 마이페이지 → 인증키
  ※ 공공데이터포털(data.go.kr)과 **별개 사이트**입니다. 키가 다릅니다.
  .env 에 `KMA_APIHUB_KEY=발급받은키` 로 등록하십시오.

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


def fetch(key_param: str, key: str, tm: str) -> tuple[int, str]:
    params = {'inf': 'SFC', 'stn': '', 'tm': tm, 'help': '1', key_param: key}
    url = f'{BASE}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'windsite-kma-probe/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def parse_stations(text: str) -> list[dict]:
    """
    고정폭 텍스트 응답을 파싱한다. 주석행(#)에 컬럼 정의가 들어 있어
    STN_ID / LON / LAT / STN_SP(표고) / STN_KO(지점명) 위치를 그대로 읽는다.
    """
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split()
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        try:
            stn_id = parts[0]
            lon = float(parts[1])
            lat = float(parts[2])
        except (ValueError, IndexError):
            continue
        if not (33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.5):
            continue
        # 지점명은 한글이 처음 나오는 토큰
        name = next((p for p in parts[3:] if re.search(r'[가-힣]', p)), '')
        alt = 0.0
        for p in parts[3:]:
            try:
                v = float(p)
            except ValueError:
                continue
            if 0 <= v <= 2000:
                alt = v
                break
        rows.append({'stn_id': stn_id, 'name': name, 'lat': lat, 'lng': lon, 'alt_m': alt})
    return rows


def main() -> None:
    key = load_key()
    OUT.mkdir(parents=True, exist_ok=True)

    for kp in KEY_PARAM_CANDIDATES:
        status, body = fetch(kp, key, '202508010000')
        head = re.sub(r'\s+', ' ', body[:120])
        if status == 200 and '인증키' not in body:
            print(f'[OK] 인증 파라미터 = {kp}')
            (OUT / 'stn_inf_raw.txt').write_text(body, encoding='utf-8')
            stations = parse_stations(body)
            print(f'     관측지점 {len(stations)}건 파싱')
            if stations:
                for s in stations[:5]:
                    print(f"       {s['stn_id']:>4} {s['name']:<8} "
                          f"{s['lat']:.4f},{s['lng']:.4f} 표고 {s['alt_m']}m")
                (OUT / 'stations.json').write_text(
                    json.dumps(stations, ensure_ascii=False, indent=1), encoding='utf-8')
                print(f'\n산출 → {OUT / "stations.json"}')
            else:
                print('     ⚠️ 파싱 결과가 비었습니다. 원본을 확인하십시오 → '
                      f'{OUT / "stn_inf_raw.txt"}')
            return
        print(f'[{status}] {kp}: {head}')

    print('\n모든 인증 파라미터 후보가 실패했습니다. 키 값을 다시 확인하십시오.')


if __name__ == '__main__':
    main()
