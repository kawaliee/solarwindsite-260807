"""
V-World 국가공간정보 연계(NED) 어댑터 — 필지 단위 속성
---------------------------------------------------------------
V-World는 WMS/WFS/2D데이터 외에 **국가공간정보포털 연계 API**를 별도로 제공한다.
  https://api.vworld.kr/ned/data/<op>?key=..&domain=..&pnu=..

WFS GetCapabilities(177개 레이어)에는 없지만 이쪽에는 있는 정보가 있다.
특히 **산지구분(보전산지·임업용·공익용·준보전)** 은 육상풍력 인허가의 최대 변수인데
레이어로는 제공되지 않고 이 API에만 있다. (실측 확인, 2026-08)

제공 오퍼레이션 (실측 확인)
  getLandUseAttr         지역지구 전체 — 보전산지/임업용산지/공익용산지/준보전산지,
                         사방지, 산림보호구역, 산림유전자원보호구역 … + 저촉/접함 구분
  getLandCharacteristics 지형지세·이용상황·도로접면(맹지 여부)·공시지가
  getPossessionAttr      소유구분(국유지/공유지/사유지) — 토지사용승낙 검토

⚠️ 이 API는 **필지(PNU) 단위**다. 검토 반경 안에 필지가 수백 개면 그만큼 호출해야 하므로,
   중심 필지 + 면적 상위 필지로 대상을 한정하고 그 사실을 결과에 명시한다.
"""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery
from .cadastral import parse_jimok
from .vworld import VworldClient

logger = logging.getLogger(__name__)

NED_BASE = 'https://api.vworld.kr/ned/data'

#: 필지 단위 API 호출 상한 — 반경 내 전 필지를 조회하면 수백 회가 되어 비현실적이다.
MAX_PARCELS = 6

_DIFF_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']


