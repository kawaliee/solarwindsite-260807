"""
재현바람장 풍황 어댑터
---------------------------------------------------------------
「발전사업 세부허가기준 등에 관한 고시」 개정으로 풍력 발전사업허가에
풍황계측기 설치가 필수가 아니게 되고 **기상청 재현바람장 자료 제출**로
바뀐다. 그 자료를 판정에 올리는 어댑터다.

ASOS 풍황(`wind.WindResourceProvider`)과 나란히 둔다. 둘은 대체 관계가
아니라 성격이 다르다.

  ASOS        지상 10m 관측을 멱법칙(α 가정)으로 허브고도까지 환산한 값
  재현바람장   부지 좌표·허브고도에서 바로 나온 값 — **인허가 제출 자료**

두 값이 다르면 그 사실 자체가 정보다. 어느 하나로 덮어쓰지 않는다.

⚠️ 이 어댑터는 **직접 조회하지 않는다.** API가 느려(30분 간격 1년치 약
   50분) 보고서 생성 중에 받을 수 없다. `collect_rawwind` 명령이 미리
   받아 둔 `RawWindSample`을 읽는다. 자료가 없으면 '확인 필요'로 두고
   무엇을 실행해야 하는지 알린다 — 없는 것을 추측으로 채우지 않는다.
"""
from __future__ import annotations

import logging

from .. import rawwind
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)


def kst(dt):
    """
    저장된 시각을 **KST로 되돌린다.**

    재현바람장 시각은 KST인데 Django(USE_TZ)가 UTC로 저장한다. 그대로 찍으면
    9시간 어긋나 날짜가 하루 밀린다 — 2025-06-01부터 받은 자료가 보고서에
    2025-05-31로 실렸다. 인허가 제출 자료의 관측 기간이라 하루도 틀리면 안 된다.
    """
    from django.utils import timezone
    return timezone.localtime(dt) if timezone.is_aware(dt) else dt


def rawwind_law() -> str:
    """고시의 **정식 명칭**. 어댑터마다 다르게 적으면 같은 근거가
    보고서에서 두 법령처럼 읽히고 조문 대조도 어긋난다."""
    return '발전사업세부허가기준, 전기요금산정기준, 전력량계허용오차 및 전력계통운영업무에 관한 고시'

def match_radius_m() -> float:
    """
    저장된 표본을 **이 호기의 것으로 말할 수 있는** 최대 거리(m).

    임의로 정하지 않는다. 고시가 정한 유효지역(신청좌표 중심 반지름 2km)에
    블레이드 회전 투영면이 들어와야 하므로, 호기가 신청좌표에서 떨어질 수
    있는 한계는 `2,000m − 로터 반지름`이다. 이 거리를 넘은 호기는 그 신청
    좌표의 유효지역 밖이라, 같은 자료로 풍황을 말할 근거가 없다.

    자료의 성질과도 맞는다 — 실측에서 2.3km 떨어진 두 지점(평창 1·8호기)의
    같은 시각 풍속이 1.0과 1.6 m/s로 갈렸다.
    """
    return rawwind.VALID_RADIUS_M - rawwind.rotor_radius_m()

#: 사업성 판정 기준(m/s). `wind.JUDGE_MS`와 같은 값을 쓴다 — 같은 사업지에
#: 두 기준이 있으면 어느 쪽을 믿어야 할지 알 수 없다.
JUDGE_MS = 5.5

#: 표본이 이 비율에 못 미치면 통계로 말하지 않는다. 조각 실패가 많으면
#: 특정 기간이 통째로 빠져 계절 편향이 생긴다.
MIN_COVERAGE = 0.7


