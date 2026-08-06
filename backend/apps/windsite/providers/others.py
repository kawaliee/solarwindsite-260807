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
        return self.item(
            status=Status.UNKNOWN,
            reason=(
                '군사기지 및 군사시설 보호구역·비행안전구역과 항공 레이더 전파영향은 '
                '좌표 기반 공개 API가 제공되지 않아 자동 판정이 불가합니다. '
                '풍력발전기는 높이가 커 표면높이 제한 및 레이더 간섭 검토 대상이 되는 경우가 많습니다.'
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
        from ..models import LocalOrdinance   # 지연 import (앱 로딩 순서)

        if not self.sigungu:
            return self.unknown(
                reason='행정구역(시·군·구)을 특정하지 못해 조례를 조회할 수 없습니다.',
                action_required='주소 또는 행정구역을 입력하면 조례를 조회합니다.',
            )

        qs = LocalOrdinance.objects.filter(sigungu=self.sigungu, energy_type__in=['WIND', 'ALL'])
        if self.sido:
            qs = qs.filter(sido=self.sido)
        rules = list(qs)

        if not rules:
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    f'{self.sido} {self.sigungu}의 풍력 이격거리 조례가 내부 DB에 등록되어 있지 않습니다. '
                    '조례는 지자체별 편차가 크고 개정이 잦아 임의 판단하지 않습니다.'
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 도시·군계획 조례를 확인해 DB에 등록하십시오.',
            )

        lines = []
        worst_diff = Difficulty.LOW
        for r in rules:
            lines.append(f'{r.target}: {r.distance_m:,}m ({r.ordinance_name} {r.article})')
            if ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(r.difficulty) > \
               ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(worst_diff.value):
                worst_diff = Difficulty(r.difficulty)

        low_conf = [r for r in rules if r.confidence == 'LOW']
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'{self.sido} {self.sigungu} 이격거리 기준 — ' + ' / '.join(lines) +
                '. 실제 저촉 여부는 대상 정온시설·주거지의 실측 거리 확인이 필요합니다.'
            ),
            difficulty=worst_diff,
            law=rules[0].ordinance_name,
            article=rules[0].article,
            confidence=Confidence.LOW if low_conf else Confidence.MEDIUM,
            source_url=rules[0].source_url,
            action_required='현행 조례 원문을 재확인하고 실측 이격거리를 산출하십시오.',
            raw={'rules': lines},
        )


# ======================================================================
# 9. 풍황
# ======================================================================
class WindResourceProvider(LayerProvider):
    category = '사업성'
    item_name = '풍황(연평균 풍속)'
    data_source = '기상청 ASOS / KIER 풍력자원지도'
    required_settings = ('KMA_API_KEY',)
    default_law = '해당 없음 (비규제 · 사업성 판단 영역)'

    #: 업계 통상 참고치 — 법정 기준이 아님
    REFERENCE_MS = 6.0

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        return self.item(
            status=Status.UNKNOWN,
            reason=(
                'KIER 풍력자원지도는 좌표 기반 공개 API가 확인되지 않아 허브고도(100~140m) 풍속을 '
                '자동 추정하지 못했습니다. 기상청 ASOS는 지상 10m 관측값이라 허브고도 환산에 '
                '별도 연직분포(전단지수) 가정이 필요해 단독 판정 근거로 쓰지 않습니다.'
            ),
            difficulty=Difficulty.MEDIUM,
            confidence=Confidence.LOW,
            source_url='https://kier-wind.org',
            action_required=(
                f'① KIER 풍력자원지도에서 대상지 허브고도 풍속 조회 '
                f'② 사업 확정 전 현장 풍황계측(통상 1년 이상) 수행. '
                f'참고: 국내 육상풍력은 통상 연평균 {self.REFERENCE_MS}m/s 이상 지역에 입지하는 것으로 '
                f'보고되나, 이는 법정 기준이 아닌 업계 참고치입니다.'
            ),
        )


# ======================================================================
# 10. 전력계통 연계 → providers/osm.py 의 OsmGridProvider로 이관
#     (한전 미공개 → OSM Overpass로 변전소·송전선로 최근접 탐색)
# ======================================================================
