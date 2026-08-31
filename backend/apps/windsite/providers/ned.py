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

from .. import geo, httpcache, pnu as pnu_codes
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
        # 지적과 NED의 법정동코드 체계가 어긋난 지역이 있다. 확인된 대응표가
        # 있을 때만 바꿔 넘긴다 — 자세한 사연은 windsite/pnu.py 참고.
        pnu = pnu_codes.for_ned(pnu)
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
def site_parcels(q: SiteQuery) -> list[dict]:
    """
    검토 도형과 겹치는 필지 **전부**를 (중심 필지 → 면적 순)으로 돌려준다.

    연속지적 응답은 캐시되므로 CadastralProvider와 중복 호출이 발생하지 않는다.

    ⚠️ **구역 모드에서는 실제 사업구역과 겹치는 필지만 후보로 삼는다.**
    `q.lat/lng/radius_m`는 구역 모드에서 사업구역의 **외접원**일 뿐이다
    (`SiteQuery` 문서 참고). 이 원으로 필지를 조회한 뒤 면적 큰 순으로만
    골랐더니, 원 안에는 들어오지만 사업구역과는 전혀 접하지 않는 큰 필지가
    뽑혔다(실측, 장흥 — 216.9ha 간척 농지의 외접원 반경 1,472m 안에는
    인접 리·마을의 국유지 필지가 여럿 들어와, 6개 후보 중 4개가 실제
    사업구역 밖이었다). 그래서 구역 모드에서는 `q.geom`(사업구역 폴리곤)과
    실제로 겹치는 필지로 먼저 거른 뒤에만 면적순으로 추린다.
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
        return []

    site = None
    if geo.GEO_AVAILABLE:
        try:
            site = geo.point_metric(q.lat, q.lng)
        except geo.GeoUnavailable:
            site = None
    review_geom = q.geom if (site is not None and q.is_area) else None

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
            if g is None:
                continue
            try:
                gm = geo.to_metric(g)
            except Exception:                                   # noqa: BLE001
                continue
            if review_geom is not None and not gm.intersects(review_geom):
                continue                        # 외접원 안이지만 사업구역 밖
            area = geo.area_m2(gm)
            contains_site = gm.contains(site)
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
    return rows


def select_parcels(q: SiteQuery, limit: int = MAX_PARCELS,
                   prefer_jimok: tuple = ()) -> tuple[list[dict], int]:
    """
    NED API로 조사할 필지를 고른다. → (고른 필지, 검토 도형과 겹치는 전체 수)

    NED는 필지당 1회 호출이라 수백 필지를 다 볼 수 없다(MAX_PARCELS 참고).
    무엇을 표본으로 삼을지는 항목마다 다르다.

    `prefer_jimok`을 주면 그 지목을 **먼저** 고르고(그 안에서는 면적 순),
    남은 자리를 나머지 필지로 채운다 — 소유구분은 국공유지가 몰려 있는
    도로·구거·제방을 봐야 하고, 산지구분·토지특성은 부지를 대표하는 큰
    필지를 봐야 한다.
    """
    rows = site_parcels(q)
    if prefer_jimok:
        # 우선 지목을 앞으로 당긴다. 정렬이 안정적이라 각 무리 안에서는
        # site_parcels가 정한 차례(중심 필지 → 면적 순)가 그대로 유지된다.
        rows = sorted(rows, key=lambda r: r['jimok'] not in prefer_jimok)
    return rows[:limit], len(rows)


class ParcelBasedProvider(LayerProvider):
    """필지 단위 NED API를 쓰는 어댑터의 공통 기반"""

    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 국가공간정보 연계(NED)'

    def _parcels(self, q: SiteQuery):
        parcels, total = select_parcels(q)
        return parcels, total

    @staticmethod
    def _code_gap(parcels: list[dict]) -> str:
        """코드 체계 불일치로 0건이 된 것이면 그 사실을 돌려준다."""
        for p in parcels:
            why = pnu_codes.unmapped_reason(p.get('pnu') or '')
            if why:
                return why
        return ''

    @staticmethod
    def _coverage_note(parcels: list[dict], total: int, q: SiteQuery | None = None,
                       how: str = '중심 필지와 면적 상위') -> str:
        if total <= len(parcels):
            return ''
        # 구역 모드에서는 total이 **사업구역과 겹치는** 필지 수다(외접원 안
        # 전체가 아니다 — select_parcels 참고). '반경 내'라고 하면 사업구역
        # 밖 필지까지 센 것처럼 읽힌다.
        scope = '사업구역 내' if (q is not None and q.is_area) else '반경 내'
        # 무엇을 기준으로 골랐는지 밝힌다. 항목마다 고르는 기준이 다른데
        # (select_parcels의 prefer_jimok) 문구가 하나면 표본의 뜻이 달라진다.
        return (f' ※ {scope} {total:,}개 필지 중 {how} {len(parcels)}개만 '
                f'조회했습니다. 나머지 필지는 확인되지 않았습니다.')


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
            gap = self._code_gap(parcels)
            return self.item(
                status=Status.UNKNOWN,
                reason=(gap if gap else
                        ('조회한 필지에서 산지구분(보전산지/준보전산지) 정보가 확인되지 '
                         '않았습니다. 산지가 아닌 필지이거나 자료가 미등재된 경우입니다.'))
                       + self._coverage_note(parcels, total, q),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required=(pnu_codes.unmapped_action('') if gap else
                                 '토지이용계획확인원으로 산지구분을 직접 확인하십시오.'),
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
                    + self._coverage_note(parcels, total, q)),
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
class LandUseZoneProvider(ParcelBasedProvider):
    """
    필지 지역지구 전체 — 토지이용계획확인원에 표기되는 모든 지역·지구·구역.

    개별 레이어를 하나씩 큐레이션하면 반드시 빠뜨리는 것이 생긴다(수변구역·하천망 등).
    이 어댑터는 필지에 걸린 **모든** 지역지구를 그대로 제시해 그 공백을 메운다.

    ⚠️ 산지구분은 ForestClassificationProvider가 별도로 판정하므로 여기서는 제외한다.
    """

    category = '규제/법령'
    item_name = '필지 지역지구(토지이용계획)'
    default_law = '토지이용규제 기본법'
    default_article = '제8조(지역·지구등의 지정)'

    #: 산지구분 — 별도 항목에서 다루므로 중복 표기하지 않는다
    FOREST_CODES = ('UFM100', 'UFM110', 'UFM120', 'UFM200')

    #: 도시계획시설(도로·광장 등)은 수가 많고 풍력 입지 판정과 무관해 요약에서만 다룬다
    FACILITY_PREFIX = ('UQS', 'UQT')

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(
                reason='검토 반경 내 필지를 특정하지 못해 지역지구를 조회할 수 없습니다.')

        zones: dict[str, dict] = {}
        for p in parcels:
            try:
                rows = NedClient.call('getLandUseAttr', 'landUses', p['pnu'])
            except Exception:                                   # noqa: BLE001
                logger.exception('지역지구 조회 실패 pnu=%s', p['pnu'])
                continue
            for r in rows:
                name = (r.get('prposAreaDstrcCodeNm') or '').strip()
                code = (r.get('prposAreaDstrcCode') or '').strip()
                if not name or code in self.FOREST_CODES:
                    continue
                z = zones.setdefault(name, {'name': name, 'code': code,
                                            'conflict': 0, 'touch': 0})
                if r.get('cnflcAtNm') == '접함':
                    z['touch'] += 1
                else:
                    z['conflict'] += 1

        if not zones:
            gap = self._code_gap(parcels)
            return self.unknown(
                reason=gap or '필지 지역지구 정보를 조회하지 못했습니다.',
                action_required=(pnu_codes.unmapped_action('') if gap else
                                 '토지이용계획확인원을 직접 확인하십시오.'),
            )

        facilities = [z for z in zones.values()
                      if z['code'].startswith(self.FACILITY_PREFIX)]
        regs = [z for z in zones.values()
                if not z['code'].startswith(self.FACILITY_PREFIX)]

        status, difficulty, hits = self._resolve(regs)

        names = ', '.join(f'{z["name"]}' for z in
                          sorted(regs, key=lambda z: -z['conflict'])[:14])
        head = (f'조회 필지에 걸린 지역·지구·구역 {len(regs)}종 — {names}'
                f'{" 외" if len(regs) > 14 else ""}.')
        if facilities:
            head += f' (도시계획시설 {len(facilities)}종은 제외)'

        note = ''
        if hits:
            note = (' 이 중 풍력 입지에 영향이 큰 항목 — '
                    + ', '.join(f'{h["name"]}({h["reason"]})' for h in hits[:4]) + '.')

        return self.item(
            status=status,
            reason=head + note + self._coverage_note(parcels, total, q),
            difficulty=difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://www.eum.go.kr',
            action_required=(
                '토지이용계획확인원을 발급받아 각 지역지구의 행위제한을 확인하십시오. '
                '개별 레이어로 조회되지 않는 규제가 여기에 표기될 수 있습니다.'
            ),
            raw={'zones': sorted(regs, key=lambda z: -z['conflict']),
                 'facilities': facilities, 'matched_rules': hits,
                 'parcels': parcels, 'total_parcels': total},
        )

    def _resolve(self, zones: list[dict]):
        """DB 규칙에 걸리는 지역지구가 있으면 가장 불리한 판정을 채택한다."""
        from ..models import RegulationRule

        rules = list(RegulationRule.objects.filter(layer='필지지역지구', is_active=True))
        hits = []
        for z in zones:
            for r in rules:
                if r.condition_key and r.condition_key in z['name']:
                    hits.append({**z, 'status': r.status, 'difficulty': r.difficulty,
                                 'reason': r.reason_template[:80]})
                    break
        if not hits:
            return Status.POSSIBLE, Difficulty.LOW, []
        worst = max(hits, key=lambda h: _DIFF_ORDER.index(h['difficulty']))
        return Status(worst['status']), Difficulty(worst['difficulty']), hits


#: 국유재산법·공유재산법상 **사용허가(대부)를 받아야 하는** 소유구분.
#:
#: ⚠️ '공유지'라는 값만 보면 안 된다. NED가 실제로 돌려주는 값은 지방자치단체
#:    종류별로 갈린다 — 실측(2026-08, 장흥 회진면 도로 8필지)에서 5필지가
#:    **'군유지'**로 왔는데, 종전 코드는 ('국유지','공유지')만 공유재산으로
#:    보아 이 5필지를 사유지로 분류하고 "조회 필지는 모두 사유지"라고까지
#:    적었다. 군유지는 공유재산 및 물품 관리법상 공유재산이라 사용허가·대부
#:    절차가 국유지와 똑같이 필요하다.
PUBLIC_OWNERS = frozenset({'국유지', '공유지', '시유지', '도유지', '군유지', '구유지'})

#: 사용허가 대상이 아닌 소유구분(개인·법인 등). 여기에도 위에도 없는 값은
#: **사유지로 단정하지 않고** 확인 대상으로 남긴다 — 모르는 값을 '문제 없음'
#: 쪽으로 넘기는 것이 이 시스템에서 가장 위험한 실패다.
PRIVATE_OWNERS = frozenset({'개인', '법인', '종중', '종교단체', '외국인', '기타'})

#: 소유구분 표본을 **먼저 쓸 지목.**
#:
#: 실측(장흥 염해농지 태양광, 사업구역 925필지 표본조사)에서 지목과 소유구분의
#: 상관이 거의 1:1로 나왔다.
#:
#:     답    12필지 표본 → 국공유 0%   (전부 '개인')
#:     도로   8필지 표본 → 국공유 100% (국유지 3 · 군유지 5)
#:     구거   8필지 표본 → 국공유 100%
#:     제방   4필지 표본 → 국공유 100%
#:
#: NED는 필지당 1회 호출이라 925필지를 다 볼 수 없다(MAX_PARCELS 참고).
#: 면적 큰 순으로만 고르면 표본이 통째로 '답'에 몰려 국공유지를 못 찾는다.
#: 같은 호출 수로 검출률을 올리려면 이 지목부터 봐야 한다.
PUBLIC_LIKELY_JIMOK = ('구거', '제방', '도로', '하천', '유지', '잡종지')

#: 표본을 무엇으로 골랐는지 밝히는 문구. 「면적 상위」라고만 적으면 표본의
#: 뜻이 달라진다 — 이 항목은 면적이 아니라 지목으로 고른다.
SAMPLE_HOW = '국·공유지가 많은 지목(도로·구거·제방 등)을 우선해'


# ======================================================================
class LandOwnershipProvider(ParcelBasedProvider):
    """토지 소유구분 — 국유지·공유지 여부 (토지사용승낙 확보 경로가 달라진다)"""

    category = '규제/법령'
    item_name = '토지 소유구분(국·공유지)'
    default_law = '국유재산법 · 공유재산 및 물품 관리법'
    default_article = '국유재산법 제30조(사용허가) · 공유재산법 제20조'

    def _parcels(self, q: SiteQuery):
        # 소유구분만은 **국공유지일 가능성이 높은 지목부터** 본다
        # (PUBLIC_LIKELY_JIMOK 참고). 다른 항목(산지구분·토지특성)은 부지를
        # 대표하는 큰 필지를 봐야 하므로 기존 선정을 그대로 쓴다.
        return select_parcels(q, prefer_jimok=PUBLIC_LIKELY_JIMOK)

    @staticmethod
    def _unsampled_scope(q: SiteQuery, sampled: list[dict]) -> str:
        """
        표본 밖에 **같은 성격의 필지가 얼마나 더 있는지** 밝힌다.

        표본 6필지만 열거하면 그 목록이 전수 조사 결과처럼 읽힌다. 국유재산
        사용허가는 인허가 일정을 좌우하는 절차라, 실제로는 수십 필지인데 몇
        곳으로 알고 일정을 짜면 어긋난다. 지목별 필지 수는 연속지적만으로
        세므로 NED 추가 호출이 없다(같은 응답을 캐시에서 다시 쓴다).
        """
        try:
            rows = site_parcels(q)
        except Exception:                                       # noqa: BLE001
            logger.exception('지목 구성 집계 실패')
            return ''
        seen = {r['pnu'] for r in sampled}
        rest = [r for r in rows
                if r['jimok'] in PUBLIC_LIKELY_JIMOK and r['pnu'] not in seen]
        if not rest:
            return ''
        from collections import Counter
        c = Counter(r['jimok'] for r in rest)
        compo = ' · '.join(f'{j} {n}필지' for j, n in c.most_common())
        ha = sum(r['area_m2'] for r in rest) / 1e4
        return (f' 표본 외에도 사업구역 안에 {compo}({ha:,.1f}ha)가 남아 있습니다 — '
                '이들 지목은 통상 국·공유지이므로 **전수 확인이 필요합니다.**')

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        parcels, total = self._parcels(q)
        if not parcels:
            return self.unknown(reason='검토 대상 필지를 특정하지 못했습니다.')

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
                    'area_m2': p['area_m2'], 'jimok': p.get('jimok', ''),
                    'owner': (r.get('posesnSeCodeNm') or '').strip(),
                    'institution': (r.get('nationInsttSeCodeNm') or '').strip(),
                })

        if not rows:
            gap = self._code_gap(parcels)
            return self.unknown(
                reason=gap or '소유구분 정보를 조회하지 못했습니다.',
                action_required=(pnu_codes.unmapped_action('') if gap else
                                 '토지대장·등기사항증명서로 소유구분을 확인하십시오.'),
            )

        public = [r for r in rows if r['owner'] in PUBLIC_OWNERS]
        # 아는 값도 모르는 값도 아닌 소유구분은 **사유지로 넘기지 않는다.**
        unclear = [r for r in rows
                   if r['owner'] not in PUBLIC_OWNERS
                   and r['owner'] not in PRIVATE_OWNERS]
        if unclear:
            logger.warning('소유구분 미분류 값: %s',
                           sorted({r['owner'] or '(빈값)' for r in unclear}))

        if not public:
            unclear_txt = ''
            if unclear:
                vals = ', '.join(sorted({r['owner'] or '구분미상' for r in unclear}))
                unclear_txt = (f' ⚠️ 다만 소유구분 「{vals}」는 국·공유지 여부를 '
                               '가리지 못한 값이라 별도 확인이 필요합니다.')
            return self.item(
                status=Status.UNKNOWN if unclear else Status.POSSIBLE,
                reason=('조회 필지는 모두 사유지로 확인됩니다 — '
                        + ', '.join(f'{r["addr"] or r["pnu"]}({r["owner"] or "구분미상"})'
                                    for r in rows[:4])
                        + '. 토지사용승낙은 개별 소유자와 협의로 진행합니다.'
                        + unclear_txt
                        + self._coverage_note(parcels, total, q, SAMPLE_HOW)),
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
                    + self._unsampled_scope(q, parcels)
                    + self._coverage_note(parcels, total, q, SAMPLE_HOW)),
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
            gap = self._code_gap(parcels)
            return self.unknown(
                reason=gap or '토지특성 정보를 조회하지 못했습니다.',
                action_required=(pnu_codes.unmapped_action('') if gap else
                                 '토지(임야)대장으로 지형·도로접면을 확인하십시오.'),
            )

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
                reason=head + ' ' + ' '.join(notes) + self._coverage_note(parcels, total, q),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                action_required='진입도로 확보 계획과 평균경사도 실측을 사업계획에 반영하십시오.',
                raw={'parcels': rows, 'total_parcels': total},
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=head + ' 진입도로 접면과 지형 조건에 특이사항이 확인되지 않았습니다.'
                   + self._coverage_note(parcels, total, q),
            difficulty=Difficulty.LOW,
            confidence=Confidence.LOW,
            action_required='실제 평균경사도·표고는 측량으로 확인하십시오.',
            raw={'parcels': rows, 'total_parcels': total},
        )
