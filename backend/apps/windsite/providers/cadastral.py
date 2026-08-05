"""
연속지적 기반 필지 분석
---------------------------------------------------------------
V-World 연속지적도(`lp_pa_cbnd_bubun`)를 폴리곤째 받아
  · 지목 구성          (지목 부호 → 정식 지목명)
  · 검토 반경 내 실면적 (원 면적이 아닌 **필지 폴리곤 교차 면적**)
  · 지목별 가용/제약 면적
을 산출한다.

⚠️ 실측으로 확인된 사항 (docs/WINDSITE_VWORLD_LAYERS.md)
    `jibun` 속성값은 `"66 도"` 형태로 **지목 부호 1글자**가 붙는다.
    `'임야'` 같은 정식 명칭으로 매칭하면 항상 실패한다.
"""
from __future__ import annotations

import logging

from .. import geo
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery
from .vworld import VworldClient

logger = logging.getLogger(__name__)

#: 지목 부호 → 정식 지목명 (공간정보관리법 시행령 제58조 · 지적업무처리규정)
JIMOK_CODES: dict[str, str] = {
    '전': '전', '답': '답', '과': '과수원', '목': '목장용지', '임': '임야',
    '광': '광천지', '염': '염전', '대': '대', '장': '공장용지', '학': '학교용지',
    '차': '주차장', '주': '주유소용지', '창': '창고용지', '도': '도로',
    '철': '철도용지', '제': '제방', '천': '하천', '구': '구거', '유': '유지',
    '양': '양어장', '수': '수도용지', '공': '공원', '체': '체육용지',
    '원': '유원지', '종': '종교용지', '사': '사적지', '묘': '묘지', '잡': '잡종지',
}

#: 풍력 부지로 활용 가능한 지목 (산지 인허가 경로)
FAVORABLE = {'임야', '잡종지', '목장용지'}
#: 별도 전용 절차가 필요한 지목
NEEDS_CONVERSION = {'전': '농지법', '답': '농지법', '과수원': '농지법',
                    '대': '건축법·국토계획법', '묘지': '장사 등에 관한 법률'}
#: 부지에서 제외되는 공공용지 성격 지목
EXCLUDED = {'도로', '하천', '구거', '제방', '철도용지', '수도용지'}


def parse_jimok(jibun: str) -> str:
    """`"66 도"` / `"산 12-3 임"` → 정식 지목명. 판별 불가 시 빈 문자열."""
    if not jibun:
        return ''
    for ch in reversed(jibun.strip()):
        if ch in JIMOK_CODES:
            return JIMOK_CODES[ch]
        if ch.isdigit() or ch in ' -':
            continue
    return ''


