"""
지형 경사도 — 조례·산지관리법의 경사도 기준 대조
---------------------------------------------------------------
이격거리 조례에는 거리가 아닌 조건이 섞여 있다.

    홍성군 [별표 26] 가.4)  "경사도가 15도 이상인 산지에 입지하지 아니할 것"
    해남군 제19조의3 제7호  "산지 내 태양광 시설은 평균경사도가 15도 이하로 한다"

거리 추출기로는 잡히지 않아 조문에서 따로 읽고(`ordinances.slope_limit`),
표고 격자로 실제 경사를 재어(`dem.stats`) 대조한다.

■ 왜 토지특성으로는 안 되는가

V-World 토지특성에도 '지형지세'(급경사·고지)가 있고 이미 연동돼 있다. 그러나
그것은 **공시지가 산정용 구분**이라 조례가 말하는 평균경사도와 다른 지표다.
그 값으로 15도 기준을 판정하면 근거 없는 숫자를 만들어내게 된다.

■ 경계에서는 단정하지 않는다

국토지리정보원 공개DEM은 90m 격자다. 부지가 작으면 표본이 서너 칸이라
평균이 한두 칸에 좌우된다. 그래서 기준 ±3도 구간은 판정하지 않고
**'확인 필요 — 실측 권장'** 으로 남긴다. 이 구간에서 가부를 선고하면,
측량하면 뒤집힐 결론을 문서로 굳히게 된다.
"""
from __future__ import annotations

import logging

from .. import dem, energy as energy_mod
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

#: 기준선 근처에서 판정을 보류할 폭(도). 90m 격자의 표본 한계를 감안한 값이다.
JUDGE_MARGIN_DEG = 3.0

#: 조례에 경사도 규정이 없을 때 참고로 제시하는 기준.
#: ⚠️ 산지관리법 시행령 별표4의 원문을 아직 대조하지 못했다. 그래서 이 값으로
#:    **가부를 판정하지 않고** 확인처만 안내한다.
FOREST_ACT_REFERENCE_DEG = 25


