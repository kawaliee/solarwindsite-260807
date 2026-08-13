"""
나머지 데이터 레이어 어댑터
---------------------------------------------------------------
 5. 산사태위험등급        (산림청)
 6. 국가유산 (문화재)      (국가유산청)
 7. 군사·비행안전 규제      (국방부 — 공개 API 부재)
 8. 지자체 이격거리 조례    (자체 DB)
 9. 풍황                  (기상청 ASOS + KIER 풍력자원지도)
10. 전력계통 연계          (한전 — 공개 API 부재)
"""
from __future__ import annotations

from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery


# ======================================================================
# 5. 산사태위험등급 → providers/landslide.py 의 LandslideProvider로 이관
#    (생활안전지도 WMS에서 이미지를 받아 색상을 등급으로 역변환)
# ======================================================================


# ======================================================================
# 6. 국가유산 → providers/local_spatial.py 의 HeritageSpatialProvider로 이관
#    (공개 API 대신 국가유산청 SHP 직접 적재 → 좌표 기반 실거리 판정)
# ======================================================================


# ======================================================================
# 7. 군사·비행안전 규제 → providers/military.py 의 MilitaryZoneProvider로 이관
#    "공개 API 부재"로 UNKNOWN 고정이던 항목이다. 실제로는 토지이용계획
#    (V-World NED getLandUseAttr)에 UNE 코드군으로 실려 있어 판정이 가능하다.
# ======================================================================


# ======================================================================
# 8. 지자체 이격거리 조례 — 자체 DB
# ======================================================================
class LocalOrdinanceProvider(LayerProvider):
    category = '지자체 조례'
    item_name = '지자체 이격거리 조례'
    data_source = '내부 조례 DB'
    required_settings = ()
    default_law = '지자체 도시·군계획 조례'

    def __init__(self, sido: str = '', sigungu: str = ''):
        self.sido = sido
        self.sigungu = sigungu

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import ordinances                # 지연 import (앱 로딩 순서)

        if not self.sigungu:
            return self.unknown(
                reason='행정구역(시·군·구)을 특정하지 못해 조례를 조회할 수 없습니다.',
                action_required='주소 또는 행정구역을 입력하면 조례를 조회합니다.',
            )

        # DB에 없으면 자치법규 OPEN API로 그 자리에서 수집한다.
        # 빈손인 이유를 함께 받는다 — '규정이 없다'와 '물어보지 못했다'는
        # 전혀 다른 사실인데 한 문장으로 뭉치면 없는 줄로 읽힌다.
        rules, state = ordinances.ordinance_state(self.sido, self.sigungu)

        if not rules and state == ordinances.UNVERIFIED:
            err = ordinances.auth_failure()
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    f'{self.sigungu}의 조례를 조회하지 못했습니다 — 국가법령정보 '
                    'OPEN API가 요청을 거절했습니다. law.go.kr은 인증키(OC)와 별개로 '
                    '**계정에 등록된 서버 IP**를 대조하는데, 등록된 주소와 현재 '
                    '나가는 공인 IP가 다르면 거절합니다. 조례가 없는 것이 아니라 '
                    f'물어보지 못한 상태입니다. (응답: {err[:80]})'
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                source_url='https://open.law.go.kr',
                action_required=(
                    '국가법령정보 공동활용(open.law.go.kr) → OPEN API → OPEN API 신청에서 '
                    '현재 서버의 공인 IP를 등록하십시오. 유동 IP면 바뀔 때마다 갱신해야 '
                    '하므로 고정 IP나 고정 출구를 두는 편이 낫습니다. 그동안은 '
                    '자치법규정보시스템(elis.go.kr)에서 직접 확인하십시오.'),
                unknown_reason='FETCH',
            )

        if not rules:
            confirmed = state == ordinances.NO_RULE
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    f'{self.sido} {self.sigungu}의 조례를 자치법규 OPEN API로 조회했으나 '
                    + ('조문·별표를 모두 판독한 결과 풍력 이격거리 조항이 없었습니다. '
                       '다만 조례 외 지침·행정예고에 있을 수 있어 확정으로 보지 않습니다.'
                       if confirmed else
                       '해당 지자체의 조례를 찾지 못했습니다. 도시·군계획 조례와 '
                       '별도 제정 조례(풍력·재생에너지·이격거리)를 모두 검색한 결과입니다.')
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                source_url='https://www.elis.go.kr',
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 조례를 직접 확인하고, '
                                'python manage.py sync_ordinances --sigungu <시군구> --apply 로 등록하십시오.',
                unknown_reason='NO_RULE' if confirmed else 'NO_DATA',
            )

        order = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
        lines = []
        worst_diff = Difficulty.LOW
        for r in sorted(rules, key=lambda x: -x.distance_m):
            # 대상 코드(RESIDENTIAL 등)가 아니라 사람이 읽는 이름으로 적는다.
            # 같은 대상에 본문·단서 두 기준이 있으면 상세가 없으면 구분되지 않는다.
            label = r.get_target_display()
            if r.target_detail:
                label += f' — {r.target_detail}'
            lines.append(f'{label}: {r.distance_m:,}m ({r.ordinance_name} {r.article})')
            if order.index(r.difficulty) > order.index(worst_diff.value):
                worst_diff = Difficulty(r.difficulty)

        # 원문 대조를 마친 조례는 HIGH로 저장된다 — 그 신뢰도를 그대로 반영한다
        worst_conf = min((r.confidence for r in rules),
                         key=lambda c: ['LOW', 'MEDIUM', 'HIGH'].index(c))
        verified = [r.verified_at for r in rules if r.verified_at]
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'{self.sido} {self.sigungu} 이격거리 기준 — ' + ' / '.join(lines) +
                '. 실제 저촉 여부는 대상 정온시설·주거지의 실측 거리 확인이 필요합니다.'
                + (f' (원문 대조 {max(verified)})' if verified else '')
            ),
            difficulty=worst_diff,
            law=rules[0].ordinance_name,
            article=rules[0].article,
            confidence=Confidence(worst_conf),
            source_url=rules[0].source_url,
            action_required='현행 조례 원문을 재확인하고 실측 이격거리를 산출하십시오.',
            raw={'rules': lines},
        )


# ======================================================================
# 9. 풍황 → providers/wind.py 의 WindResourceProvider로 이관
#    (기상청 ASOS 관측자료 기반 — 최근접 관측소 자동 선정)
# ======================================================================

# ======================================================================
# 10. 전력계통 연계 → providers/osm.py 의 OsmGridProvider로 이관
#     (한전 미공개 → OSM Overpass로 변전소·송전선로 최근접 탐색)
# ======================================================================
