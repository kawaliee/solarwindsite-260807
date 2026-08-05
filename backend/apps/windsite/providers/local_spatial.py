"""
내부 적재 공간데이터 기반 어댑터
---------------------------------------------------------------
공개 API가 없는 레이어를 SHP로 적재해(`load_spatial_shp`) 조회한다.
PostGIS가 없으므로 **bbox 1차 필터 → shapely 정밀 계산** 2단계로 처리한다.

좌표계는 적재 시 원본(EPSG:5179)을 유지했으므로 검토 지점을 같은 좌표계로
변환해 그대로 비교한다. 즉 조회 경로에 재투영이 없다.
"""
from __future__ import annotations

import logging

from .. import geo
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

#: bbox 후보가 지나치게 많을 때의 안전 상한 (조용히 자르지 않고 결과에 표기)
MAX_CANDIDATES = 4000


class LocalSpatialProvider(LayerProvider):
    """
    SpatialDataset 여러 건을 하나의 검토 항목으로 묶어 판정하는 범용 어댑터.

    서브클래스는 dataset_codes와 판정 메타만 지정하면 된다.
    """

    required_settings = ()
    #: 조회 대상 데이터셋 키
    dataset_codes: tuple[str, ...] = ()
    #: 레이어 고유 보호범위 — 검토 반경에 가산 (예: 역사문화환경 500m)
    search_margin_m: int = 0
    #: 저촉 시 기본 판정
    hit_status: Status = Status.CONDITIONAL
    hit_difficulty: Difficulty = Difficulty.HIGH

    def _features(self, q: SiteQuery):
        """bbox로 후보를 좁힌 뒤 (거리, 피처) 목록을 돌려준다."""
        from ..models import SpatialFeature                     # 지연 import

        site = geo.point_metric(q.lat, q.lng)
        r = q.radius_m + self.search_margin_m
        qs = (SpatialFeature.objects
              .filter(dataset__code__in=self.dataset_codes,
                      dataset__is_active=True,
                      min_x__lte=site.x + r, max_x__gte=site.x - r,
                      min_y__lte=site.y + r, max_y__gte=site.y - r)
              .select_related('dataset'))

        cand = list(qs[:MAX_CANDIDATES])
        truncated = len(cand) >= MAX_CANDIDATES

        from shapely import wkb as shapely_wkb

        out: list[tuple[float, object]] = []
        for f in cand:
            try:
                g = shapely_wkb.loads(bytes(f.geom_wkb))
            except Exception:                                   # noqa: BLE001
                logger.debug('WKB 판독 실패 id=%s', f.pk, exc_info=True)
                continue
            d = site.distance(g)
            if d <= r:
                out.append((round(d, 1), f))
        out.sort(key=lambda t: t[0])
        return out, truncated

    # ------------------------------------------------------------------
    def _dataset_ready(self) -> bool:
        from ..models import SpatialFeature
        return SpatialFeature.objects.filter(
            dataset__code__in=self.dataset_codes, dataset__is_active=True).exists()

    def _not_loaded(self) -> AnalysisItem:
        return self.unknown(
            reason=(
                f'{self.item_name} 공간데이터가 적재되어 있지 않아 판정할 수 없습니다. '
                f'(데이터셋: {", ".join(self.dataset_codes)})'
            ),
            action_required='`python manage.py load_spatial_shp` 로 원본 SHP을 적재하십시오.',
        )


