"""
인허가 로드맵 생성
---------------------------------------------------------------
DB(PermitStep)에 등록된 육상풍력 인허가 절차를 사업 조건(용량·부지 특성)에
맞춰 필터링하고, 각 단계의 해당 여부와 사유를 붙여 반환한다.

⚠️ 육상풍력은 「해상풍력 보급 촉진 및 산업 육성에 관한 특별법」(2026-03-26 시행)의
   적용을 받지 않는다. 따라서 아래 로드맵은 개별 인허가 체계를 전제로 한다.
"""
from __future__ import annotations

from .schemas import Confidence, PermitStepResult


#: conditional_on 키 → 사람이 읽는 설명
FLAG_LABEL = {
    'FARMLAND': '부지에 농지(전·답·과수원)가 포함된 경우',
    'FOREST': '부지에 산지가 포함된 경우',
    'GRASSLAND': '부지에 초지가 포함된 경우',
    'HERITAGE': '인근에 지정 국가유산이 있는 경우',
    'MILITARY': '군사시설 보호구역·비행안전구역에 해당하는 경우',
    'BAEKDUDAEGAN': '백두대간 보호지역에 해당하는 경우',
    'ROAD': '도로 점용이 필요한 경우',
    'EIA': '환경영향평가 대상 규모인 경우',
    'SMALL_EIA': '소규모환경영향평가 대상인 경우',
}


def build_roadmap(capacity_mw: float | None = None,
                  site_flags: set[str] | None = None) -> list[PermitStepResult]:
    from .models import PermitStep

    flags = site_flags or set()
    steps = PermitStep.objects.filter(is_active=True).order_by('order')

    out: list[PermitStepResult] = []
    for s in steps:
        applicable = True
        reason = '모든 육상풍력 사업 공통 절차입니다.'

        if s.conditional_on:
            key = s.conditional_on
            if key in ('EIA', 'SMALL_EIA'):
                applicable, reason = _eia_applicability(key, capacity_mw)
            else:
                applicable = key in flags
                reason = (
                    f'{FLAG_LABEL.get(key, key)}에 해당하여 필요합니다.'
                    if applicable else
                    f'{FLAG_LABEL.get(key, key)}에 해당하지 않아 생략 가능합니다. '
                    f'(부지 확정 후 재확인 권장)'
                )

        out.append(PermitStepResult(
            order=s.order,
            phase=s.get_phase_display(),
            name=s.name,
            authority=s.authority,
            law=s.law,
            article=s.article,
            statutory_days=s.statutory_days,
            depends_on=list(s.depends_on or []),
            applicable=applicable,
            applicability_reason=reason,
            confidence=Confidence(s.confidence),
            source_url=s.source_url,
            note=s.note,
        ))
    return out


def _eia_applicability(key: str, capacity_mw: float | None) -> tuple[bool, str]:
    """
    환경영향평가 / 소규모환경영향평가 대상 여부.

    ⚠️ 환경영향평가법 시행령 별표3·별표4의 풍력 항목 원문을 아직 대조하지 못했다.
       따라서 용량만으로 단정하지 않고, 판단 근거와 함께 '확인 필요'를 명시한다.
    """
    if capacity_mw is None:
        return True, ('설비용량이 입력되지 않아 대상 여부를 판단하지 못했습니다. '
                      '환경영향평가법 시행령 별표3·별표4로 확인이 필요합니다.')

    if key == 'EIA':
        return True, (
            f'설비용량 {capacity_mw:g}MW 기준 환경영향평가 대상 여부는 '
            '환경영향평가법 시행령 별표3(대상사업의 종류·범위)으로 확정해야 합니다. '
            '국내 육상풍력은 상당수가 소규모환경영향평가로 진행된다는 분석이 있으나, '
            '법정 기준선은 원문 확인이 필요합니다.'
        )
    return True, (
        f'설비용량 {capacity_mw:g}MW 기준 소규모환경영향평가 대상 여부는 '
        '환경영향평가법 시행령 별표4로 확정해야 합니다.'
    )


def collect_laws() -> list[dict]:
    from .models import LawReference

    return [
        {
            'name': l.name,
            'category': l.category,
            'purpose': l.purpose,
            'key_articles': l.key_articles,
            'confidence': l.confidence,
            'source_url': l.source_url,
            'verified_at': l.verified_at.isoformat() if l.verified_at else None,
            'note': l.note,
        }
        for l in LawReference.objects.all()
    ]