class RawWindProvider(LayerProvider):
    """재현바람장 풍황 — 발전사업허가 제출 자료"""

    category = '사업성'
    item_name = '풍황(재현바람장)'
    data_source = '기상청 API허브 재현바람장'
    required_settings = ('KMA_APIHUB_KEY',)
    default_law = rawwind_law()
    default_article = '풍력 발전사업허가 풍황 자료 제출 요건'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import geo
        from ..models import RawWindSample

        lim = match_radius_m()
        rows = list(RawWindSample.objects.all()[:200])
        near, outside = [], []
        for r in rows:
            try:
                d = geo.point_metric(q.lat, q.lng).distance(
                    geo.point_metric(r.lat, r.lng))
            except Exception:                                   # noqa: BLE001
                continue
            (near if d <= lim else outside).append((d, r))
        if not near and outside:
            # 자료는 있는데 이 호기가 그 신청좌표의 유효지역 밖이다. '자료
            # 없음'과 전혀 다른 사실이라 따로 말한다 — 이쪽은 수집이 아니라
            # **신청좌표 또는 배치를 고쳐야 하는** 문제다.
            d0 = min(d for d, _ in outside)
            return self.unknown(
                reason=(
                    f'이 호기는 재현바람장을 받아 둔 신청좌표에서 {d0:,.0f}m '
                    f'떨어져 있어, 블레이드 회전 반지름 '
                    f'{rawwind.rotor_radius_m():,.0f}m를 더하면 유효지역'
                    f'(반지름 {rawwind.VALID_RADIUS_M:,}m)을 벗어납니다. '
                    f'같은 자료로 이 호기의 풍황을 말할 수 없습니다.'),
                action_required=(
                    '① 신청좌표를 옮겨 이 호기를 유효지역에 넣거나 '
                    '② 이 호기를 담는 별도 신청좌표로 재현바람장을 따로 받으십시오 '
                    '(`collect_rawwind --plan <배치안ID> --cover`).'),
                why='NO_DATA',
            )
        if not near:
            return self.unknown(
                reason=(
                    '재현바람장 자료가 아직 수집되지 않았습니다. 풍황계측기 없이 '
                    '발전사업허가를 신청하려면 이 자료가 필요하므로 **반드시 '
                    '수집해야 합니다** — 자료가 없다는 뜻이지 바람이 약하다는 '
                    '뜻이 아닙니다.'),
                action_required=(
                    '`python manage.py collect_rawwind --plan <배치안ID>` 로 '
                    '대표 지점 1년치를 받으십시오. 30분 간격 1년치에 약 50분 '
                    '걸립니다.'),
                why='NO_DATA',
            )

        near.sort(key=lambda x: x[0])
        by_h = {}
        for d, r in near:
            # 같은 고도가 여럿이면 가장 가깝고 표본이 많은 것을 쓴다.
            cur = by_h.get(r.height_m)
            if cur is None or (d, -r.samples) < (cur[0], -cur[1].samples):
                by_h[r.height_m] = (d, r)

        usable = {h: (d, r) for h, (d, r) in by_h.items()
                  if r.coverage >= MIN_COVERAGE}
        if not usable:
            worst = min(by_h.values(), key=lambda x: x[1].coverage)[1]
            return self.unknown(
                reason=(f'재현바람장을 받았으나 표본이 기대치의 '
                        f'{worst.coverage * 100:.0f}%에 그쳐 통계로 쓰지 '
                        f'않았습니다. 특정 기간이 통째로 빠지면 계절 편향이 '
                        f'생겨 연평균이 왜곡됩니다.'),
                action_required='`collect_rawwind`를 다시 실행해 빠진 구간을 '
                                '채우십시오.',
                why='FETCH',
            )

        # 판정은 보고 고도 중 **낮은 쪽**으로 한다. 허브고도가 확정되기 전에
        # 높은 고도 값으로 단정하면 사업성을 후하게 본다.
        base_h = min(usable)
        base_d, base = usable[base_h]
        stats = base.stats or {}
        mean = float(stats.get('mean_ms') or 0)

        if mean >= JUDGE_MS:
            st, df = Status.POSSIBLE, Difficulty.LOW
            verdict = (f'육상풍력 참고 기준({JUDGE_MS} m/s) 이상으로 자원 여건이 '
                       f'확보되는 것으로 나타납니다.')
        else:
            st, df = Status.CONDITIONAL, Difficulty.HIGH
            verdict = (f'육상풍력 참고 기준({JUDGE_MS} m/s)에 못 미쳐 **자원 '
                       f'부족 가능성**이 있습니다. 기종 선정(저풍속형)과 허브고도 '
                       f'상향을 함께 검토하십시오.')

        # 고도마다 수집 기간이 다르면 고도 간 비교가 성립하지 않는다. 특히
        # 연직시어 α는 두 고도의 평균을 나눠 구하므로, 기간이 어긋난 채로
        # 재면 시어가 아니라 계절 차이를 재게 된다. 반드시 밝힌다.
        spans = {(kst(r.start).date(), kst(r.end).date())
                 for _d, r in usable.values()}
        mixed = ('' if len(spans) <= 1 else
                 ' ⚠️ 고도별 수집 기간이 서로 달라 고도 간 비교(연직시어 포함)는 '
                 '성립하지 않습니다 — 같은 기간으로 다시 받아 대조하십시오: '
                 + ', '.join(f'{h}m {kst(usable[h][1].start):%Y-%m-%d}~'
                             f'{kst(usable[h][1].end):%Y-%m-%d}'
                             for h in sorted(usable)) + '.')

        parts = []
        for h in sorted(usable):
            _d, r = usable[h]
            s = r.stats or {}
            parts.append(
                f"{h}m 평균 {s.get('mean_ms')} m/s"
                f"(주풍향 {rawwind.dir_ko(s.get('prevailing_dir') or '')}"
                f" {float(s.get('dir_ratio') or 0) * 100:.0f}%"
                f" · 3m/s 미만 {float(s.get('calm_ratio') or 0) * 100:.0f}%)")

        # 두 고도를 받았으면 연직시어 지수를 역산해 함께 낸다. ASOS 환산이
        # α를 가정했던 것과 달리 이 부지의 실제 값이라, 허브고도가 조회
        # 고도와 다를 때 환산 가정이 하나 줄어든다.
        shear, alpha = '', None
        hs = sorted(usable)
        if len(hs) >= 2 and len(spans) <= 1:
            lo, hi = hs[0], hs[-1]
            a = rawwind.shear_alpha(
                float((usable[lo][1].stats or {}).get('mean_ms') or 0), lo,
                float((usable[hi][1].stats or {}).get('mean_ms') or 0), hi)
            if a is not None:
                alpha = round(a, 3)
                shear = (f' 두 고도에서 역산한 연직시어 지수 α는 {a:.2f}입니다 '
                         f'— 허브고도가 다르면 이 값으로 환산하십시오.')

        period = f"{kst(base.start):%Y-%m-%d} ~ {kst(base.end):%Y-%m-%d}"
        rotor = rawwind.rotor_radius_m()
        far = (f' 신청좌표에서 {base_d:,.0f}m 떨어져 있어 블레이드 반지름 '
               f'{rotor:,.0f}m를 더해도 유효지역(반지름 '
               f'{rawwind.VALID_RADIUS_M:,}m) 안입니다 — 여유 '
               f'{rawwind.VALID_RADIUS_M - base_d - rotor:,.0f}m.'
               if base_d >= 100 else
               f' 신청좌표가 이 호기 자리입니다 — 유효지역 안입니다.')

        return self.item(
            status=st,
            reason=(
                f'재현바람장 {period} 기준 — ' + ' · '.join(parts) + f'. {verdict}'
                + shear + mixed
                + f' 자료는 사업지 **대표 지점 한 곳**({base.lat:.5f}, '
                  f'{base.lng:.5f})에서 받은 값입니다.{far} 재현바람장 격자는 '
                  f'촘촘해 2~3km 떨어진 지점의 풍속이 눈에 띄게 다를 수 있으므로, '
                  f'호기별 값은 별도로 확인해야 합니다.'
                + f' 표본 {base.samples:,}개({base.coverage * 100:.0f}% 수집, '
                  f'{base.interval_min}분 간격).'
            ),
            difficulty=df,
            confidence=Confidence.MEDIUM,
            source_url='https://apihub.kma.go.kr',
            action_required=(
                '① 발전사업허가 신청 시 이 자료를 풍황 자료로 제출하십시오 '
                '(고시 개정으로 풍황계측기 설치를 갈음합니다) '
                '② 기종·허브고도가 정해지면 그 고도로 다시 받아 대조하십시오 '
                '③ 배치 조정 단계에서는 호기별 지점으로 확인하십시오'
            ),
            raw={'heights': {h: (r.stats or {}) for h, (_d, r) in usable.items()},
                 'match_distance_m': round(base_d, 1),
                 'center': [base.lat, base.lng],
                 'valid_radius_m': rawwind.VALID_RADIUS_M,
                 'rotor_m': rotor,
                 'margin_m': round(rawwind.VALID_RADIUS_M - base_d - rotor, 1),
                 'coverage': round(base.coverage, 3),
                 'alpha': alpha,
                 'samples': base.samples,
                 'expected_samples': base.expected_samples,
                 'interval_min': base.interval_min,
                 'period': [kst(base.start).date().isoformat(),
                            kst(base.end).date().isoformat()],
                 'mixed_period': len(spans) > 1},
        )
