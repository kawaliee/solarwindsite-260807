"""
V-World 레이어 실측 프로브
---------------------------------------------------------------
목적: 풍력 입지검토에 쓰이는 규제 레이어의 **실제 레이어 ID와 속성명**을
      추측이 아니라 V-World 서버 응답으로 확정한다.

수행 내용
  1) WFS GetCapabilities  → 제공 레이어 ID 전체 목록
  2) WFS DescribeFeatureType → 레이어별 속성(필드)명·타입
  3) 데이터 API GetFeature 표본 호출 → 실제 속성값 형태 확인

산출: backend/data/vworld/
  - wfs_capabilities.xml       (원본)
  - layers.json                (레이어 ID → 제목)
  - describe/<레이어>.xml      (속성 정의 원본)
  - probe_result.json          (요약 — 시드 작성 근거)

사용:  python scripts/vworld_probe.py [--sample-lat 35.06 --sample-lng 126.98]
※ 인증키는 .env에서 읽으며 출력물에 절대 기록하지 않는다.
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
OUT = ROOT / 'backend' / 'data' / 'vworld'

WFS_URL = 'https://api.vworld.kr/req/wfs'
DATA_URL = 'https://api.vworld.kr/req/data'


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
    if not env.get('VWORLD_API_KEY'):
        sys.exit('VWORLD_API_KEY가 비어 있습니다.')
    return env


def fetch(url: str, params: dict[str, str], timeout: int = 60) -> bytes:
    full = f'{url}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(full, headers={'User-Agent': 'windsite-probe/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def redact(text: str, key: str) -> str:
    """인증키가 응답 본문(에러 메시지 등)에 반사되어 나오는 경우 제거."""
    return text.replace(key, '<REDACTED>') if key else text


# ----------------------------------------------------------------------
def parse_capabilities(xml: str) -> list[dict[str, str]]:
    """FeatureType 블록에서 Name/Title/기타를 추출 (네임스페이스 무관 정규식)."""
    layers: list[dict[str, str]] = []
    for block in re.findall(r'<(?:\w+:)?FeatureType[^>]*>(.*?)</(?:\w+:)?FeatureType>', xml, re.S):
        def tag(t: str) -> str:
            m = re.search(rf'<(?:\w+:)?{t}[^>]*>(.*?)</(?:\w+:)?{t}>', block, re.S)
            return m.group(1).strip() if m else ''
        name = tag('Name')
        if name:
            layers.append({'name': name, 'title': tag('Title'), 'abstract': tag('Abstract')[:200]})
    return layers


def parse_describe(xml: str) -> list[dict[str, str]]:
    """xsd:element 에서 속성명/타입 추출."""
    out: list[dict[str, str]] = []
    for m in re.finditer(r'<(?:\w+:)?element\b([^>]*)/?>', xml):
        attrs = m.group(1)
        name = re.search(r'name="([^"]+)"', attrs)
        typ = re.search(r'type="([^"]+)"', attrs)
        if name:
            out.append({'name': name.group(1), 'type': typ.group(1) if typ else ''})
    return out


# ----------------------------------------------------------------------
#: 풍력 입지검토 대상 레이어 — **GetCapabilities 실측 결과(177건)에서 직접 선별**한 것.
#: 추측한 ID가 아니며, main()에서 layers.json 실측 목록과 대조해 존재 여부를 검증한다.
CURATED: dict[str, list[str]] = {
    # 용도지역 (국토계획법 제36조) — V-World는 4개 지역을 별도 레이어로 제공
    '용도지역': ['lt_c_uq111', 'lt_c_uq112', 'lt_c_uq113', 'lt_c_uq114'],
    # 용도지구·구역
    '용도지구': ['lt_c_uq121', 'lt_c_uq123', 'lt_c_uq124', 'lt_c_uq125',
              'lt_c_uq126', 'lt_c_uq128', 'lt_c_uq129', 'lt_c_uq130'],
    '개발제한구역': ['lt_c_ud801'],
    '개발행위허가제한': ['lt_c_upisuq171'],
    '도시자연공원구역': ['lt_c_uq162'],
    # 산림
    '백두대간': ['lt_c_uf901'],
    '산림보호구역': ['lt_c_uf151'],
    '산림입지': ['lt_c_fsdifrsts'],
    '임업산촌진흥권역': ['lt_c_uf602'],
    '하천망': ['lt_c_wkmstrm'],
    # 자연·환경
    '자연공원': ['lt_c_wgisnpgug', 'lt_c_wgisnpdo', 'lt_c_wgisnpgun'],
    '습지보호': ['lt_c_um901', 'lt_c_wgisarwet'],
    '야생생물보호': ['lt_c_um221'],
    '상수원보호': ['lt_c_um710'],
    '대기환경규제': ['lt_c_um301'],
    '해양보호구역': ['lt_c_tfismpa'],
    # 농지
    '농업진흥지역': ['lt_c_agrixue101'],
    '영농여건불리농지': ['lt_c_agrixue102'],
    # 재해
    '재해위험지구': ['lt_c_up201'],
    '급경사재해예방': ['lt_c_up401'],
    '산불위험': ['lt_c_kfdrssigugrade'],
    # 문화·교육
    '국가유산보호구역': ['lt_c_uo301'],
    '전통사찰보존': ['lt_c_uo501'],
    '교육환경보호구역': ['lt_c_uo101'],
    # 군사·항공 (풍력 높이 규제 직결)
    '비행금지구역': ['lt_c_aisprhc'],
    '비행제한구역': ['lt_c_aisresc'],
    '관제권': ['lt_c_aisctrc'],
    '비행장교통구역': ['lt_c_aisatzc'],
    '군작전구역': ['lt_c_aismoac'],
    '훈련구역': ['lt_c_aiscatc'],
    '공중전투기동훈련장': ['lt_c_aisacmc'],
    '항공위험구역': ['lt_c_aisdngc'],
    '접근관제구역': ['lt_c_aistmac'],
    '경계구역': ['lt_c_aisaltc'],
    '공중급유구역': ['lt_c_aisrflc'],
    '제한고도': ['lt_l_aisrouteu'],
    '초경량비행장치공역': ['lt_c_aisuac'],
    '헬기장': ['lt_p_aishcstrip'],
    '경량항공기이착륙장': ['lt_c_aisfldc'],
    # 지적·소유
    '연속지적': ['lp_pa_cbnd_bubun'],
    '토지소유': ['dt_d160'],
    '토지이용계획': ['lt_c_lhblpn'],
    # 이격거리 산정 기초
    '건물': ['lt_c_spbd'],
    '도로': ['lt_l_sprd'],
    # 행정구역
    '행정구역': ['lt_c_adsido', 'lt_c_adsigg', 'lt_c_ademd', 'lt_c_adri'],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', action='append', default=[],
                    metavar='LAT,LNG,LABEL',
                    help='표본 지점 (반복 지정 가능). 미지정 시 기본 3개 지점 사용')
    ap.add_argument('--buffer', type=int, default=2000)
    ap.add_argument('--skip-describe', action='store_true')
    ap.add_argument('--skip-sample', action='store_true')
    args = ap.parse_args()

    points: list[tuple[float, float, str]] = []
    for s in args.sample:
        lat, lng, *rest = s.split(',')
        points.append((float(lat), float(lng), rest[0] if rest else s))
    if not points:
        points = [
            (35.0648, 126.9895, '광주 도심'),
            (35.0570, 126.9860, '전남 화순 산간'),
            (35.6470, 128.7340, '경북 청도 산간'),
        ]

    env = load_env()
    key = env['VWORLD_API_KEY']
    domain = env.get('VWORLD_DOMAIN') or 'localhost'
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'describe').mkdir(exist_ok=True)

    # 1) GetCapabilities -------------------------------------------------
    print('[1/3] WFS GetCapabilities …')
    raw = fetch(WFS_URL, {
        'SERVICE': 'WFS', 'REQUEST': 'GetCapabilities', 'VERSION': '1.1.0',
        'KEY': key, 'DOMAIN': domain,
    })
    xml = redact(raw.decode('utf-8', 'replace'), key)
    (OUT / 'wfs_capabilities.xml').write_text(xml, encoding='utf-8')
    layers = parse_capabilities(xml)
    print(f'      레이어 {len(layers)}건')
    if not layers:
        print('      ⚠️ 레이어를 파싱하지 못했습니다. 응답 앞부분:')
        print(xml[:800])
    (OUT / 'layers.json').write_text(
        json.dumps(layers, ensure_ascii=False, indent=2), encoding='utf-8')

    # 2) 큐레이션 레이어를 실측 목록과 대조 -------------------------------
    index = {l['name']: l for l in layers}
    matched: dict[str, list[dict[str, str]]] = {}
    missing: dict[str, list[str]] = {}
    for topic, ids in CURATED.items():
        matched[topic] = [index[i] for i in ids if i in index]
        absent = [i for i in ids if i not in index]
        if absent:
            missing[topic] = absent
    if missing:
        print('      ⚠️ 실측 목록에 없는 레이어:', missing)

    # 3) DescribeFeatureType --------------------------------------------
    describe: dict[str, list[dict[str, str]]] = {}
    if not args.skip_describe:
        targets = sorted({h['name'] for hits in matched.values() for h in hits})
        print(f'[2/3] DescribeFeatureType {len(targets)}건 …')
        for name in targets:
            try:
                d = fetch(WFS_URL, {
                    'SERVICE': 'WFS', 'REQUEST': 'DescribeFeatureType', 'VERSION': '1.1.0',
                    'TYPENAME': name, 'KEY': key, 'DOMAIN': domain,
                })
                dx = redact(d.decode('utf-8', 'replace'), key)
                safe = re.sub(r'[^\w.-]', '_', name)
                (OUT / 'describe' / f'{safe}.xml').write_text(dx, encoding='utf-8')
                describe[name] = parse_describe(dx)
                print(f'      {name}: 속성 {len(describe[name])}개')
            except Exception as e:                                  # noqa: BLE001
                describe[name] = []
                print(f'      {name}: 실패 ({type(e).__name__})')

    # 4) 데이터 API 표본 호출 -------------------------------------------
    #    지점마다 존재하는 레이어가 다르므로 여러 지점을 돌며
    #    "한 번이라도 피처가 조회된" 레이어의 실제 속성 키를 확보한다.
    samples: dict[str, dict] = {}
    if not args.skip_sample:
        all_ids = sorted({h['name'] for hits in matched.values() for h in hits})
        print(f'[3/3] GetFeature 표본 — 레이어 {len(all_ids)} × 지점 {len(points)} …')
        for name in all_ids:
            rec = {'statuses': [], 'feature_count': 0, 'property_keys': [],
                   'sample_properties': {}, 'geometry_type': '', 'hit_at': ''}
            for lat, lng, label in points:
                try:
                    body = fetch(DATA_URL, {
                        'service': 'data', 'request': 'GetFeature', 'data': name,
                        'key': key, 'domain': domain,
                        'geomFilter': f'POINT({lng} {lat})',
                        'buffer': str(args.buffer), 'size': '5',
                        'format': 'json', 'crs': 'EPSG:4326', 'geometry': 'true',
                    })
                    j = json.loads(redact(body.decode('utf-8', 'replace'), key))
                    resp = j.get('response', {})
                    st = resp.get('status')
                    rec['statuses'].append(f'{label}:{st}')
                    feats = ((resp.get('result') or {})
                             .get('featureCollection', {}).get('features', []))
                    if feats and not rec['property_keys']:
                        props = feats[0].get('properties') or {}
                        rec.update({
                            'feature_count': len(feats),
                            'property_keys': sorted(props.keys()),
                            'sample_properties': props,
                            'geometry_type': (feats[0].get('geometry') or {}).get('type', ''),
                            'hit_at': label,
                        })
                except Exception as e:                              # noqa: BLE001
                    rec['statuses'].append(f'{label}:EXC({type(e).__name__})')
            samples[name] = rec
            flag = '●' if rec['property_keys'] else '○'
            print(f"      {flag} {name:<24} {rec['hit_at'] or '-':<12} "
                  f"attrs={len(rec['property_keys'])}")

    (OUT / 'probe_result.json').write_text(json.dumps({
        'sample_points': [{'lat': p[0], 'lng': p[1], 'label': p[2]} for p in points],
        'buffer_m': args.buffer,
        'layer_total': len(layers),
        'curated': CURATED,
        'missing_from_capabilities': missing,
        'matched': matched,
        'describe': describe,
        'samples': samples,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n완료 → {OUT}')


if __name__ == '__main__':
    main()
