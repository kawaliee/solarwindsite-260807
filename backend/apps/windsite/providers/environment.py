"""
환경 규제 어댑터 — 생태자연도 / 환경 보호구역
---------------------------------------------------------------
환경공간정보서비스(EGIS, egis.me.go.kr)가 원 데이터를 보유하나,
2026-08 기준 좌표 기반 조회용 공개 REST 엔드포인트가 확인되지 않았다.
따라서 엔드포인트를 설정값으로 두고, 미설정 시 UNKNOWN을 반환한다.

※ 판정 기준 주의
   「육상풍력 개발사업 환경성평가 지침」(환경부)의 등급별 회피/조건부 문구는
   원문(hwp) 대조를 완료하지 못했다. 본 어댑터는 등급 자체만 확인하고,
   판정 문구는 DB(RegulationRule)에서 가져와 검증 상태를 함께 표기한다.
"""
from __future__ import annotations

from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery


class EcoNatureMapProvider(LayerProvider):
    """3. 생태자연도"""

    category = '환경'
    item_name = '생태자연도'
    data_source = '환경공간정보서비스(EGIS)'
    required_settings = ('EGIS_API_KEY', 'EGIS_ECOMAP_URL')
    default_law = '자연환경보전법'
    default_article = '제34조(생태·자연도의 작성·활용)'

    #: 등급별 기본 판정 (DB 규칙이 없을 때의 폴백)
    GRADE_RULES = {
        '1': (Status.CONDITIONAL, Difficulty.CRITICAL,
              '생태·자연도 1등급 권역은 개발사업 입지를 원칙적으로 지양합니다. '
              '불가피성이 인정되는 예외 사유에 해당하는지 환경청과 사전 협의가 필요합니다.'),
        '2': (Status.CONDITIONAL, Difficulty.HIGH,
              '생태·자연도 2등급 권역이 포함되어 환경영향평가(또는 소규모환경영향평가) 협의 과정에서 '
              '보전·저감 방안 제시가 요구됩니다.'),
        '3': (Status.POSSIBLE, Difficulty.LOW,
              '생태·자연도 3등급 권역으로, 개발과 보전의 조화가 가능한 지역으로 분류됩니다.'),
        '별도': (Status.CONDITIONAL, Difficulty.HIGH,
               '별도관리지역(자연공원·습지보호지역·백두대간보호지역 등)이 포함되어 '
               '해당 개별법의 행위제한이 우선 적용됩니다.'),
    }

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        res = self.get(settings.EGIS_ECOMAP_URL, {
            'serviceKey': settings.EGIS_API_KEY,
            'lat': q.lat, 'lng': q.lng, 'buffer': q.radius_m, 'type': 'json',
        })
        res.raise_for_status()
        data = res.json()

        grades = _extract_grades(data)
        if not grades:
            return self.unknown(
                reason='생태자연도 등급을 응답에서 판별하지 못했습니다.',
                action_required='환경공간정보서비스(egis.me.go.kr)에서 대상지 등급을 직접 확인하십시오.',
            )

        worst = min(grades, key=lambda g: {'1': 0, '별도': 1, '2': 2, '3': 3}.get(g, 9))
        status, diff, reason = self.GRADE_RULES.get(
            worst, (Status.UNKNOWN, Difficulty.MEDIUM, '등급 판정 기준을 확인하지 못했습니다.'))

        return self.item(
            status=status,
            reason=f'검토 반경 내 생태·자연도 {"·".join(sorted(grades))}등급 권역 확인. {reason}',
            difficulty=diff,
            confidence=Confidence.MEDIUM,
            source_url='https://egis.me.go.kr',
            action_required='유역(지방)환경청과 사전 환경성 협의를 진행하십시오.',
            raw={'grades': sorted(grades)},
        )


class ProtectedAreaProvider(LayerProvider):
    """4. 환경 보호구역 (습지·자연공원·백두대간 등)"""

    category = '환경'
    item_name = '환경 보호구역 저촉'
    data_source = '환경공간정보서비스(EGIS)'
    required_settings = ('EGIS_API_KEY', 'EGIS_PROTECTED_URL')
    default_law = '자연공원법 · 습지보전법 · 백두대간 보호에 관한 법률'
    default_article = '자연공원법 제23조 / 습지보전법 제13조 / 백두대간법 제7조'

    #: 구역 키워드 → (상태, 난이도, 법령, 조문)
    AREA_RULES: dict[str, tuple[Status, Difficulty, str, str]] = {
        '국립공원': (Status.CONDITIONAL, Difficulty.CRITICAL, '자연공원법', '제23조'),
        '도립공원': (Status.CONDITIONAL, Difficulty.CRITICAL, '자연공원법', '제23조'),
        '군립공원': (Status.CONDITIONAL, Difficulty.CRITICAL, '자연공원법', '제23조'),
        '습지보호': (Status.CONDITIONAL, Difficulty.CRITICAL, '습지보전법', '제13조'),
        '생태경관': (Status.CONDITIONAL, Difficulty.CRITICAL, '자연환경보전법', '제15조'),
        '야생생물': (Status.CONDITIONAL, Difficulty.HIGH, '야생생물 보호 및 관리에 관한 법률', '보호구역 행위제한 조항'),
        '백두대간': (Status.CONDITIONAL, Difficulty.CRITICAL, '백두대간 보호에 관한 법률', '제7조'),
    }

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        res = self.get(settings.EGIS_PROTECTED_URL, {
            'serviceKey': settings.EGIS_API_KEY,
            'lat': q.lat, 'lng': q.lng, 'buffer': q.radius_m, 'type': 'json',
        })
        res.raise_for_status()
        names = _extract_names(res.json())

        if not names:
            return self.item(
                status=Status.POSSIBLE,
                reason='검토 반경 내 자연공원·습지보호지역·생태경관보전지역 등 환경 보호구역이 조회되지 않았습니다.',
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://egis.me.go.kr',
                raw={'areas': []},
            )

        hits = []
        for n in names:
            for kw, rule in self.AREA_RULES.items():
                if kw in n:
                    hits.append((n, *rule))
                    break
        if not hits:
            return self.item(
                status=Status.CONDITIONAL,
                reason=f'검토 반경 내 보호구역이 조회되었습니다: {", ".join(names[:6])}. 개별 확인이 필요합니다.',
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                raw={'areas': names},
            )

        worst = max(hits, key=lambda h: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(h[2].value))
        return self.item(
            status=worst[1],
            reason=f'검토 반경이 보호구역과 저촉됩니다: {", ".join(h[0] for h in hits)}.',
            difficulty=worst[2],
            law=worst[3],
            article=worst[4],
            confidence=Confidence.MEDIUM,
            source_url='https://egis.me.go.kr',
            action_required='해당 보호구역 관리청의 행위허가 가능 여부를 사전 확인하십시오.',
            raw={'areas': names},
        )


# ----------------------------------------------------------------------
def _extract_grades(data: dict) -> set[str]:
    """응답 구조가 기관마다 달라 재귀적으로 등급 문자열을 탐색한다."""
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif 'etc' in str(k).lower() or '등급' in str(k) or 'grade' in str(k).lower():
                    s = str(v)
                    for g in ('1', '2', '3'):
                        if g in s:
                            found.add(g)
                    if '별도' in s:
                        found.add('별도')
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(data)
    return found


def _extract_names(data: dict) -> list[str]:
    names: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif isinstance(v, str) and ('nm' in str(k).lower() or 'name' in str(k).lower()):
                    if v.strip():
                        names.append(v.strip())
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(data)
    return sorted(set(names))
