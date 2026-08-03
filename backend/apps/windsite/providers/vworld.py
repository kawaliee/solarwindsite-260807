"""
브이월드(V-World) 기반 어댑터 — 토지이용규제 / 연속지적
---------------------------------------------------------------
V-World 데이터 API 2.0 (GetFeature) 사용.
  https://api.vworld.kr/req/data?service=data&request=GetFeature&data=<레이어>&...

인증키 발급: https://www.vworld.kr  →  오픈API 인증키 신청 (사용 도메인 등록 필요)
  ※ 로컬 개발 시 'localhost' 도메인을 반드시 등록해야 호출이 허용됩니다.
"""
from __future__ import annotations

from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

VWORLD_DATA_URL = 'https://api.vworld.kr/req/data'

#: 용도지역·지구 중 풍력 입지에 결정적 영향을 주는 구역 키워드 → (상태, 난이도, 근거)
#: 판정 근거를 코드에 고정하지 않고, 키워드 매칭 결과를 사유로 제시한다.
CRITICAL_ZONES: dict[str, tuple[Status, Difficulty, str, str]] = {
    '자연환경보전지역': (Status.CONDITIONAL, Difficulty.CRITICAL,
                  '국토의 계획 및 이용에 관한 법률', '제76조(용도지역에서의 건축물의 건축 제한 등)'),
    '개발제한구역': (Status.CONDITIONAL, Difficulty.CRITICAL,
                '개발제한구역의 지정 및 관리에 관한 특별조치법', '제12조(행위제한)'),
    '농업진흥': (Status.CONDITIONAL, Difficulty.HIGH,
              '농지법', '제32조(용도구역에서의 행위 제한)'),
    '보전산지': (Status.CONDITIONAL, Difficulty.HIGH,
              '산지관리법', '제12조(보전산지에서의 행위제한)'),
    '공익용산지': (Status.CONDITIONAL, Difficulty.HIGH,
               '산지관리법', '제12조제2항'),
    '백두대간': (Status.CONDITIONAL, Difficulty.CRITICAL,
              '백두대간 보호에 관한 법률', '제7조(행위 제한)'),
    '공원': (Status.CONDITIONAL, Difficulty.CRITICAL,
           '자연공원법', '제23조(공원구역에서의 행위 제한)'),
    '상수원보호': (Status.CONDITIONAL, Difficulty.CRITICAL,
               '수도법', '제7조(상수원보호구역 지정 등)'),
    '군사기지': (Status.CONDITIONAL, Difficulty.HIGH,
              '군사기지 및 군사시설 보호법', '제13조(행정기관의 처분등에 관한 협의)'),
}


class _VworldBase(LayerProvider):
    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 데이터 API'

    def fetch(self, layer: str, q: SiteQuery, size: int = 100) -> dict:
        params = {
            'service': 'data',
            'request': 'GetFeature',
            'data': layer,
            'key': settings.VWORLD_API_KEY,
            'domain': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
            'geomFilter': f'POINT({q.lng} {q.lat})',
            'buffer': str(q.radius_m),
            'size': str(size),
            'format': 'json',
            'crs': 'EPSG:4326',
        }
        res = self.get(VWORLD_DATA_URL, params)
        res.raise_for_status()
        return res.json()

    @staticmethod
    def features(payload: dict) -> list[dict]:
        try:
            return payload['response']['result']['featureCollection']['features']
        except (KeyError, TypeError):
            return []

    @staticmethod
    def status_of(payload: dict) -> str:
        try:
            return payload['response']['status']
        except (KeyError, TypeError):
            return 'UNKNOWN'


