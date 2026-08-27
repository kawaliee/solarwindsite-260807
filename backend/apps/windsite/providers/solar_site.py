"""
태양광 부지 전용 판정 항목
---------------------------------------------------------------
풍력에는 없고 태양광에만 성립하는 항목을 모았다. 공통 항목(용도지역·산지·
환경·국가유산 등)은 기존 어댑터가 그대로 처리한다.

  · 농지 경로        염도평가 → 타용도 일시사용 (당사 표준)
  · 부지 지장물      부지를 지나는 송전선로 · 구거 · 묘지
  · 매장유산 확인    사업가능 면적 3만m² 초과 시 (⚠️ 법정 의무 아님)
  · 출력제어 이력    참고 표기 — 점수에 넣지 않는다
  · 소음·반사광      주민 수용성 참고 — 점수에 넣지 않는다

■ 참고 항목은 점수를 흔들지 않는다

출력제어와 민원은 사업 판단에 중요하지만 **자동 판정할 근거가 없다.**
지역 단위 통계이거나 사례 기반이라 특정 부지에 그대로 적용되지 않는다.
그래서 상태를 POSSIBLE로 두고 사유에 정보만 싣는다 — 점수 산식은 판정
상태로만 움직이므로 이렇게 두면 점수에 영향이 없다.
"""
from __future__ import annotations

import logging

from .. import geo
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

#: 매장유산 확인을 안내하는 사업면적 기준(m²).
#:
#: ⚠️ **현행 법정 의무가 아니다.** 「매장유산 보호 및 조사에 관한 법률」에서
#:    사업시행자의 의무 지표조사 조항은 **2024-02-13 개정으로 삭제**되었다
#:    (제7조제1항·제3항·제4항 모두 `삭제 <2024.2.13>`, 현행 제6조의2는
#:    "국가 또는 지방자치단체는 … 할 수 있다"). 그럼에도 개발행위허가 협의
#:    단계에서 관할 지자체·국가유산청이 확인을 요구하는 실무가 남아 있어
#:    **참고선**으로 둔다. 문구에 그 사실을 반드시 밝힌다.
HERITAGE_AREA_M2 = 30_000

#: 농지 지목
FARM_JIMOK = ('전', '답', '과수원')
#: 지장물로 보는 지목 — 부지 안에 있으면 그만큼 배치가 빠진다
OBSTACLE_JIMOK = {'구거': '농업용 용배수로', '묘': '묘지', '유지': '저수지·유지'}

#: 염해농지 판정 기준 (농지법 시행규칙 제31조의2제1항) — 원문 확인 2026-08
SALINE_DSM = 5.50
SALINE_RATIO_PCT = 90

#: 영구건축물을 올릴 수 있는 지목.
#:
#: ⚠️ **농지 타용도 일시사용허가로는 영구건축물을 못 짓는다.** 부지 대부분을
#:    일시사용으로 가는 사업이라도 **154kV 변전소·전기실은 영구건축물**이라
#:    별도 부지가 필요하다(인허가 전문업체 장흥 검토서 2026-08 지적).
#:    이 사실을 말하지 않으면 사업이 다 된 줄 알고 진행하다 설계 단계에서
#:    막힌다. 구역 안에 후보가 될 비농지가 있는지 함께 세어 알린다.
PERMANENT_OK_JIMOK = ('대', '잡종지', '공장용지', '창고용지', '학교용지',
                      '주차장', '체육용지', '수도용지', '제방', '염전')