class NedClient:
    """V-World NED API 호출"""

    @staticmethod
    def call(op: str, root: str, pnu: str, rows: int = 100) -> list[dict]:
        params = {
            'key': settings.VWORLD_API_KEY,
            'domain': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
            'pnu': pnu,
            'format': 'json',
            'numOfRows': str(rows),
            'pageNo': '1',
        }

        def fetch() -> list[dict]:
            res = httpx.get(f'{NED_BASE}/{op}', params=params, timeout=45.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            payload = res.json()
            block = payload.get(root) or {}
            field = block.get('field')
            if isinstance(field, dict):        # 1건일 때 dict로 오는 경우 대비
                return [field]
            return field or []

        return httpcache.get_or_set(f'ned:{op}', {'pnu': pnu, 'rows': rows}, fetch)


# ----------------------------------------------------------------------
def select_parcels(q: SiteQuery, limit: int = MAX_PARCELS) -> tuple[list[dict], int]:
    """
    검토 반경 내 필지 중 **면적이 큰 순서로** 조사 대상을 고른다.
    연속지적 응답은 캐시되므로 CadastralProvider와 중복 호출이 발생하지 않는다.

    returns: (선정 필지 목록, 반경 내 전체 필지 수)
    """
    layer_id = 'lp_pa_cbnd_bubun'
    try:
        from ..models import RegulationLayer
        row = RegulationLayer.objects.filter(code='연속지적', is_active=True).first()
        if row:
            layer_id = row.layer_id
    except Exception:                                           # noqa: BLE001
        pass

    feats, _ = VworldClient.fetch_all(layer_id, q.lat, q.lng, q.radius_m)
    if not feats:
        return [], 0

    site = None
    if geo.GEO_AVAILABLE:
        try:
            site = geo.point_metric(q.lat, q.lng)
        except geo.GeoUnavailable:
            site = None

    rows: list[dict] = []
    for f in feats:
        props = f.get('properties') or {}
        pnu = props.get('pnu')
        if not pnu:
            continue
        area = 0.0
        contains_site = False
        if site is not None:
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is not None:
                try:
                    gm = geo.to_metric(g)
                    area = geo.area_m2(gm)
                    contains_site = gm.contains(site)
                except Exception:                               # noqa: BLE001
                    pass
        rows.append({
            'pnu': pnu,
            'addr': props.get('addr', ''),
            'jibun': props.get('jibun', ''),
            'jimok': parse_jimok(props.get('jibun', '')),
            'area_m2': round(area, 1),
            'is_center': contains_site,
        })

    # 중심 필지를 먼저, 그다음 면적 큰 순
    rows.sort(key=lambda r: (not r['is_center'], -r['area_m2']))
    return rows[:limit], len(rows)


class ParcelBasedProvider(LayerProvider):
    """필지 단위 NED API를 쓰는 어댑터의 공통 기반"""

    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 국가공간정보 연계(NED)'

    def _parcels(self, q: SiteQuery):
        parcels, total = select_parcels(q)
        return parcels, total

    @staticmethod
    def _coverage_note(parcels: list[dict], total: int) -> str:
        if total <= len(parcels):
            return ''
        return (f' ※ 반경 내 {total:,}개 필지 중 중심 필지와 면적 상위 '
                f'{len(parcels)}개만 조회했습니다. 나머지 필지는 확인되지 않았습니다.')


# ======================================================================
class ForestClassificationProvider(ParcelBasedProvider):
    """
    산지구분 — 보전산지(임업용/공익용) · 준보전산지.

    육상풍력은 대부분 산지에 입지하므로 이 구분이 인허가 난이도를 좌우한다.
    산지관리법 제12조는 보전산지에서의 행위를 제한하고, 공익용산지가 임업용산지보다
    제한이 강하다.
    """

    category = '산림'
    item_name = '산지구분(보전산지·준보전산지)'
    default_law = '산지관리법'
    default_article = '제4조(산지의 구분) · 제12조(보전산지에서의 행위제한)'

    #: 판정 규칙이 DB에 없을 때의 폴백. 근거 조문은 원문 대조 대상이다.
    FALLBACK = {
        '공익용산지': (Status.CONDITIONAL, Difficulty.CRITICAL,
                   '공익용산지는 산지관리법 제12조제2항에 따라 허용행위가 임업용산지보다 '
                   '더 좁게 열거되어 있습니다. 풍력발전시설 설치 가능 여부를 산림청·지자체와 '
                   '사전 협의하십시오.'),
        '임업용산지': (Status.CONDITIONAL, Difficulty.HIGH,
                   '임업용산지는 산지관리법 제12조제1항의 열거된 행위만 허용됩니다. '
                   '산지전용허가 또는 산지일시사용허가 가능 여부를 확인해야 합니다.'),
        '보전산지': (Status.CONDITIONAL, Difficulty.HIGH,
                  '보전산지는 행위제한 대상입니다. 임업용·공익용 세부구분을 확인하십시오.'),
        '준보전산지': (Status.POSSIBLE, Difficulty.LOW,
                   '준보전산지는 보전산지에 비해 행위제한이 완화되어 있어 산지전용 인허가 '
                   '경로로 진행하기에 상대적으로 유리합니다.'),
    }
    ORDER = ['공익용산지', '임업용산지', '보전산지', '준보전산지']

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(
                reason='검토 반경 내 필지를 특정하지 못해 산지구분을 조회할 수 없습니다.',
                action_required='좌표를 확인하거나 검토 반경을 넓혀 재시도하십시오.',
            )

        found: dict[str, list[dict]] = {}
        others: set[str] = set()
        for p in parcels:
            try:
                rows = NedClient.call('getLandUseAttr', 'landUses', p['pnu'])
            except Exception:                                   # noqa: BLE001
                logger.exception('산지구분 조회 실패 pnu=%s', p['pnu'])
                continue
            for r in rows:
                name = (r.get('prposAreaDstrcCodeNm') or '').strip()
                if not name:
                    continue
                if name in self.ORDER:
                    found.setdefault(name, []).append({
                        'pnu': p['pnu'], 'addr': p['addr'],
                        'conflict': r.get('cnflcAtNm', ''),
                        'code': r.get('prposAreaDstrcCode', ''),
                    })
                elif any(k in name for k in ('산지', '산림', '사방지')):
                    others.add(name)

        if not found:
            return self.item(
                status=Status.UNKNOWN,
                reason=('조회한 필지에서 산지구분(보전산지/준보전산지) 정보가 확인되지 '
                        '않았습니다. 산지가 아닌 필지이거나 자료가 미등재된 경우입니다.'
                        + self._coverage_note(parcels, total)),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='토지이용계획확인원으로 산지구분을 직접 확인하십시오.',
                raw={'parcels': parcels, 'total_parcels': total},
            )

        worst = next(k for k in self.ORDER if k in found)
        status, difficulty, note = self._resolve(worst)

        # '저촉'과 '접함'을 구분해 제시한다 — 접함은 경계에 닿는 것이라 의미가 다르다
        summary = []
        for name in self.ORDER:
            if name not in found:
                continue
            hits = found[name]
            conflict = sum(1 for h in hits if h['conflict'] == '저촉')
            touch = sum(1 for h in hits if h['conflict'] == '접함')
            summary.append(f'{name}(저촉 {conflict}·접함 {touch})')

        extra = f' 그 밖에 {", ".join(sorted(others))}가 함께 확인됩니다.' if others else ''
        return self.item(
            status=status,
            reason=(f'조회 필지의 산지구분 — {" / ".join(summary)}. {note}{extra}'
                    + self._coverage_note(parcels, total)),
            difficulty=difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://www.vworld.kr',
            action_required=(
                '① 산지전용허가 또는 산지일시사용허가 대상 여부 확인 '
                '② 보전산지 포함 시 대체산림자원조성비·복구비 예치 검토 '
                '③ 평균경사도·표고 기준(산지관리법 시행령 별표4) 충족 여부 측량 확인'
            ),
            raw={'found': found, 'others': sorted(others),
                 'parcels': parcels, 'total_parcels': total},
        )

    def _resolve(self, name: str):
        """판정은 DB(RegulationRule) 우선, 없으면 폴백."""
        from ..models import RegulationRule

        rule = RegulationRule.objects.filter(
            layer='산지구분', condition_key=name, is_active=True).first()
        if rule:
            return (Status(rule.status), Difficulty(rule.difficulty), rule.reason_template)
        return self.FALLBACK.get(
            name, (Status.UNKNOWN, Difficulty.MEDIUM, '판정 기준을 확인하지 못했습니다.'))


# ======================================================================
class LandOwnershipProvider(ParcelBasedProvider):
    """토지 소유구분 — 국유지·공유지 여부 (토지사용승낙 확보 경로가 달라진다)"""

    category = '규제/법령'
    item_name = '토지 소유구분(국·공유지)'
    default_law = '국유재산법 · 공유재산 및 물품 관리법'
    default_article = '국유재산법 제30조(사용허가) · 공유재산법 제20조'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(reason='검토 반경 내 필지를 특정하지 못했습니다.')

        rows: list[dict] = []
        for p in parcels:
            try:
                res = NedClient.call('getPossessionAttr', 'possessions', p['pnu'], rows=5)
            except Exception:                                   # noqa: BLE001
                logger.exception('소유구분 조회 실패 pnu=%s', p['pnu'])
                continue
            for r in res[:1]:
                rows.append({
                    'pnu': p['pnu'], 'addr': p['addr'],
                    'area_m2': p['area_m2'],
                    'owner': (r.get('posesnSeCodeNm') or '').strip(),
                    'institution': (r.get('nationInsttSeCodeNm') or '').strip(),
                })

        if not rows:
            return self.unknown(
                reason='소유구분 정보를 조회하지 못했습니다.',
                action_required='토지대장·등기사항증명서로 소유구분을 확인하십시오.',
            )

        public = [r for r in rows if r['owner'] in ('국유지', '공유지')]
        if not public:
            return self.item(
                status=Status.POSSIBLE,
                reason=('조회 필지는 모두 사유지로 확인됩니다 — '
                        + ', '.join(f'{r["addr"] or r["pnu"]}({r["owner"] or "구분미상"})'
                                    for r in rows[:4])
                        + '. 토지사용승낙은 개별 소유자와 협의로 진행합니다.'
                        + self._coverage_note(parcels, total)),
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                action_required='토지사용승낙서 또는 임대차·매매 계약 확보 계획을 수립하십시오.',
                raw={'parcels': rows, 'total_parcels': total},
            )

        names = ', '.join(
            f'{r["addr"] or r["pnu"]}({r["owner"]}{"·" + r["institution"] if r["institution"] else ""})'
            for r in public[:4])
        return self.item(
            status=Status.CONDITIONAL,
            reason=(f'국·공유지가 포함되어 있습니다 — {names}. 사용허가(대부) 절차가 필요하며 '
                    '소관청 협의에 상당한 기간이 소요됩니다.'
                    + self._coverage_note(parcels, total)),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.MEDIUM,
            action_required=(
                '① 소관청(국유림은 산림청 지방산림청, 그 외는 기재부·지자체) 확인 '
                '② 국유재산 사용허가 또는 대부계약 절차 착수 '
                '③ 국유림의 경우 「국유림의 경영 및 관리에 관한 법률」상 사용허가 병행 검토'
            ),
            raw={'parcels': rows, 'total_parcels': total},
        )


# ======================================================================
class LandCharacteristicsProvider(ParcelBasedProvider):
    """토지특성 — 지형지세·이용상황·도로접면(맹지 여부)"""

    category = '사업성'
    item_name = '토지특성(지형·진입도로)'
    default_law = '해당 없음 (사업성·시공 조건 참고)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(reason='검토 반경 내 필지를 특정하지 못했습니다.')

        rows: list[dict] = []
        for p in parcels:
            try:
                res = NedClient.call('getLandCharacteristics', 'landCharacteristicss',
                                     p['pnu'], rows=3)
            except Exception:                                   # noqa: BLE001
                logger.exception('토지특성 조회 실패 pnu=%s', p['pnu'])
                continue
            for r in res[:1]:
                rows.append({
                    'pnu': p['pnu'], 'addr': p['addr'], 'area_m2': p['area_m2'],
                    'terrain': (r.get('tpgrphHgCodeNm') or '').strip(),
                    'shape': (r.get('tpgrphFrmCodeNm') or '').strip(),
                    'use': (r.get('ladUseSittnNm') or '').strip(),
                    'road': (r.get('roadSideCodeNm') or '').strip(),
                    'zone': (r.get('prposArea1Nm') or '').strip(),
                })

        if not rows:
            return self.unknown(reason='토지특성 정보를 조회하지 못했습니다.')

        landlocked = [r for r in rows if '맹지' in r['road']]
        steep = [r for r in rows if '급경사' in r['terrain'] or '고지' in r['terrain']]

        parts = [f'{r["addr"] or r["pnu"]}: {r["terrain"]}·{r["use"]}·{r["road"]}'
                 for r in rows[:4]]
        head = '조회 필지의 토지특성 — ' + ' / '.join(parts) + '.'

        notes = []
        if landlocked:
            notes.append(f'{len(landlocked)}개 필지가 **맹지**로 도로에 접하지 않아 '
                         '진입도로 확보(사용승낙 또는 도로 개설)가 선행되어야 합니다.')
        if steep:
            notes.append(f'{len(steep)}개 필지가 급경사·고지로 분류되어 있습니다. '
                         '다만 이 값은 공시지가 산정용 지형지세 구분이며 '
                         '**산지관리법 시행령 별표4의 평균경사도 기준과는 다른 지표**이므로, '
                         '실제 평균경사도는 측량으로 확인해야 합니다.')

        if landlocked or steep:
            return self.item(
                status=Status.CONDITIONAL,
                reason=head + ' ' + ' '.join(notes) + self._coverage_note(parcels, total),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='진입도로 확보 계획과 평균경사도 실측을 사업계획에 반영하십시오.',
                raw={'parcels': rows, 'total_parcels': total},
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=head + ' 진입도로 접면과 지형 조건에 특이사항이 확인되지 않았습니다.'
                   + self._coverage_note(parcels, total),
            difficulty=Difficulty.LOW,
            confidence=Confidence.LOW,
            action_required='실제 평균경사도·표고는 측량으로 확인하십시오.',
            raw={'parcels': rows, 'total_parcels': total},
        )