class SlopeProvider(LayerProvider):
    """평균경사도·사면향 — 조례 기준 대조"""

    category = '산림'
    item_name = '지형 경사도 (평균경사도)'
    data_source = '국토지리정보원 수치표고모델(DEM)'
    required_settings = ()
    default_law = '산지관리법'
    default_article = '제18조(산지전용허가기준) · 시행령 별표4'

    def __init__(self, sido: str = '', sigungu: str = '',
                 energy: str = energy_mod.DEFAULT):
        self.sido = sido
        self.sigungu = sigungu
        self.energy = energy_mod.normalize(energy)

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import ordinances                               # 지연 import

        try:
            s = dem.stats(q.geom)
        except dem.DemUnavailable as e:
            return self.unknown(
                reason=(
                    f'평균경사도를 산출하지 못했습니다 — {e} '
                    '경사가 완만하다는 뜻이 아니라 표고 자료가 없다는 뜻입니다.'
                ),
                action_required=(
                    '국토정보플랫폼(map.ngii.go.kr)에서 해당 지역 공개DEM 도엽을 '
                    '내려받아 backend/data/dem/ 에 넣으면 자동 판정됩니다.'),
                why='NO_DATA',
            )
        except Exception as e:                                  # noqa: BLE001
            logger.exception('경사도 산출 실패')
            return self.unknown(
                reason=f'표고 자료 판독 중 오류가 발생했습니다: {type(e).__name__}',
                action_required='DEM 파일 형식과 좌표계를 확인하십시오.',
                why='FETCH',
            )

        rule = ordinances.slope_limit(self.sido, self.sigungu, self.energy)
        basis = self._basis(s)
        aspect = self._aspect(s)

        if rule:
            return self._judge_by_ordinance(s, rule, basis, aspect)
        return self._no_rule(s, basis, aspect)

    # ------------------------------------------------------------------
    def _basis(self, s: dict) -> str:
        """수치를 어떻게 얻었는지 — 값 옆에 늘 붙인다."""
        txt = (f'{s["cell_m"]:.0f}m 격자 {s["cells"]}칸 표본'
               f'(도엽 {", ".join(s["tiles"])})')
        if s['coverage'] < 0.999:
            txt += f' · 구역의 {s["coverage"] * 100:.0f}%만 자료 있음'
        return txt

    def _aspect(self, s: dict) -> str:
        """사면향 — 판정에 쓰지 않고 사업성 참고로만 적는다."""
        r = s['south_ratio']
        if r >= 0.6:
            return f' 남향(남동~남서) 비율은 {r * 100:.0f}%로 일사 조건에 유리합니다.'
        if r <= 0.2:
            return (f' 남향 비율이 {r * 100:.0f}%로 낮아 배치에 따라 '
                    '음영·발전량 손실을 검토해야 합니다.')
        return f' 남향 비율은 {r * 100:.0f}%입니다.'

    # ------------------------------------------------------------------
    def _judge_by_ordinance(self, s: dict, rule: dict, basis: str,
                            aspect: str) -> AnalysisItem:
        limit, mean = rule['degrees'], s['mean_deg']
        head = (f'평균경사도 {mean}도 (최대 {s["max_deg"]}도, 상위10% {s["p90_deg"]}도, '
                f'평균표고 {s["mean_elev_m"]:,.0f}m). {basis}. '
                f'{self.sigungu} 조례 기준은 {limit}도입니다.')
        common = dict(law=rule['law'], article=rule['article'],
                      source_url=rule.get('source_url', ''),
                      raw={**s, 'limit_deg': limit, 'rule': rule})

        # 표본이 부족하면 값이 어느 쪽이든 단정하지 않는다.
        if not s['reliable']:
            return self.item(
                status=Status.UNKNOWN,
                reason=head + ' 다만 표본이 부족해 이 평균을 판정 근거로 쓰지 않습니다.'
                              + aspect,
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='측량으로 평균경사도를 확인하십시오. 더 촘촘한 '
                                '표고자료(5m DEM)를 넣으면 자동 판정됩니다.',
                unknown_reason='NO_DATA', **common)

        if mean >= limit + JUDGE_MARGIN_DEG:
            return self.item(
                status=Status.IMPOSSIBLE,
                reason=(head + f' 기준을 {mean - limit:.1f}도 초과합니다 — '
                        f'조문이 직접 금지하는 구간입니다. 근거: “{rule["sentence"]}”'
                        + aspect),
                difficulty=Difficulty.CRITICAL,
                confidence=Confidence.MEDIUM,
                action_required='부지를 완경사 구역으로 옮기거나, 조례 단서(예외 규정) '
                                '해당 여부를 관할 지자체에 확인하십시오.',
                **common)

        if mean <= limit - JUDGE_MARGIN_DEG:
            return self.item(
                status=Status.POSSIBLE,
                reason=head + f' 기준을 {limit - mean:.1f}도 밑돕니다.' + aspect,
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                action_required='인허가 단계에서 측량 성과로 평균경사도를 확정하십시오.',
                **common)

        # 기준선 ±3도 — 90m 격자로는 가릴 수 없는 구간이다.
        return self.item(
            status=Status.UNKNOWN,
            reason=(head + f' 기준선({limit}도)과의 차이가 '
                    f'{abs(mean - limit):.1f}도로 격자 해상도의 오차 범위 안입니다. '
                    '이 구간은 자동 판정하지 않습니다.' + aspect),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.LOW,
            action_required='측량으로 평균경사도를 확정해야 가부가 갈립니다. '
                            '설계 단계 전에 우선 확인하십시오.',
            unknown_reason='NO_CRITERIA', **common)

    # ------------------------------------------------------------------
    def _no_rule(self, s: dict, basis: str, aspect: str) -> AnalysisItem:
        """
        조례에 경사도 규정이 없을 때.

        산지관리법 기준으로 가부를 선고하지 않는다 — 시행령 별표4 원문을 아직
        대조하지 못했고, 사업 유형·산지 구분에 따라 기준이 갈리기 때문이다.
        값을 제시하고 확인처를 붙이는 데까지가 역할이다.
        """
        mean = s['mean_deg']
        head = (f'평균경사도 {mean}도 (최대 {s["max_deg"]}도, 상위10% {s["p90_deg"]}도, '
                f'평균표고 {s["mean_elev_m"]:,.0f}m). {basis}. '
                f'{self.sigungu or "해당 지자체"} 조례에서는 경사도 규정을 '
                '찾지 못했습니다.')
        raw = {**s, 'limit_deg': None}

        if not s['reliable']:
            return self.unknown(
                reason=head + ' 표본이 부족해 이 평균을 판정 근거로 쓰지 않습니다.' + aspect,
                action_required='측량으로 평균경사도를 확인하십시오.',
                why='NO_DATA')

        if mean >= FOREST_ACT_REFERENCE_DEG:
            return self.item(
                status=Status.CONDITIONAL,
                reason=(head + f' 산지전용 실무에서 통용되는 참고 기준'
                        f'({FOREST_ACT_REFERENCE_DEG}도)을 넘습니다.' + aspect),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='산지관리법 시행령 별표4의 평균경사도 기준과 '
                                '해당 산지 구분을 원문으로 확인하십시오.',
                raw=raw)

        return self.item(
            status=Status.POSSIBLE,
            reason=head + ' 급경사로 볼 수준은 아닙니다.' + aspect,
            difficulty=Difficulty.LOW,
            confidence=Confidence.LOW,
            action_required='산지에 해당하면 산지관리법 시행령 별표4의 평균경사도 '
                            '기준을 원문으로 확인하십시오.',
            raw=raw)
