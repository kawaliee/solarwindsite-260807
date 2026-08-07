"""
군사기지 및 군사시설 보호법상 보호구역 어댑터
---------------------------------------------------------------
「군사기지 및 군사시설 보호법」상 **통제보호구역·제한보호구역·비행안전구역**은
좌표 기반 공개 API가 없다고 판단해 오랫동안 UNKNOWN으로 두었으나, 그 판단은 틀렸다.

토지이용계획(V-World NED `getLandUseAttr`)에 **`UNE` 코드군**으로 그대로 실려 있다.
이미 산지구분·소유구분·지역지구 어댑터가 호출하던 바로 그 응답이다. 새 인증키가 필요 없다.

실측 확보한 코드 (2026-08, 접경지 7곳 + 공군·해군기지 13곳)

    UNE110  통제보호구역                 UNE411  비행안전제1구역(전술)
    UNE111  통제보호구역(민통선이북:10km)  UNE412  비행안전제2구역(전술)
    UNE114  통제보호구역(해군기지)         UNE414  비행안전제4구역(전술)
    UNE120  제한보호구역                 UNE415  비행안전제5구역(전술)
    UNE121  제한보호구역(전방지역:25km)    UNE420  지원항공작전기지
    UNE123  제한보호구역(폭발물관련:1km)   UNE421~424 비행안전제1~4구역(지원)
    UNE124  제한보호구역(전술항공:5km)     UNE432  비행안전제2구역(헬기)
    UNE127  제한보호구역(해군기지)         UNE433  비행안전제3구역(헬기)

⚠️ 판별은 **코드 접두**로 한다. 명칭 키워드로 거르면 「교육환경 보호에 관한 법률」의
   `UOA120 상대보호구역`이 '보호구역'에 걸려 오탐된다. 실제로 걸렸다.

⚠️ 한계 — 결과에 반드시 명시한다
   · 필지 단위다. 검토 반경 전체가 아니라 중심 필지 + 면적 상위 필지만 조회한다
   · 경계까지의 거리가 나오지 않는다 ('저촉/접함/포함' 구분만 제공)
   · 비행안전구역은 구역별 표면높이 제한이 달라, 구역 확인만으로 200m급 발전기의
     저촉 여부가 확정되지 않는다. 관할부대 협의는 여전히 필요하다
   · 레이더 전파영향은 이 자료로 판정할 수 없다
"""
from __future__ import annotations

import logging
import re

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import SiteQuery
from .ned import NedClient, ParcelBasedProvider

logger = logging.getLogger(__name__)

#: 군사기지법상 구역 코드군
MIL_PREFIX = 'UNE'

#: 비행안전구역 제N구역 → 명칭에서 뽑는다 (전술/지원/헬기 공통)
_FLIGHT_ORDINAL = re.compile(r'비행안전\s*제\s*([1-6])\s*구역')

_DIFF_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

#: 구역 구분 — (코드 접두, 구분명, 판정, 난이도, 설명)
#: 통제보호구역은 법 제9조상 원칙적으로 건축·공작물 설치가 금지된다.
ZONE_KINDS: list[tuple[str, str, Status, Difficulty, str]] = [
    ('UNE11', '통제보호구역', Status.CONDITIONAL, Difficulty.CRITICAL,
     '통제보호구역에서는 「군사기지 및 군사시설 보호법」 제9조에 따라 건축물의 신축·증축과 '
     '공작물의 설치가 원칙적으로 금지됩니다. 예외적 허용은 관할부대장 협의를 거쳐야 하며 '
     '풍력발전시설이 허용된 전례를 별도로 확인해야 합니다.'),
    ('UNE12', '제한보호구역', Status.CONDITIONAL, Difficulty.HIGH,
     '제한보호구역에서는 같은 법 제13조에 따라 건축·공작물 설치 시 관할부대장과의 협의가 '
     '필요합니다. 협의 결과에 따라 높이 제한 또는 부동의 처분이 있을 수 있습니다.'),
]