class CadastralProvider(LayerProvider):
    """필지 경계·지목 및 검토 반경 내 실면적"""

    category = '규제/법령'
    item_name = '필지·지적 분석'
    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 연속지적도'
    default_law = '공간정보의 구축 및 관리 등에 관한 법률'
    default_article = '제2조(정의) — 지목'

    LAYER_CODE = '연속지적'
    FALLBACK_LAYER_ID = 'lp_pa_cbnd_bubun'

    def _layer(self):
        from ..models import RegulationLayer                    # 지연 import
        return RegulationLayer.objects.filter(code=self.LAYER_CODE, is_active=True).first()

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        lyr = self._layer()
        layer_id = lyr.layer_id if lyr else self.FALLBACK_LAYER_ID

        feats, meta = VworldClient.fetch_all(layer_id, q.lat, q.lng, q.radius_m)
        if VworldClient.status_of(meta['payload']) == 'ERROR':
            return self.unknown(
                reason=f'연속지적 조회 실패 — {VworldClient.error_text(meta["payload"])[:150]}',
                action_required='V-World 인증키·도메인 등록 상태를 확인하십시오.',
            )

        if not feats:
            return self.unknown(
                reason='검토 반경 내 지적 정보가 조회되지 않았습니다.',
                action_required='정부24 또는 일사편리에서 토지대장을 직접 확인하십시오.',
            )

        stat = self._measure(q, feats)
        if stat is None:
            # 공간연산 불가 — 면적 없이 지목 구성만 보고
            return self._without_geometry(feats)

        stat['truncated'] = meta['truncated']
        stat['pages'] = meta['pages']
        return self._report(q, stat)

    # ------------------------------------------------------------------
    def _measure(self, q: SiteQuery, feats: list[dict]) -> dict | None:
        """검토 원과 필지 폴리곤의 교차 면적을 지목별로 집계한다."""
        try:
            site = geo.point_metric(q.lat, q.lng)
        except geo.GeoUnavailable:
            return None

        circle = site.buffer(q.radius_m)
        by_jimok: dict[str, dict] = {}
        parcels: list[dict] = []
        unknown_jimok = 0

        for f in feats:
            props = f.get('properties') or {}
            jimok = parse_jimok(props.get('jibun', ''))
            if not jimok:
                unknown_jimok += 1

            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            try:
                gm = geo.to_metric(g)
                inter = gm.intersection(circle)
            except Exception:                                    # noqa: BLE001
                logger.debug('필지 교차 연산 실패', exc_info=True)
                continue
            if inter.is_empty:
                continue

            a = geo.area_m2(inter)
            key = jimok or '미상'
            b = by_jimok.setdefault(key, {'area_m2': 0.0, 'count': 0})
            b['area_m2'] += a
            b['count'] += 1
            parcels.append({
                'pnu': props.get('pnu', ''),
                'addr': props.get('addr', ''),
                'jibun': props.get('jibun', ''),
                'jimok': key,
                'area_in_radius_m2': round(a, 1),
                'jiga': props.get('jiga', ''),
            })

        total = sum(v['area_m2'] for v in by_jimok.values())
        usable = sum(v['area_m2'] for k, v in by_jimok.items() if k in FAVORABLE)
        excluded = sum(v['area_m2'] for k, v in by_jimok.items() if k in EXCLUDED)
        conversion = {k: v for k, v in by_jimok.items() if k in NEEDS_CONVERSION}

        parcels.sort(key=lambda p: p['area_in_radius_m2'], reverse=True)
        return {
            'by_jimok': {k: {'area_m2': round(v['area_m2'], 1), 'count': v['count']}
                         for k, v in sorted(by_jimok.items(),
                                            key=lambda kv: kv[1]['area_m2'], reverse=True)},
            'parcel_count': len(parcels),
            'unknown_jimok': unknown_jimok,
            'total_parcel_area_m2': round(total, 1),
            'usable_area_m2': round(usable, 1),
            'excluded_area_m2': round(excluded, 1),
            'conversion_needed': {k: round(v['area_m2'], 1) for k, v in conversion.items()},
            'circle_area_m2': round(geo.area_m2(circle), 1),
            'top_parcels': parcels[:15],
        }

    # ------------------------------------------------------------------
    def _report(self, q: SiteQuery, s: dict) -> AnalysisItem:
        total = s['total_parcel_area_m2'] or 1.0
        usable_pct = s['usable_area_m2'] / total * 100
        compo = ', '.join(f'{k} {v["area_m2"]:,.0f}㎡({v["count"]}필지)'
                          for k, v in list(s['by_jimok'].items())[:6])

        head = (
            f'검토 반경 {q.radius_m:,}m 내 {s["parcel_count"]:,}개 필지, '
            f'지적 면적 합계 {s["total_parcel_area_m2"]:,.0f}㎡. 지목 구성 — {compo}.'
        )
        if s.get('truncated'):
            head += (' ⚠️ 조회 상한에 도달해 일부 필지가 누락되었을 수 있습니다 '
                     '(면적이 과소 산출될 수 있음).')
        usable_txt = (
            f' 산지 인허가 경로로 진행 가능한 지목(임야·잡종지·목장용지) 면적은 '
            f'{s["usable_area_m2"]:,.0f}㎡({usable_pct:.0f}%)입니다.'
        )

        if s['conversion_needed']:
            det = ', '.join(f'{k} {v:,.0f}㎡' for k, v in s['conversion_needed'].items())
            return self.item(
                status=Status.CONDITIONAL,
                reason=head + usable_txt + f' 전용 절차가 필요한 지목이 포함되어 있습니다 — {det}.',
                difficulty=Difficulty.MEDIUM,
                law='농지법',
                article='제34조(농지의 전용허가·협의)',
                confidence=Confidence.MEDIUM,
                action_required='해당 지목은 농지전용허가·농지보전부담금 등 별도 절차가 필요합니다. '
                                '배치 조정으로 회피 가능한지 함께 검토하십시오.',
                raw=s,
            )

        if usable_pct < 50:
            return self.item(
                status=Status.CONDITIONAL,
                reason=head + usable_txt + ' 가용 지목 비율이 낮아 배치 가능 면적 확보를 별도 검토해야 합니다.',
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.MEDIUM,
                action_required='토지대장으로 소유구분(국·공유지)과 실제 사용 가능 필지를 특정하십시오.',
                raw=s,
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=head + usable_txt,
            difficulty=Difficulty.LOW,
            law='산지관리법',
            article='제14조(산지전용허가) · 제15조의2(산지일시사용허가·신고)',
            confidence=Confidence.MEDIUM,
            action_required='소유구분(국·공유지 여부)과 토지사용승낙 확보 계획을 수립하십시오.',
            raw=s,
        )

    # ------------------------------------------------------------------
    def _without_geometry(self, feats: list[dict]) -> AnalysisItem:
        counts: dict[str, int] = {}
        for f in feats:
            j = parse_jimok((f.get('properties') or {}).get('jibun', '')) or '미상'
            counts[j] = counts.get(j, 0) + 1
        return self.item(
            status=Status.UNKNOWN,
            reason=(
                f'{len(feats):,}개 필지가 조회되었으나 공간연산 라이브러리(shapely/pyproj)가 없어 '
                f'면적을 산출하지 못했습니다. 지목 구성: {counts}'
            ),
            difficulty=Difficulty.MEDIUM,
            confidence=Confidence.LOW,
            action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            raw={'jimok_counts': counts, 'parcel_count': len(feats)},
        )