# ======================================================================
class HeritageSpatialProvider(LocalSpatialProvider):
    """
    국가유산 — 지정유산·보호구역·현상변경 허용기준.

    기존 구현은 공개 API(HERITAGE_URL)에 의존해 인증키가 없으면 항상 UNKNOWN이었다.
    국가유산청이 배포하는 SHP을 직접 적재해 **좌표 기반 실거리 판정**으로 전환한다.
    """

    category = '안전/문화재'
    item_name = '국가유산·역사문화환경 보존지역'
    data_source = '국가유산청 공간정보(SHP 적재)'
    default_law = '문화유산의 보존 및 활용에 관한 법률'
    default_article = '제13조(역사문화환경 보존지역의 보호)'

    #: 지정·등록유산 및 보호구역
    DESIGNATED_CODES = (
        'heritage_국가지정유산', 'heritage_시도지정유산',
        'heritage_국가등록문화유산', 'heritage_시도등록문화유산',
        'heritage_국가지정유산보호구역', 'heritage_시도지정유산보호구역',
    )
    #: 현상변경 허용기준 구역 (1~4구역)
    CRITERIA_CODES = ('heritage_현상변경허용기준',)

    dataset_codes = DESIGNATED_CODES + CRITERIA_CODES

    #: 역사문화환경 보존지역의 원칙적 범위. 시·도 조례로 조정될 수 있어
    #: 이 값은 '조회 범위'로만 쓰고 법적 확정 판정에는 쓰지 않는다.
    search_margin_m = 500

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리(shapely/pyproj)가 없어 국가유산 거리 판정을 수행하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )
        if not self._dataset_ready():
            return self._not_loaded()

        found, truncated = self._features(q)
        if not found:
            return self.item(
                status=Status.POSSIBLE,
                reason=(
                    f'검토 반경 {q.radius_m:,}m + 역사문화환경 보존지역 기본범위 '
                    f'{self.search_margin_m:,}m 내에서 지정·등록 국가유산 및 '
                    f'현상변경 허용기준 구역이 조회되지 않았습니다.'
                ),
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://www.khs.go.kr',
                action_required='매장유산 지표조사 대상 여부는 사업 면적 기준으로 별도 확인하십시오.',
                raw={'heritages': [], 'searched_radius_m': q.radius_m + self.search_margin_m},
            )

        designated = [(d, f) for d, f in found if f.dataset.code in self.DESIGNATED_CODES]
        criteria = [(d, f) for d, f in found if f.dataset.code in self.CRITERIA_CODES]

        items = [{
            'name': f.name or '(명칭 없음)',
            'kind': f.kind,
            'dataset': f.dataset.name,
            'sigungu': f.sigungu,
            'distance_m': d,
            'lat': f.centroid_lat, 'lng': f.centroid_lng,
        } for d, f in found[:30]]

        lines = []
        inside_criteria = [(d, f) for d, f in criteria if d == 0]
        if inside_criteria:
            zones = ', '.join(sorted({f'{f.name} {f.kind}'.strip() for _, f in inside_criteria}))
            lines.append(
                f'대상 지점이 **현상변경 허용기준 구역** 안에 있습니다 — {zones}. '
                f'해당 구역의 허용기준(높이·규모 제한)을 그대로 적용받습니다.'
            )
        elif criteria:
            d, f = criteria[0]
            lines.append(
                f'가장 가까운 현상변경 허용기준 구역({f.name} {f.kind})까지 '
                f'{geo.format_distance(d)}입니다.'
            )

        if designated:
            d, f = designated[0]
            near = ', '.join(f'{ff.name}({ff.kind}, {geo.format_distance(dd)})'
                             for dd, ff in designated[:5])
            lines.append(
                f'인근 지정·등록 국가유산 {len(designated)}건 — {near}. '
                f'최근접 {geo.format_distance(d)}.'
            )

        if truncated:
            lines.append('⚠️ 후보 상한에 도달해 일부 유산이 누락되었을 수 있습니다.')

        # 현상변경 허용기준 구역 안이면 난이도를 올린다 (허가 요건이 확정적으로 발생)
        difficulty = Difficulty.CRITICAL if inside_criteria else self.hit_difficulty

        return self.item(
            status=self.hit_status,
            reason=' '.join(lines),
            difficulty=difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://www.khs.go.kr',
            action_required=(
                '관할 시·도의 국가유산 현상변경 허용기준을 확인하고, '
                '허용기준을 초과하는 경우 국가유산청 현상변경 허가(개별 심의)를 신청하십시오. '
                '풍력발전기는 높이가 커 허용기준 초과 가능성이 높습니다.'
            ),
            raw={
                'heritages': items,
                'designated_count': len(designated),
                'criteria_count': len(criteria),
                'inside_criteria': bool(inside_criteria),
                'truncated': truncated,
            },
        )