#: 비행안전구역 — 구역 번호가 작을수록 표면높이 제한이 엄격하다.
#: 제1구역은 활주로와 그 직상부라 대형 풍력발전기 입지가 사실상 성립하지 않는다.
FLIGHT_ZONE_RULES: dict[int, tuple[Status, Difficulty, str]] = {
    1: (Status.CONDITIONAL, Difficulty.CRITICAL,
        '비행안전 제1구역은 활주로와 그 주변으로 표면높이 제한이 가장 엄격해 '
        '대형 풍력발전기 입지가 사실상 성립하기 어렵습니다.'),
    2: (Status.CONDITIONAL, Difficulty.CRITICAL,
        '비행안전 제2구역은 진입표면 구역으로 표면높이 제한이 매우 낮습니다.'),
    3: (Status.CONDITIONAL, Difficulty.CRITICAL,
        '비행안전 제3구역은 전이표면 구역으로 표면높이 제한이 낮습니다.'),
    4: (Status.CONDITIONAL, Difficulty.HIGH,
        '비행안전 제4구역은 수평표면 구역입니다. 기준 표면높이를 초과하는 구조물은 '
        '관할부대장 협의 대상이며, 200m급 풍력발전기는 초과 가능성이 큽니다.'),
    5: (Status.CONDITIONAL, Difficulty.HIGH,
        '비행안전 제5구역은 원추표면 구역입니다. 표면높이 초과 여부를 실측 표고와 '
        '함께 검토해야 합니다.'),
    6: (Status.CONDITIONAL, Difficulty.HIGH,
        '비행안전 제6구역은 외부수평표면 구역입니다. 표면높이 초과 여부를 확인하십시오.'),
}

#: 작전기지 자체(비행안전구역 번호가 없는 UNE4xx) — 기지 부지에 해당한다
BASE_RULE = (Status.CONDITIONAL, Difficulty.CRITICAL,
             '항공작전기지 부지에 해당합니다. 기지 내 민간 발전시설 설치는 '
             '원칙적으로 성립하지 않습니다.')