class LandUseRegulationProvider(_VworldBase):
    """1. 토지이용규제 (용도지역·지구)"""

    category = '규제/법령'
    item_name = 'V-World 토지이용규제'
    default_law = '국토의 계획 및 이용에 관한 법률'
    default_article = '제76조'

    #: 용도지역지구도 레이어
    LAYER = 'LT_C_UQ111'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        payload = self.fetch(self.LAYER, q)
        if self.status_of(payload) == 'ERROR':
            return self.unknown(
                reason='V-World 응답이 ERROR입니다. 인증키 또는 등록 도메인을 확인하십시오.',
                action_required='V-World 마이페이지에서 인증키 상태와 사용 도메인(localhost) 등록을 확인하십시오.',
            )

        feats = self.features(payload)
        zone_names = sorted({
            (f.get('properties') or {}).get('dgm_nm')
            or (f.get('properties') or {}).get('DGM_NM')
            or (f.get('properties') or {}).get('prpos_area_dstrc_nm', '')
            for f in feats
        } - {''})

        if not feats:
            return self.item(
                status=Status.UNKNOWN,
                reason='검토 반경 내 용도지역·지구 정보가 조회되지 않았습니다. (레이어 미제공 구역이거나 좌표 오류 가능)',
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='토지이음(eum.go.kr)에서 대상 필지의 토지이용계획확인원을 직접 확인하십시오.',
                raw={'feature_count': 0},
            )

        # 결정적 규제 구역 매칭
        hits: list[tuple[str, Status, Difficulty, str, str]] = []
        for name in zone_names:
            for kw, (st, df, law, art) in CRITICAL_ZONES.items():
                if kw in name:
                    hits.append((name, st, df, law, art))
                    break

        if not hits:
            return self.item(
                status=Status.POSSIBLE,
                reason=f'검토 반경 내 용도지역·지구: {", ".join(zone_names[:8])} — 풍력 입지를 원천 배제하는 구역은 조회되지 않았습니다.',
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://www.vworld.kr',
                action_required='개발행위허가 기준(지자체 도시계획조례) 충족 여부는 별도 확인이 필요합니다.',
                raw={'zones': zone_names},
            )

        worst = max(hits, key=lambda h: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(h[2].value))
        names = ', '.join(h[0] for h in hits)
        return self.item(
            status=worst[1],
            reason=f'검토 반경이 규제 구역과 저촉됩니다: {names}. 해당 구역의 행위제한 규정에 따른 개별 검토·협의가 필요합니다.',
            difficulty=worst[2],
            law=worst[3],
            article=worst[4],
            confidence=Confidence.MEDIUM,
            source_url='https://www.vworld.kr',
            action_required='토지이용계획확인원 발급 후 해당 구역 소관기관과 사전 협의하십시오.',
            raw={'zones': zone_names, 'hits': [h[0] for h in hits]},
        )


class CadastralProvider(_VworldBase):
    """2. 필지 경계 및 지적 (지목·소유구분)"""

    category = '규제/법령'
    item_name = '필지·지적 분석'
    default_law = '공간정보의 구축 및 관리 등에 관한 법률'
    default_article = '제2조(정의) — 지목'

    LAYER = 'LP_PA_CBND_BUBUN'   # 연속지적도(부분)

    #: 풍력 입지에 유리/불리한 지목
    FAVORABLE = {'임야', '잡종지', '목장용지'}
    DIFFICULT = {'전', '답', '과수원', '대', '묘지'}

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        payload = self.fetch(self.LAYER, q, size=300)
        feats = self.features(payload)
        if not feats:
            return self.unknown(
                reason='검토 반경 내 지적 정보가 조회되지 않았습니다.',
                action_required='정부24 또는 일사편리에서 토지대장을 직접 확인하십시오.',
            )

        jimok: dict[str, int] = {}
        pnus: list[str] = []
        for f in feats:
            p = f.get('properties') or {}
            code = p.get('jibun', '') or ''
            pnu = p.get('pnu') or p.get('PNU')
            if pnu:
                pnus.append(pnu)
            # jibun 문자열 앞부분에 지목 한글이 붙는 경우가 있어 보조적으로만 사용
            for name in list(self.FAVORABLE) + list(self.DIFFICULT):
                if name in code:
                    jimok[name] = jimok.get(name, 0) + 1
                    break

        parcel_count = len(feats)
        if not jimok:
            return self.item(
                status=Status.UNKNOWN,
                reason=f'검토 반경 내 {parcel_count}개 필지가 조회되었으나 지목을 특정하지 못했습니다.',
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='토지대장으로 지목·소유구분(국공유지 여부)을 확인하십시오.',
                raw={'parcel_count': parcel_count, 'pnu_sample': pnus[:5]},
            )

        hard = {k: v for k, v in jimok.items() if k in self.DIFFICULT}
        if hard:
            return self.item(
                status=Status.CONDITIONAL,
                reason=(
                    f'검토 반경 {parcel_count}개 필지 중 농지·대지 계열 지목이 포함되어 있습니다({hard}). '
                    '해당 필지는 농지전용허가 등 별도 절차가 필요합니다.'
                ),
                difficulty=Difficulty.MEDIUM,
                law='농지법',
                article='제34조(농지의 전용허가·협의)',
                confidence=Confidence.MEDIUM,
                action_required='농지 포함 시 농지전용허가·농지보전부담금 검토가 필요합니다.',
                raw={'jimok': jimok, 'parcel_count': parcel_count},
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=f'검토 반경 {parcel_count}개 필지가 임야·잡종지 계열로 조회되어 산지 인허가 경로로 진행 가능합니다({jimok}).',
            difficulty=Difficulty.LOW,
            law='산지관리법',
            article='제14조·제15조의2',
            confidence=Confidence.MEDIUM,
            action_required='소유구분(국·공유지 여부)과 토지사용승낙 확보 계획을 수립하십시오.',
            raw={'jimok': jimok, 'parcel_count': parcel_count},
        )