class FarmlandRouteProvider(LayerProvider):
    """농지 사업 경로 — 염도평가 기반 타용도 일시사용"""

    category = '규제/법령'
    item_name = '농지 사업 경로 (염도평가·일시사용)'
    data_source = '연속지적 지목 + 농지법'
    required_settings = ()
    default_law = '농지법'
    default_article = '제36조제1항제4호 · 시행규칙 제31조의2'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .cadastral import parse_jimok
        from .vworld import VworldClient

        feats, meta = VworldClient.fetch_area('lp_pa_cbnd_bubun', q.geom)
        if meta.get('strategy') == 'failed':
            return self.unknown(
                reason='연속지적을 조회하지 못해 농지 여부를 확인하지 못했습니다.',
                why='FETCH')

        farm_a = total_a = perm_a = 0.0
        n_farm = n_perm = 0
        perm_jimok: dict[str, int] = {}
        for f in feats:
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            try:
                gm = geo.clip(geo.to_metric(g), q.geom)
            except Exception:                               # noqa: BLE001
                continue
            if gm is None or gm.is_empty:
                continue
            a = geo.area_m2(gm)
            total_a += a
            jimok = parse_jimok((f.get('properties') or {}).get('jibun', ''))
            if jimok in FARM_JIMOK:
                farm_a += a
                n_farm += 1
            elif jimok in PERMANENT_OK_JIMOK:
                # 변전소·전기실처럼 **영구건축물**이 들어갈 자리 후보다.
                perm_a += a
                n_perm += 1
                perm_jimok[jimok] = perm_jimok.get(jimok, 0) + 1

        if not farm_a:
            return self.item(
                status=Status.POSSIBLE,
                reason='검토 구역에 농지 지목(전·답·과수원)이 없어 농지 절차가 '
                       '발생하지 않습니다.',
                difficulty=Difficulty.LOW, confidence=Confidence.MEDIUM,
                raw={'farm_area_m2': 0})

        ratio = farm_a / total_a * 100 if total_a else 0
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'검토 구역의 농지 지목 {n_farm:,}필지 · {farm_a / 10_000:,.1f}ha'
                f'(구역 지적면적의 {ratio:.0f}%)입니다. '
                f'**토양 염도 평가를 거쳐 농지 타용도 일시사용허가**로 사업이 '
                f'가능합니다 — 농지법 제36조제1항제4호가목과 시행규칙 '
                f'제31조의2는 「사업구역 내 농지면적의 100분의 {SALINE_RATIO_PCT} '
                f'이상이 필지별 토양 염도 {SALINE_DSM} dS/m 이상」인 지역에 대해 '
                f'농지전용이 아닌 일시사용을 허용합니다. '
                f'농업진흥구역이라도 시행령 제29조제7항제7호가 태양에너지 '
                f'발전설비를 허용 행위로 두고 있어 구역만으로 불가가 되지 '
                f'않습니다. **이 시스템은 염도를 측정하지 않으므로 대상 후보만 '
                f'특정한 것입니다.** '
                + _permanent_note(n_perm, perm_a, perm_jimok)),
            difficulty=Difficulty.MEDIUM,
            confidence=Confidence.HIGH,
            source_url='https://www.law.go.kr',
            action_required=(
                f'농림축산식품부장관이 정하는 방법·기관으로 토양 염도를 측정해 '
                f'필지별 {SALINE_DSM} dS/m 이상 여부와 구역 내 농지면적 대비 '
                f'{SALINE_RATIO_PCT}% 충족 여부를 확인하십시오. 일시사용은 '
                f'사용기간 제한이 있어 사업기간·PPA 기간과 대조가 필요합니다. '
                f'**변전소·전기실 부지는 따로 확보**해야 하므로 위치를 함께 '
                f'정하십시오.'),
            raw={'farm_parcels': n_farm, 'farm_area_m2': round(farm_a, 1),
                 'farm_ratio_pct': round(ratio, 1),
                 'saline_dsm': SALINE_DSM, 'saline_ratio_pct': SALINE_RATIO_PCT,
                 'permanent_parcels': n_perm,
                 'permanent_area_m2': round(perm_a, 1),
                 'permanent_jimok': perm_jimok})


