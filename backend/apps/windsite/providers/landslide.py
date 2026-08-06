"""
산사태위험등급 어댑터 — 생활안전지도 WMS
---------------------------------------------------------------
산림청 산사태위험지도를 생활안전지도(safemap.go.kr) 오픈API로 조회한다.

⚠️ 이 서비스는 **WMS(지도 이미지)** 만 제공한다. 등급 숫자를 직접 주지 않으므로
   검토 반경만큼의 이미지를 받아 **픽셀 색상을 등급으로 역변환**한다.

실측으로 확인한 사항 (2026-08)
  · 엔드포인트  https://safemap.go.kr/openapi2/IF_0046_WMS
  · 인증 파라미터는 `serviceKey` (apikey/key는 인식되지 않음)
  · **layers/styles 파라미터를 주면 400**이 난다. 이 엔드포인트 자체가 산사태위험지도라
    기본 레이어로 동작하므로 레이어를 지정하지 않는다
  · 색상표는 산림청 배포 래스터(.clr)와 동일한 5등급 팔레트
      1등급 (255,0,0)  2등급 (255,201,0)  3등급 (182,255,142)
      4등급 (48,194,255) 5등급 (0,0,255)
    PNG 렌더링 과정에서 ±1~3 오차가 있어 근사 매칭한다
  · 1등급이 위험도 최고, 5등급이 최저
"""
from __future__ import annotations

import io
import logging
import urllib.parse

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

DEFAULT_WMS_URL = 'https://safemap.go.kr/openapi2/IF_0046_WMS'

#: 색상 → 등급. 산림청 배포 래스터의 .clr 색상표와 동일함을 실측 확인했다.
PALETTE: dict[tuple[int, int, int], int] = {
    (255, 0, 0): 1,
    (255, 201, 0): 2,
    (182, 255, 142): 3,
    (48, 194, 255): 4,
    (0, 0, 255): 5,
}
#: PNG 렌더링 오차 허용치 (채널당)
COLOR_TOLERANCE = 6

#: 요청 이미지 한 변의 최대 픽셀 수 (원본이 10m 격자라 그 이상은 의미가 없다)
MAX_IMAGE_PX = 400
SOURCE_PIXEL_M = 10


def _match_grade(rgb: tuple[int, int, int]) -> int | None:
    for ref, grade in PALETTE.items():
        if all(abs(a - b) <= COLOR_TOLERANCE for a, b in zip(rgb, ref)):
            return grade
    return None


