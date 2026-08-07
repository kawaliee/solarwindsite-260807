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
# 7. 군사·비행안전 규제 — 공개 API 부재
# ======================================================================
class MilitaryAirspaceProvider(LayerProvider):
    category = '안전/문화재'
    item_name = '군사기지·비행안전구역'
    data_source = '국방부/관할부대 (공개 API 미제공)'
    required_settings = ()
    default_law = '군사기지 및 군사시설 보호법'
    default_article = '제10조(비행안전구역에서의 금지 또는 제한) · 제13조(협의)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        # 항공 공역은 별도 레이어로 이미 판정하고 있다. 여기서 다루는 것은
        # 그 레이어들이 담지 못하는 **군사기지법상 보호구역(통제보호·제한보호·
        # 비행안전 제1~6구역)** 과 레이더 전파영향이다. 이 둘만 공개 API가 없다.
        from ..models import RegulationLayer

        judged = list(RegulationLayer.objects
                      .filter(is_active=True, category='안전/문화재',
                              layer_id__contains='ais')
                      .values_list('title', flat=True))
        covered = ', '.join(t for t in judged if t)[:200]

        return self.item(
            status=Status.UNKNOWN,
            reason=(
                '「군사기지 및 군사시설 보호법」상 **보호구역(통제보호구역·제한보호구역·'
                '비행안전구역 제1~6구역)** 지정 현황과 항공 레이더 전파영향은 좌표 기반 '
                '공개 API가 제공되지 않아 자동 판정이 불가합니다. '
                '풍력발전기는 높이가 커 표면높이 제한 및 레이더 간섭 검토 대상이 되는 '
                '경우가 많습니다. '
                + (f'다만 항공 공역은 별도 항목으로 판정하고 있습니다 — {covered}.'
                   if covered else '')
            ),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.MEDIUM,
            source_url='https://www.mnd.go.kr',
            action_required=(
                '① 지자체를 경유해 관할부대에 군사시설 보호구역 저촉 여부 질의 '
                '② 표면높이 초과 시 관할부대심의위원회 협의(비행안전영향 검토) '
                '③ 공군 레이더 전파영향 검토 요청'
            ),
        )


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

        # DB에 없으면 자치법규 OPEN API로 그 자리에서 수집한다
        rules = ordinances.ensure_ordinances(self.sido, self.sigungu)

        if not rules:
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    f'{self.sido} {self.sigungu}의 풍력 이격거리 조례를 '
                    '자치법규 OPEN API에서 자동 조회했으나 이격거리 조항을 찾지 못했습니다. '
                    '해당 지자체에 이격 규정이 없거나, 도시·군계획 조례가 아닌 '
                    '별도 조례·지침에 있을 수 있습니다. 임의 판단하지 않습니다.'
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 조례를 직접 확인하고, '
                                'python manage.py sync_ordinances --sigungu <시군구> --apply 로 등록하십시오.',
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
