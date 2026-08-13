"""
행정구역 개편으로 어긋난 법정동코드 대응표를 **확인해서** 만든다.

지적·지오코딩은 개편된 코드를 쓰는데 건축물대장과 국가공간정보 연계(NED)는
종전 코드로 적재돼 있다. 앞 5자리(시도 2 + 시군구 3)만 다르다.

    지적/지오코딩   12850 31028  전남광주통합특별시 완도군 군외면
    대장/NED        46890 31028  전라남도 완도군 군외면

■ 왜 '응답이 오는 코드'를 그냥 고르면 안 되는가

뒤 5자리를 고정하고 앞 5자리를 훑으면 응답이 오는 조합이 여럿 나온다.
다른 시군구에도 같은 읍면동 코드가 있기 때문이다. 그래서 이 명령은
**건축물대장 응답에 실린 소재지 주소(platPlc)** 를 대조해, 시군구명과
읍면동명이 함께 들어 있을 때만 맞다고 본다. 확인되지 않으면 아무것도
적지 않는다 — 남의 땅 자료를 이 사업지 것으로 붙이는 편보다 낫다.

사용::

    python manage.py map_legacy_codes --sigungu 완도군 --code 12850 --dong 31028
    python manage.py map_legacy_codes --lat 34.295048 --lng 126.581953
"""
from __future__ import annotations

import time

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.windsite import pnu

LEDGER_URL = ('https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo')


class Command(BaseCommand):
    help = '개편 전후 시군구 코드 대응표를 건축물대장 주소로 대조해 만든다'

    def add_arguments(self, parser):
        parser.add_argument('--sigungu', help='시군구명 (예: 완도군)')
        parser.add_argument('--code', help='개편된 시군구 코드 5자리 (예: 12850)')
        parser.add_argument('--dong', help='법정동 코드 5자리 (예: 31028)')
        parser.add_argument('--dong-name', default='', help='읍면동명 (예: 군외면)')
        parser.add_argument('--sido', default='', help='시도명 — 후보 순서를 정한다')
        parser.add_argument('--lat', type=float)
        parser.add_argument('--lng', type=float)
        parser.add_argument('--limit', type=int, default=400,
                            help='시도할 후보 수 상한')

    def handle(self, *args, **o):
        code, dong = o.get('code'), o.get('dong')
        sigungu, dong_name = o.get('sigungu') or '', o.get('dong_name') or ''
        sido = o.get('sido') or ''

        if o.get('lat') is not None and o.get('lng') is not None:
            from apps.windsite.geocode import reverse_geocode
            rg = reverse_geocode(o['lat'], o['lng']) or {}
            st = rg.get('structure') or {}
            sido = sido or rg.get('sido', '')
            sigungu = sigungu or rg.get('sigungu', '')
            dong_name = dong_name or st.get('level4L', '')
            lc = st.get('level4LC') or ''
            if len(lc) >= 10:
                code, dong = code or lc[:5], dong or lc[5:10]

        if not (code and dong and sigungu):
            self.stderr.write('--code, --dong, --sigungu 가 필요합니다 '
                              '(또는 --lat/--lng 로 자동 추출).')
            return

        self.stdout.write(f'대상 — {sido} {sigungu} {dong_name} / 신 코드 {code}{dong}')
        key = getattr(settings, 'BLDG_LEDGER_API_KEY', '')
        if not key:
            self.stderr.write('BLDG_LEDGER_API_KEY 가 없습니다.')
            return

        cands = [c for c in pnu.candidates(sido) if c != str(code)][:o['limit']]
        self.stdout.write(f'후보 {len(cands)}개를 소재지 주소로 대조합니다…')

        found = None
        with httpx.Client(timeout=25.0) as client:
            for i, cand in enumerate(cands, 1):
                addr = self._probe(client, key, cand, dong)
                if addr and sigungu in addr and (not dong_name or dong_name in addr):
                    found = (cand, addr)
                    break
                if i % 40 == 0:
                    self.stdout.write(f'  … {i}/{len(cands)}')
                time.sleep(0.05)

        if not found:
            self.stderr.write(
                '확인된 대응을 찾지 못했습니다. 대응표를 바꾸지 않았습니다. '
                '행정안전부 법정동코드 고시로 직접 확인해 '
                f'data/{pnu.ALIAS_FILE} 에 적으십시오.')
            return

        old, addr = found
        self.stdout.write(self.style.SUCCESS(
            f'확인 — {code} → {old}   (대장 소재지: {addr})'))
        pnu.save_alias(str(code), old)
        self.stdout.write(f'data/{pnu.ALIAS_FILE} 에 저장했습니다.')

    @staticmethod
    def _probe(client, key: str, sigungu_cd: str, bjdong_cd: str) -> str:
        """건축물대장 1건을 받아 소재지 주소를 돌려준다. 없으면 빈 문자열."""
        try:
            r = client.get(LEDGER_URL, params={
                'serviceKey': key, '_type': 'json', 'numOfRows': '1',
                'pageNo': '1', 'sigunguCd': sigungu_cd, 'bjdongCd': bjdong_cd})
            if r.status_code != 200:
                return ''
            body = (r.json().get('response') or {}).get('body') or {}
            if not int(body.get('totalCount') or 0):
                return ''
            item = (body.get('items') or {}).get('item')
            item = item[0] if isinstance(item, list) else item
            return (item or {}).get('platPlc') or ''
        except Exception:                                       # noqa: BLE001
            return ''