class LandslideProvider(LayerProvider):
    """산사태위험등급 (1~5등급, 1등급이 최고 위험)"""

    category = '산림'
    item_name = '산사태위험등급'
    data_source = '산림청 산사태위험지도 (생활안전지도 WMS)'
    required_settings = ('FOREST_API_KEY',)
    default_law = '산지관리법'
    default_article = '제18조(산지전용허가기준 등) · 시행령 별표4'

    #: 등급별 기본 판정 — DB(RegulationRule)에 규칙이 있으면 그쪽이 우선한다.
    FALLBACK = {
        1: (Status.CONDITIONAL, Difficulty.CRITICAL,
            '산사태위험 1등급지가 포함되어 있습니다. 산지전용·산지일시사용 허가 심사에서 '
            '재해영향 검토가 크게 강화되며 실무상 허가가 어려운 경우가 많습니다. '
            '해당 구역을 회피하는 배치 조정을 우선 검토하십시오.'),
        2: (Status.CONDITIONAL, Difficulty.HIGH,
            '산사태위험 2등급지가 포함되어 사면 안정성 검토와 재해저감 대책 수립이 요구됩니다.'),
        3: (Status.CONDITIONAL, Difficulty.MEDIUM,
            '산사태위험 3등급지가 포함되어 있습니다. 일반적인 사면 안정 대책으로 대응 가능한 '
            '수준이나 설계 단계에서 검토가 필요합니다.'),
        4: (Status.POSSIBLE, Difficulty.LOW,
            '산사태위험 4등급지로 상대적으로 안정적인 구간입니다.'),
        5: (Status.POSSIBLE, Difficulty.LOW,
            '산사태위험 5등급지로 위험도가 낮은 구간입니다.'),
    }

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 산사태위험등급을 조회하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )

        try:
            counts, total_px = self._sample(q)
        except httpx.HTTPStatusError as e:
            return self.unknown(
                reason=(f'산사태위험지도 조회에 실패했습니다 (HTTP {e.response.status_code}). '
                        '인증키 또는 요청 파라미터를 확인하십시오.'),
                action_required='생활안전지도(safemap.go.kr)에서 인증키 상태를 확인하십시오.',
            )
        except Exception as e:                                  # noqa: BLE001
            logger.exception('산사태위험등급 조회 실패')
            return self.unknown(
                reason=f'산사태위험지도 조회 중 오류가 발생했습니다: {type(e).__name__}',
                action_required='네트워크 상태를 확인한 뒤 재조회하십시오.',
            )

        if not counts:
            return self.item(
                status=Status.UNKNOWN,
                reason=('검토 반경에서 산사태위험등급이 판별되지 않았습니다. '
                        '산림이 아닌 지역이거나 등급도 미구축 구간일 수 있습니다.'),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                source_url='https://sansatai.forest.go.kr',
                action_required='산사태정보시스템에서 대상지 등급을 직접 확인하십시오.',
                raw={'grade_pixels': {}, 'sampled_pixels': total_px},
            )

        worst = min(counts)
        status, difficulty, note = self._resolve(worst)

        graded_px = sum(counts.values())
        px_area = SOURCE_PIXEL_M ** 2
        approx_area = {g: n * px_area for g, n in counts.items()}
        circle_area = 3.141592653589793 * (q.radius_m ** 2)

        dist = ' · '.join(
            f'{g}등급 {counts[g] / graded_px * 100:.0f}%'
            for g in sorted(counts))

        # 10m 격자라 최악등급이 몇 픽셀만 있어도 검출된다. 판정은 안전측으로 최악등급을
        # 따르되, **실면적을 함께 제시**해 과대 해석을 막는다.
        worst_area = approx_area[worst]
        share = worst_area / circle_area * 100
        scale = (f'{worst}등급 면적은 약 {worst_area:,.0f}㎡'
                 f'(검토 반경 면적의 {share:.1f}%)입니다.')
        if share < 1.0:
            scale += (' 면적이 작아 배치 조정으로 회피 가능한지 우선 검토하십시오.')

        coverage = ''
        if graded_px and graded_px / max(total_px, 1) < 0.5:
            coverage = (f' ※ 검토 반경 중 등급이 부여된 구간은 '
                        f'{graded_px / max(total_px, 1) * 100:.0f}%입니다 '
                        f'(나머지는 산림이 아니거나 등급 미구축 구간).')

        return self.item(
            status=status,
            reason=(f'검토 반경 {q.radius_m:,}m 내 최고 위험등급은 {worst}등급입니다. '
                    f'등급 분포 — {dist}. {scale} {note}{coverage}'),
            difficulty=difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://safemap.go.kr',
            action_required=(
                '① 1·2등급지를 회피하는 발전기 배치 검토 '
                '② 재해영향평가등의 협의 대상 여부 확인 '
                '③ 사면 안정 해석과 사방시설 계획 수립'
            ),
            raw={
                'worst_grade': worst,
                'grade_pixels': dict(sorted(counts.items())),
                'approx_area_m2': {g: round(a) for g, a in sorted(approx_area.items())},
                'sampled_pixels': total_px,
                'pixel_size_m': SOURCE_PIXEL_M,
            },
        )

    # ------------------------------------------------------------------
    def _sample(self, q: SiteQuery) -> tuple[dict[int, int], int]:
        """검토 원 안의 픽셀만 등급별로 집계한다."""
        from PIL import Image

        site = geo.point_metric(q.lat, q.lng)
        half = float(q.radius_m)
        # 원본 해상도(10m)에 맞춰 크기를 정하되 상한을 둔다
        size = max(16, min(MAX_IMAGE_PX, int(half * 2 / SOURCE_PIXEL_M)))

        url = getattr(settings, 'FOREST_LANDSLIDE_URL', '') or DEFAULT_WMS_URL
        params = {
            'serviceKey': settings.FOREST_API_KEY,
            'service': 'WMS',
            'request': 'GetMap',
            'version': '1.1.1',
            'srs': geo.METRIC_CRS,
            'bbox': f'{site.x - half},{site.y - half},{site.x + half},{site.y + half}',
            'width': str(size),
            'height': str(size),
            'format': 'image/png',
            'transparent': 'true',
            # ⚠️ layers/styles를 넣으면 400이 난다 (실측). 기본 레이어로 호출한다.
        }

        def fetch() -> bytes:
            res = httpx.get(f'{url}?{urllib.parse.urlencode(params)}', timeout=45.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            return res.content

        blob = httpcache.get_or_set('landslide', params, fetch)
        img = Image.open(io.BytesIO(blob)).convert('RGBA')
        w, h = img.size
        px = img.load()

        counts: dict[int, int] = {}
        cx = cy = (size - 1) / 2.0
        r_px = size / 2.0
        inside = 0
        for yy in range(h):
            for xx in range(w):
                # 검토 원 밖은 제외 — 사각형 그대로 세면 모서리가 과대 반영된다
                if (xx - cx) ** 2 + (yy - cy) ** 2 > r_px ** 2:
                    continue
                inside += 1
                r, g, b, a = px[xx, yy]
                if a == 0:
                    continue
                grade = _match_grade((r, g, b))
                if grade:
                    counts[grade] = counts.get(grade, 0) + 1
        return counts, inside

    # ------------------------------------------------------------------
    def _resolve(self, grade: int):
        """판정은 DB(RegulationRule) 우선, 없으면 폴백."""
        from ..models import RegulationRule

        rule = RegulationRule.objects.filter(
            layer='산사태위험등급', condition_key=str(grade), is_active=True).first()
        if rule:
            return (Status(rule.status), Difficulty(rule.difficulty), rule.reason_template)
        return self.FALLBACK.get(
            grade, (Status.UNKNOWN, Difficulty.MEDIUM, '등급 판정 기준을 확인하지 못했습니다.'))