class SiteObstacleProvider(LayerProvider):
    """부지 지장물 — 통과 송전선로·구거·묘지"""

    category = '인프라'
    item_name = '부지 지장물 (통과 선로·구거·묘지)'
    data_source = '연속지적 지목 + OpenStreetMap 송전선로'
    required_settings = ()
    default_law = '해당 없음 (배치·시공 제약)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .cadastral import parse_jimok
        from .vworld import VworldClient

        found: dict[str, float] = {}
        counts: dict[str, int] = {}
        feats, meta = VworldClient.fetch_area('lp_pa_cbnd_bubun', q.geom)
        for f in feats:
            j = parse_jimok((f.get('properties') or {}).get('jibun', ''))
            if j not in OBSTACLE_JIMOK:
                continue
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            try:
                gm = geo.clip(geo.to_metric(g), q.geom)
            except Exception:                               # noqa: BLE001
                continue
            if gm is None or gm.is_empty:
                continue
            found[j] = found.get(j, 0.0) + geo.area_m2(gm)
            counts[j] = counts.get(j, 0) + 1

        lines = self._crossing_lines(q)

        if not found and not lines:
            return self.item(
                status=Status.POSSIBLE,
                reason='부지를 지나는 송전선로와 구거·묘지 지목이 조회되지 '
                       '않았습니다. 다만 관정·상하수도·가스관 등 **지하시설물은 '
                       '공개 자료가 없어** 확인하지 못했습니다.',
                difficulty=Difficulty.LOW, confidence=Confidence.LOW,
                action_required='굴착 전 지하시설물 조회와 현장 확인이 필요합니다.',
                raw={})

        parts = []
        obstacle_a = sum(found.values())
        for j, a in sorted(found.items(), key=lambda kv: -kv[1]):
            parts.append(f'{OBSTACLE_JIMOK[j]}({j}) {counts[j]}필지 '
                         f'{a / 10_000:,.2f}ha')
        if lines:
            parts.append(f'부지 통과 송전선로 {lines["count"]}개 구간 '
                         f'{lines["length_m"]:,.0f}m')

        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                '부지 안에 배치를 막는 요소가 있습니다 — ' + ' · '.join(parts) + '. '
                + ('부지를 지나는 가공선로 아래는 이격을 두어야 해 띠 모양으로 '
                   '배치가 빠지며, 부지 한가운데를 지나면 어레이가 두 조각으로 '
                   '갈립니다. ' if lines else '')
                + f'지장물 지목 합계는 {obstacle_a / 10_000:,.2f}ha로 '
                  f'검토 면적의 {obstacle_a / max(geo.area_m2(q.geom), 1) * 100:.1f}%입니다. '
                  '태양광은 부지를 빽빽하게 채우는 사업이라 이 면적이 곧 설비용량 '
                  '손실입니다. 관정·상하수도·가스관 등 지하시설물은 공개 자료가 '
                  '없어 확인하지 못했습니다.'),
            difficulty=Difficulty.MEDIUM, confidence=Confidence.MEDIUM,
            action_required='한전 협의로 선로 이설·이격 기준을 확인하고, 구거·묘지는 '
                            '이설·개장 가능성과 비용을 검토하십시오. 지하시설물은 '
                            '굴착 전 조회가 필요합니다.',
            raw={'obstacle_area_m2': round(obstacle_a, 1),
                 'by_jimok': {k: round(v, 1) for k, v in found.items()},
                 'crossing_lines': lines})

    # ------------------------------------------------------------------
    def _crossing_lines(self, q: SiteQuery) -> dict | None:
        """
        **부지를 지나는** 송전선로. 가까운 선로가 아니라 통과하는 선로다.

        `OsmGridProvider._lines`는 최근접 거리만 돌려주므로 통과 여부를 알 수
        없다. 여기서는 도형을 받아 부지와 교차하는 구간의 길이를 잰다.

        조회 실패는 조용히 넘긴다 — 선로 하나 때문에 지장물 항목 전체를
        죽일 이유가 없다.
        """
        from shapely.geometry import LineString

        from .osm import SNAP_GRID_DEG, OverpassClient, _max_voltage, snap

        clat, clng = snap(q.lat, q.lng, SNAP_GRID_DEG)
        ql = f"""[out:json][timeout:60];
(
  way["power"="line"](around:3000,{clat},{clng});
  way["power"="minor_line"](around:3000,{clat},{clng});
);
out geom tags;"""
        try:
            els = OverpassClient.query(ql)
        except Exception:                                   # noqa: BLE001
            logger.info('송전선로 조회 실패 — 지장물 판정에서 제외')
            return None

        total, n, kv = 0.0, 0, []
        for el in els or []:
            pts = el.get('geometry') or []
            if len(pts) < 2:
                continue
            try:
                gm = geo.to_metric(LineString([(p['lon'], p['lat']) for p in pts]))
                seg = gm.intersection(q.geom)
            except Exception:                               # noqa: BLE001
                continue
            length = float(getattr(seg, 'length', 0.0))
            if length <= 0:
                continue
            total += length
            n += 1
            v = _max_voltage((el.get('tags') or {}).get('voltage'))
            if v:
                kv.append(v)
        if not n:
            return None
        return {'count': n, 'length_m': round(total, 1),
                'max_kv': max(kv) if kv else None}


