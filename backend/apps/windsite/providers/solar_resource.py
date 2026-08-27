"""
일사량 어댑터 — 태양광 사업성 판정
---------------------------------------------------------------
풍황이 풍력에서 하는 역할을 태양광에서는 일사량이 맡는다. 다만 값을 하나로
말하지 않는다. 성격이 다른 세 자료를 나란히 싣고 **편차를 밝힌다.**

  ① 기상청 GK2A DSR       위성 · 2km · 5개년 — 공간 분해능이 가장 좋다
  ② Global Solar Atlas    Solargis · 250m 장기평균 — 금융 실사에서 통용된다
  ③ 기상청 ASOS 일사       관측소 실측 — 모델값이 아닌 유일한 값

실측(2026-08): 홍성 1,505 / 1,484 / 1,571 (편차 5.7%)
               삼척 1,346 / 1,462 / 1,509 (편차 11.3%)

삼척처럼 10%를 넘게 벌어지는 곳이 실제로 있다. 자료 하나만 썼다면 그
사실조차 몰랐을 것이다. 발전량 추정에 10% 차이는 사업 판단을 가른다.

■ 일사량이 아니라 **발전시간·이용률**로 말한다

일사량(kWh/m²)은 중간 지표다. 사업 판단에 쓰는 값은 일 평균 발전시간과
이용률이라, 그 두 값을 앞에 놓고 일사량은 근거로 뒤에 붙인다.

산정은 **경사각 20° · 성능비 84%** 를 기본으로 한다. Global Solar Atlas가
주는 PVOUT은 최적경사(국내 31~35°) 기준인데, 실제 부지는 이격·음영·조성비
때문에 15~20°로 눕히는 경우가 많아 그대로 쓰면 과대평가가 된다.

■ 이 항목은 가부를 선고하지 않는다

일사량은 규제가 아니라 사업성이다. 법령이 정한 하한이 없으므로 '불가'가
성립하지 않는다. 사내 기준 이용률도 **참고선으로만** 긋는다 — 계산값을
그 값에 맞추지 않는다. 둘을 나란히 놓고 부지가 선의 위인지 아래인지를
보여주는 것이 목적이다.
"""
from __future__ import annotations

import logging

from .. import solar
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

#: 국내 연간 수평면 일사량의 통상 범위(kWh/m²/yr). 법정 기준이 아니라
#: '이 값이 전국에서 어디쯤인가'를 말하기 위한 참고선이다.
KR_LOW, KR_HIGH = 1350, 1550

#: 자료 간 편차가 이보다 크면 값 하나를 믿고 사업성을 잡지 말라고 경고한다.
#: 발전량 추정에서 10%는 수익성 판단을 가르는 폭이다.
SPREAD_WARN_PCT = 10.0


