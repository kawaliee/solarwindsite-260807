"""
유효지역 해역 제외 판정 — 블레이드가 바다에 걸치는가
---------------------------------------------------------------
「발전사업세부허가기준 …에 관한 고시」의 육상풍력 사업 유효지역은 *신청좌표
중심 반지름 2km 원 이내로 **해역을 제외한** 지역*이고, *블레이드의 회전 가능
범위를 수평으로 투영한 면적은 유효지역 이내여야* 한다.

거리 요건(신청좌표~호기 + 로터 ≤ 2km)은 `RawWindProvider`가 본다. 이 어댑터는
남은 축인 **해역**을 본다. 둘 다 통과해야 유효지역 안이다.

⚠️ **단정하지 않는다.** 육지 경계로 쓰는 행정경계(읍·면·동)는 조위 기준
   해안선이 아니라 수십 m 어긋날 수 있고(coast.BOUNDARY_TOLERANCE_M),
   블레이드가 일부만 해역에 걸칠 때 이를 어떻게 볼지는 허가청 판단이다.
   그래서 걸치면 CONDITIONAL로 두고 **얼마나 걸치는지 수치로** 낸다.
"""
from __future__ import annotations

import logging

from .. import coast
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)


class SeaAreaProvider(LayerProvider):
    """유효지역 해역 제외 — 블레이드 회전 투영면"""

    category = '사업성'
    item_name = '유효지역(해역 제외)'
    data_source = 'V-World 읍·면·동 행정경계'
    required_settings = ('VWORLD_API_KEY',)
    default_law = '발전사업세부허가기준 등에 관한 고시'
    default_article = '육상풍력 사업 유효지역'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import rawwind
        rotor = rawwind.rotor_radius_m()
        try:
            s = coast.blade_sea(q.lat, q.lng, rotor)
        except Exception as e:                                  # noqa: BLE001
            from .base import explain_error
            return self.unknown(
                reason=f'육지 경계를 조회하지 못해 해역 여부를 확인하지 '
                       f'못했습니다 — {explain_error(e)}. **해역이 없다는 '
                       f'뜻이 아닙니다.**',
                action_required='조회를 다시 시도하거나 해안선 자료로 직접 '
                                '확인하십시오.',
                why='FETCH')

        ratio = s['sea_ratio']
        base = (f'블레이드 회전 반지름 {rotor:,.0f}m 기준 투영원 '
                f'{3.14159 * rotor * rotor / 10_000:,.1f}ha 중 ')
        if ratio <= 0:
            return self.item(
                status=Status.POSSIBLE,
                reason=base + '해역에 걸치는 부분이 없습니다 — 유효지역의 '
                              '해역 제외 요건을 충족합니다.',
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                raw={'sea_ratio': 0.0, 'rotor_m': rotor, **s})

        where = ('호기 자리 자체가 육지 경계 밖입니다'
                 + (f'(해안선까지 {s["offshore_m"]:,.0f}m — 행정경계 정밀도 '
                    f'{coast.BOUNDARY_TOLERANCE_M}m 안이라 단정할 수 없습니다)'
                    if s['tolerant'] else
                    f'(해안선까지 {s["offshore_m"]:,.0f}m)')
                 if not s['on_land'] else '호기 자리는 육지입니다')
        return self.item(
            status=Status.CONDITIONAL,
            reason=(base + f'{s["sea_m2"] / 10_000:,.1f}ha({ratio * 100:.0f}%)가 '
                    f'해역에 걸칩니다. {where}. 고시상 유효지역은 해역을 제외한 '
                    f'지역이고 블레이드 회전 투영면이 그 안에 있어야 하므로, '
                    f'**이 호기는 유효지역 요건 저촉 소지가 있습니다.**'),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.LOW,
            action_required=(
                '① 호기를 내륙으로 옮기거나 로터직경이 작은 기종을 검토하십시오 '
                '② 육지 경계는 행정경계라 조위 기준 해안선과 다를 수 있으므로, '
                '공유수면 관리청·허가청에 실제 해안선으로 확인하십시오 '
                '③ 블레이드가 일부만 해역에 걸치는 경우의 해석은 허가청 판단입니다'),
            raw={'rotor_m': rotor, **s})