# ======================================================================
class MilitaryZoneProvider(ParcelBasedProvider):
    """군사기지·비행안전구역 — 토지이용계획 UNE 코드 기반 판정"""

    category = '안전/문화재'
    item_name = '군사기지·비행안전구역'
    data_source = 'V-World 국가공간정보 연계(NED) 토지이용계획'
    default_law = '군사기지 및 군사시설 보호법'
    default_article = ('제9조(통제보호구역에서의 제한) · 제10조(비행안전구역에서의 제한) · '
                       '제13조(행정기관의 허가등에 관한 협의)')

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(
                reason='검토 반경 내 필지를 특정하지 못해 군사기지법상 보호구역을 '
                       '조회할 수 없습니다.',
                action_required='좌표를 확인하거나 검토 반경을 넓혀 재시도하십시오.',
            )

        zones: dict[str, dict] = {}
        failed = 0
        for p in parcels:
            try:
                rows = NedClient.call('getLandUseAttr', 'landUses', p['pnu'])
            except Exception:                                   # noqa: BLE001
                logger.exception('군사구역 조회 실패 pnu=%s', p['pnu'])
                failed += 1
                continue
            for r in rows:
                code = (r.get('prposAreaDstrcCode') or '').strip()
                name = (r.get('prposAreaDstrcCodeNm') or '').strip()
                # 명칭이 아니라 코드로 거른다 — 교육환경 '상대보호구역' 오탐 방지
                if not code.startswith(MIL_PREFIX) or not name:
                    continue
                z = zones.setdefault(code, {
                    'code': code, 'name': name, 'conflict': 0, 'touch': 0,
                })
                if r.get('cnflcAtNm') == '접함':
                    z['touch'] += 1
                else:
                    z['conflict'] += 1

        if failed and failed == len(parcels):
            return self.unknown(
                reason='토지이용계획 조회에 모두 실패해 군사기지법상 보호구역을 '
                       '판정하지 못했습니다. 데이터 부재가 아니라 조회 실패입니다.',
                action_required='잠시 후 재시도하십시오.',
                difficulty=Difficulty.HIGH,
                why='FETCH',
            )

        coverage = self._coverage_note(parcels, total)
        if not zones:
            return self.item(
                status=Status.POSSIBLE,
                reason=('조회한 필지의 토지이용계획에서 군사기지법상 보호구역'
                        '(통제보호·제한보호·비행안전구역)이 확인되지 않았습니다. '
                        '다만 이는 조회한 필지에 한한 결과이며, 구역 경계까지의 거리는 '
                        '이 자료로 산출되지 않습니다.' + coverage),
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://www.eum.go.kr',
                action_required='최종 확정 전 관할부대에 보호구역 저촉 여부를 조회하십시오.',
                raw={'zones': [], 'parcels': parcels, 'total_parcels': total},
            )

        judged = [self._judge(z) for z in zones.values()]
        known = [j for j in judged if j['status'] is not None]
        unknown = [j for j in judged if j['status'] is None]

        if not known:
            names = ', '.join(f'{j["name"]}[{j["code"]}]' for j in unknown)
            return self.unknown(
                reason=(f'군사기지법상 구역이 확인되었으나 판정 기준이 등록되지 않은 '
                        f'코드입니다 — {names}. 임의 판단하지 않습니다.' + coverage),
                action_required='관할부대에 해당 구역의 행위제한을 확인하십시오.',
                difficulty=Difficulty.HIGH,
                why='NO_RULE',
            )

        worst = max(known, key=lambda j: _DIFF_ORDER.index(j['difficulty'].value))

        detail = ' / '.join(
            f'{j["name"]}({self._hit_text(j)})'
            for j in sorted(known, key=lambda j: -_DIFF_ORDER.index(j['difficulty'].value))
        )
        extra = ''
        if unknown:
            extra = (' 판정 기준이 없는 구역도 함께 확인됩니다 — '
                     + ', '.join(f'{j["name"]}[{j["code"]}]' for j in unknown) + '.')

        return self.item(
            status=worst['status'],
            reason=(f'조회 필지가 군사기지법상 보호구역에 걸립니다 — {detail}. '
                    f'{worst["note"]}{extra}'
                    ' 구역 확인만으로는 표면높이 저촉 여부가 확정되지 않으므로 '
                    '실측 표고와 발전기 최고높이를 대입한 검토가 필요합니다.' + coverage),
            difficulty=worst['difficulty'],
            confidence=Confidence.MEDIUM,
            source_url='https://www.eum.go.kr',
            action_required=(
                '① 지자체를 경유해 관할부대에 보호구역 저촉 여부와 협의 절차를 질의 '
                '② 표면높이 초과 시 관할부대심의위원회 협의(비행안전영향 검토) '
                '③ 공군 레이더 전파영향 검토 요청 — 이 항목은 자동 판정 대상이 아닙니다'
            ),
            raw={'zones': [{k: v for k, v in z.items()} for z in zones.values()],
                 'parcels': parcels, 'total_parcels': total},
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _hit_text(j: dict) -> str:
        parts = []
        if j['conflict']:
            parts.append(f'저촉·포함 {j["conflict"]}필지')
        if j['touch']:
            parts.append(f'접함 {j["touch"]}필지')
        return ' · '.join(parts) or '확인'

    @staticmethod
    def _judge(z: dict) -> dict:
        """
        코드 접두로 구역 종류를 정한다.
        확인되지 않은 코드는 **추측하지 않고** status=None으로 남긴다.
        """
        out = {**z, 'status': None, 'difficulty': Difficulty.MEDIUM, 'note': ''}

        for prefix, _kind, status, diff, note in ZONE_KINDS:
            if z['code'].startswith(prefix):
                return {**out, 'status': status, 'difficulty': diff, 'note': note}

        if z['code'].startswith('UNE4'):
            m = _FLIGHT_ORDINAL.search(z['name'])
            if m:
                rule = FLIGHT_ZONE_RULES.get(int(m.group(1)))
                if rule:
                    return {**out, 'status': rule[0], 'difficulty': rule[1], 'note': rule[2]}
            elif '작전기지' in z['name']:
                return {**out, 'status': BASE_RULE[0],
                        'difficulty': BASE_RULE[1], 'note': BASE_RULE[2]}
        return out