class SolarResourceProvider(LayerProvider):
    """일사량(연간 수평면 전일사량) — 위성·모델·실측 3종 대조"""

    category = '사업성'
    item_name = '일사량(연간 수평면 전일사량)'
    data_source = '기상청 GK2A · Global Solar Atlas · 기상청 ASOS'
    #: ASOS는 키가 있어야 하지만, 없어도 위성·GSA로 값이 나온다.
    #: required로 두면 키 하나 때문에 항목 전체가 죽는다.
    required_settings = ()
    optional_settings = ('KMA_API_KEY',)
    default_law = '해당 없음 (비규제 · 사업성 판단 영역)'

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        lat, lng = q.lat, q.lng
        cap = q.capacity_mw

        kma = gsa = asos = None
        try:
            kma = solar.kma_dsr(lat, lng)
        except solar.SolarUnavailable as e:
            logger.info('기상청 일사량 미사용: %s', e)
        except Exception:                                       # noqa: BLE001
            logger.exception('기상청 일사량 판독 실패')

        gsa = solar.gsa(lat, lng)

        try:
            asos = solar.asos_gsr(lat, lng)
        except Exception:                                       # noqa: BLE001
            logger.exception('ASOS 일사량 조회 실패')

        rows = [(n, v) for n, v in (
            ('기상청 GK2A 위성(2km)', (kma or {}).get('ghi_kwh')),
            ('Global Solar Atlas(250m)', (gsa or {}).get('ghi_kwh')),
            ('기상청 ASOS 실측', (asos or {}).get('ghi_kwh')),
        ) if v]

        if not rows:
            return self.unknown(
                reason=('일사량 자료를 한 곳에서도 받지 못했습니다. 일사량이 낮다는 '
                        '뜻이 아니라 조회하지 못했다는 뜻입니다.'),
                action_required=(
                    '기상청 태양기상자원지도(netCDF)를 backend/data/solar/'
                    'kma_resource_map/ 에 넣거나, 네트워크·인증키 상태를 '
                    '확인하십시오.'),
                why='FETCH',
            )

        est = solar.yield_estimate(lat, lng, capacity_mw=cap)
        return self._report(rows, kma, gsa, asos, est)

    # ------------------------------------------------------------------
    def _report(self, rows, kma, gsa, asos, est=None) -> AnalysisItem:
        vals = [v for _, v in rows]
        lo, hi = min(vals), max(vals)
        avg = sum(vals) / len(vals)
        spread = (hi - lo) / avg * 100 if avg else 0.0

        detail = ' · '.join(f'{n} {v:,}' for n, v in rows)
        # 발전시간·이용률을 맨 앞에 둔다. 사업 판단에 실제로 쓰이는 값이라
        # 일사량 뒤에 묻히면 읽히지 않는다.
        head = ''
        if est:
            head = (f'일 평균 발전시간 {est["hours_per_day"]:.2f} h/일 '
                    f'(이용률 {est["capacity_factor"]:.1f}%) — '
                    f'경사각 {est["tilt_deg"]}° · 성능비 {est["pr"] * 100:.0f}% 기준. ')
            if est.get('annual_mwh'):
                head += f'입력 설비용량 기준 연간 {est["annual_mwh"]:,.0f} MWh. '
            gap = est['capacity_factor'] - est['inhouse_cf']
            head += (f'사내 기준({est["inhouse_cf"]}% · '
                     f'{est["inhouse_hours"]}h/일) 대비 '
                     f'{"+" if gap >= 0 else ""}{gap:.1f}%p입니다. ')
        head += f'연간 수평면 전일사량 {lo:,}~{hi:,} kWh/m²/yr — {detail}. '

        # 자료의 성격을 밝힌다. 숫자만 보면 셋이 같은 종류인 줄 안다.
        notes = []
        if kma:
            notes.append(
                f'위성값은 {min(kma["by_year"])}~{max(kma["by_year"])}년 '
                f'{kma["years"]}개년 평균이며(연간 변동폭 {kma["spread_w_m2"]}W/m²), '
                + solar.DSR_CALIBRATION_NOTE)
        if asos:
            notes.append(
                f'실측값은 {asos["station"]} 관측소({asos["distance_km"]}km, '
                f'{asos["days"]}일) 합계이며, 부지와 지형이 다를 수 있습니다.')
        if gsa and gsa.get('pvout_kwh_kwp'):
            notes.append(
                f'Global Solar Atlas 기준 발전량은 {gsa["pvout_kwh_kwp"]:,} kWh/kWp/yr, '
                f'최적 경사각은 {gsa["optimal_tilt_deg"]}도입니다(결정질 실리콘 기준).')

        if est:
            notes.append(est['basis'])
            notes.append(
                '산정에 출력제어·어레이 음영·설비 노후·가동정지를 반영하지 '
                '않았습니다 — 실제 운영값은 이보다 낮습니다.')

        raw = {'sources': dict(rows), 'kma': kma, 'gsa': gsa, 'asos': asos,
               'yield': est, 'spread_pct': round(spread, 1)}

        # 편차가 크면 값 하나로 사업성을 잡지 말라고 경고한다.
        if spread >= SPREAD_WARN_PCT:
            return self.item(
                status=Status.UNKNOWN,
                reason=(head + f'자료 간 편차가 {spread:.1f}%로 큽니다 — '
                        '어느 값을 쓰느냐에 따라 발전량 추정이 그만큼 달라집니다. '
                        + ' '.join(notes)),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required=('발전량 산정에는 현장 일사계 실측 또는 상용 자원평가 '
                                 '보고서를 쓰십시오. 이 값들은 후보지 선별용입니다.'),
                source_url='https://globalsolaratlas.info',
                raw=raw, unknown_reason='NO_CRITERIA')

        if avg >= KR_HIGH:
            band = f'국내 상위권({KR_HIGH:,} 이상)에 듭니다.'
            status, diff = Status.POSSIBLE, Difficulty.LOW
        elif avg >= KR_LOW:
            band = f'국내 통상 범위({KR_LOW:,}~{KR_HIGH:,}) 안입니다.'
            status, diff = Status.POSSIBLE, Difficulty.LOW
        else:
            band = f'국내 통상 범위({KR_LOW:,}~{KR_HIGH:,})를 밑돕니다.'
            status, diff = Status.CONDITIONAL, Difficulty.MEDIUM

        return self.item(
            status=status,
            reason=head + f'세 자료 평균 {avg:,.0f}으로 {band} ' + ' '.join(notes),
            difficulty=diff,
            confidence=Confidence.MEDIUM,
            action_required=('사업 확정 전 현장 일사계 실측 또는 상용 자원평가로 '
                             '발전량을 확정하십시오. 이 값은 후보지 선별용입니다.'),
            source_url='https://globalsolaratlas.info',
            raw=raw)