class HeritageSurveyCheckProvider(LayerProvider):
    """매장유산 확인 — 사업가능 면적 기준 (⚠️ 법정 의무 아님)"""

    category = '안전/문화재'
    item_name = '매장유산 확인 (사업면적 기준)'
    data_source = '국가유산청 (확인처 안내)'
    required_settings = ()
    default_law = '매장유산 보호 및 조사에 관한 법률'
    default_article = '제6조의2 (국가 등에 의한 지표조사)'

    def __init__(self, usable_m2: float | None = None):
        #: 가용 + 조건부 면적. 배제는 애초에 사업 대상이 아니라 뺀다.
        self.usable_m2 = usable_m2

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        a = self.usable_m2 if self.usable_m2 else geo.area_m2(q.geom)
        over = a >= HERITAGE_AREA_M2
        base = f'사업가능 면적(제약없음 + 조건부) {a / 10_000:,.1f}ha = {a:,.0f}㎡. '
        note = (
            '⚠️ 「매장유산 보호 및 조사에 관한 법률」의 **사업시행자 의무 지표조사 '
            '조항은 2024-02-13 개정으로 삭제**되었습니다(제7조제1항·제3항·제4항 '
            '삭제, 현행 제6조의2는 "국가 또는 지방자치단체는 … 할 수 있다"). '
            f'따라서 {HERITAGE_AREA_M2:,}㎡ 기준은 **현행 법정 의무가 아니라** '
            '개발행위허가 협의 단계에서 관할 지자체·국가유산청이 확인을 요구하는 '
            '실무 관행에 따른 참고선입니다.')

        if not over:
            return self.item(
                status=Status.POSSIBLE,
                reason=base + f'참고 기준({HERITAGE_AREA_M2:,}㎡)을 밑돕니다. ' + note,
                difficulty=Difficulty.LOW, confidence=Confidence.MEDIUM,
                raw={'usable_m2': round(a, 1), 'threshold_m2': HERITAGE_AREA_M2})

        return self.item(
            status=Status.CONDITIONAL,
            reason=base + f'참고 기준({HERITAGE_AREA_M2:,}㎡)을 넘습니다 — '
                          '매장유산 존재 여부 확인이 필요할 수 있습니다. ' + note,
            difficulty=Difficulty.MEDIUM, confidence=Confidence.MEDIUM,
            source_url='https://www.khs.go.kr',
            action_required='국가유산청 및 관할 지자체에 매장유산 유존지역 해당 '
                            '여부와 협의 필요성을 확인하십시오.',
            raw={'usable_m2': round(a, 1), 'threshold_m2': HERITAGE_AREA_M2})


