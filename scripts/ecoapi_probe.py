"""
국립생태원 생태자연도 API 실측 프로브
---------------------------------------------------------------
공공데이터포털 「국립생태원_생태자연도 서비스」(B553084)의 **실제 오퍼레이션 경로와
응답 속성명**을 서버 응답으로 확정한다. V-World 때와 같은 이유다 — 문서만 보고
파라미터를 추측하면 "호출은 되는데 판정이 성립하지 않는" 상태가 된다.

확인 대상
  1) WFS 오퍼레이션 경로 (getEcologyzmpWFS 등 후보를 순차 시도)
  2) 좌표계 — 문서상 EPSG:5186(중부원점 GRS80). bbox는 miny,minx,maxy,maxx 순
  3) 등급이 담긴 속성명 (etc_grad / grad / 등급 …)

사용:  python scripts/ecoapi_probe.py [--lat 36.61 --lng 129.19] [--buffer 1000]
※ 인증키는 .env에서 읽으며 출력물에 기록하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'backend' / 'data' / 'ecoapi'

DEFAULT_BASE = 'https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService'

#: 오퍼레이션 경로 후보 — 어느 것이 맞는지 서버 응답으로 고른다
OPERATION_CANDIDATES = [
    'wfs/getEcologyzmpWFS',
    'wfs/getEcologyzmpWfs',
    'getEcologyzmpWFS',
    'wfs',
]
#: 레이어명 후보 (문서 예시: tbl_opn_eczm)
LAYER_CANDIDATES = ['tbl_opn_eczm', 'eczm', 'ecologyzmp']

#: 생태자연도 원 좌표계 (중부원점 GRS80)
ECO_CRS = 'EPSG:5186'


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / '.env'
    if not p.exists():
        sys.exit('.env 파일이 없습니다.')
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()
    key = env.get('ECO_API_KEY') or env.get('DATA_GO_KR_KEY')
    if not key:
        sys.exit('DATA_GO_KR_KEY(또는 ECO_API_KEY)가 비어 있습니다. '
                 '공공데이터포털 마이페이지의 인증키를 넣으십시오.')
    # Encoding 형태(퍼센트 인코딩)로 넣어도 동작하도록 디코딩해 통일한다.
    # 그대로 두면 쿼리 생성 시 이중 인코딩되어 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 난다.
    env['_KEY'] = urllib.parse.unquote(key) if '%' in key else key
    return env


def fetch(url: str, params: dict[str, str], timeout: int = 60) -> tuple[int, str]:
    full = f'{url}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(full, headers={'User-Agent': 'windsite-ecoprobe/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:                          # noqa: PERF203
        return e.code, e.read().decode('utf-8', 'replace')


def redact(text: str, key: str) -> str:
    return text.replace(key, '<REDACTED>') if key else text


def to_bbox(lat: float, lng: float, buffer_m: int) -> str:
    """WGS84 지점 → EPSG:5186 bbox 문자열 (miny,minx,maxy,maxx)"""
    from pyproj import Transformer
    t = Transformer.from_crs('EPSG:4326', ECO_CRS, always_xy=True)
    x, y = t.transform(lng, lat)
    return f'{y - buffer_m},{x - buffer_m},{y + buffer_m},{x + buffer_m}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--lat', type=float, default=36.6100)
    ap.add_argument('--lng', type=float, default=129.1900)
    ap.add_argument('--buffer', type=int, default=1000)
    args = ap.parse_args()

    env = load_env()
    key = env['_KEY']
    base = env.get('ECO_API_BASE') or DEFAULT_BASE
    OUT.mkdir(parents=True, exist_ok=True)

    bbox = to_bbox(args.lat, args.lng, args.buffer)
    print(f'표본 지점 {args.lat},{args.lng} · buffer {args.buffer}m')
    print(f'bbox({ECO_CRS}) = {bbox}\n')

    results: list[dict] = []
    winner: dict | None = None

    for op in OPERATION_CANDIDATES:
        for layer in LAYER_CANDIDATES:
            url = f'{base.rstrip("/")}/{op}'
            params = {
                'serviceKey': key,
                'srs': ECO_CRS,
                'bbox': bbox,
                'layers': layer,
            }
            status, body = fetch(url, params)
            body = redact(body, key)
            head = re.sub(r'\s+', ' ', body[:220])
            ok = status == 200 and 'ERROR' not in body.upper()[:400]
            print(f'[{status}] {op:<26} layer={layer:<14} {"OK" if ok else "…"} {head[:110]}')
            results.append({'op': op, 'layer': layer, 'status': status,
                            'head': head, 'ok': ok})
            if ok and winner is None:
                winner = {'op': op, 'layer': layer, 'url': url, 'body': body}

    if winner:
        (OUT / 'wfs_sample.xml').write_text(winner['body'], encoding='utf-8')
        print(f'\n성공한 조합: {winner["op"]} / layer={winner["layer"]}')
        print('속성 후보:', sorted(set(re.findall(r'<(?:\w+:)?(\w+)>', winner['body'])))[:40])
    else:
        print('\n성공한 조합이 없습니다. 응답 본문을 확인하십시오 → '
              f'{OUT / "attempts.json"}')

    (OUT / 'attempts.json').write_text(
        json.dumps({'base': base, 'bbox': bbox, 'crs': ECO_CRS,
                    'sample_point': {'lat': args.lat, 'lng': args.lng},
                    'results': results}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'\n기록 → {OUT}')


if __name__ == '__main__':
    main()