class CurtailmentInfoProvider(LayerProvider):
    """
    출력제어 이력 — **참고 표기 전용**.

    상태를 POSSIBLE로 고정한다. 점수 산식은 판정 상태로만 움직이므로 이렇게
    두면 참고 정보가 점수를 흔들지 않는다. 이용률 산정(`solar.yield_estimate`)
    에도 관여하지 않는다 — 지역 단위 사실이라 특정 부지에 그대로 적용할 수 없다.
    """

    category = '사업성'
    item_name = '출력제어 이력 (참고)'
    data_source = '전력거래소 공표 자료 (지역 단위)'
    required_settings = ()
    default_law = '해당 없음 (참고 정보)'

    #: 출력제어가 보고된 지역. 시·도 단위 사실이며 부지 판정이 아니다.
    REPORTED = {
        '제주특별자치도': '재생에너지 출력제어가 상시 발생하는 지역',
        '전라남도': '태양광 밀집으로 출력제어 사례가 보고된 지역',
        '전북특별자치도': '태양광 밀집으로 출력제어 사례가 보고된 지역',
        '전남광주통합특별시': '태양광 밀집으로 출력제어 사례가 보고된 지역',
    }

    def __init__(self, sido: str = '', sigungu: str = ''):
        self.sido = sido
        self.sigungu = sigungu

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        hit = self.REPORTED.get(self.sido)
        if hit:
            reason = (f'**참고** — {self.sido}는 {hit}입니다. 인근 사업지에서 '
                      '출력제어가 발생한 이력이 보고된 지역이므로 실제 발전량이 '
                      '모델 추정보다 낮을 수 있습니다. '
                      '**이 항목은 판정·점수·이용률 산정에 반영하지 않습니다** — '
                      '지역 단위 사실이라 특정 부지에 그대로 적용할 수 없습니다.')
        else:
            reason = ('인근에서 출력제어가 보고된 이력이 확인되지 않았습니다. '
                      '다만 계통 상황은 변하므로 접속 협의 시 한전·전력거래소에 '
                      '확인하십시오. **판정·점수에 반영하지 않는 참고 항목입니다.**')
        return self.item(
            status=Status.POSSIBLE, reason=reason,
            difficulty=Difficulty.LOW, confidence=Confidence.LOW,
            source_url='https://epsis.kpx.or.kr',
            action_required='한전·전력거래소에 해당 계통의 출력제어 이력과 전망을 '
                            '확인하십시오.',
            raw={'reported': bool(hit), 'sido': self.sido})


class AcceptanceInfoProvider(LayerProvider):
    """주민 수용성(소음·반사광) — **참고 표기 전용**, 점수 미반영"""

    category = '사업성'
    item_name = '주민 수용성 (소음·반사광 참고)'
    data_source = '확인처 안내'
    required_settings = ()
    default_law = '해당 없음 (참고 정보)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        return self.item(
            status=Status.POSSIBLE,
            reason=(
                '**참고** — 태양광은 인버터 소음과 모듈 반사광이 민원 사유가 '
                '됩니다. 반사광은 주거지·도로·항공기 진입 방향과의 관계에 따라 '
                '달라져 자동 판정이 성립하지 않고, 민원 이력은 공개 자료가 '
                '없습니다. **이 항목은 판정·점수에 반영하지 않습니다.** '
                '주민 수용성은 조례 이격거리 판정과 별개로 사업 성패를 가르는 '
                '경우가 많아 항목으로 남겨 둡니다.'),
            difficulty=Difficulty.LOW, confidence=Confidence.LOW,
            action_required='관할 지자체 민원 이력과 인근 사업지 사례를 확인하고, '
                            '필요 시 반사광 시뮬레이션·주민설명회를 계획에 '
                            '반영하십시오. 분쟁 발생 시 환경분쟁조정위원회가 '
                            '확인처입니다.',
            raw={})


def _permanent_note(n: int, area_m2: float, by_jimok: dict) -> str:
    """
    변전소·전기실 부지 안내.

    농지 일시사용허가는 **영구건축물을 허용하지 않는다.** 부지 대부분이
    일시사용으로 풀려도 변전소 자리는 따로 있어야 하므로, 구역 안에 후보가
    될 비농지가 있는지 여기서 함께 짚는다. 없으면 없다고 말한다 — 구역 밖에서
    구해야 한다는 뜻이라 사업 구도가 달라진다.
    """
    head = ('⚠️ **154kV 변전소·전기실은 영구건축물이라 일시사용허가 대상이 '
            '아닙니다.** 별도 부지를 확보해야 합니다 — ')
    if not n:
        return (head + '검토 구역 안에 영구건축물을 올릴 수 있는 지목'
                       f'({"·".join(PERMANENT_OK_JIMOK[:4])} 등)이 **없습니다.** '
                       '구역 밖에서 별도 매입·임차가 필요합니다.')
    kinds = ' · '.join(f'{k} {v}필지' for k, v in
                       sorted(by_jimok.items(), key=lambda x: -x[1])[:4])
    return (head + f'구역 안 비농지 {n:,}필지 · {area_m2 / 10_000:,.2f}ha'
                   f'({kinds})가 후보입니다. 변전소 소요면적과 계통 인입 '
                   f'방향을 함께 보고 위치를 정하십시오.')
