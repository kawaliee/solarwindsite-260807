"""
풍력 입지타당성 검토 — 결과 스키마
---------------------------------------------------------------
요청 명세의 JSON 출력 포맷을 그대로 파이썬 dataclass로 정의한다.
모든 판정 결과는 이 구조로 직렬화된다.

설계 원칙
  1) 확인되지 않은 항목은 절대 임의 판정하지 않는다 → status=UNKNOWN
  2) 모든 판정에는 근거(law/article)와 출처(source_url)를 함께 싣는다
  3) 판정 기준의 검증 수준(confidence)을 결과에 노출해, 사용자가
     "확정 판정"과 "참고 판정"을 구분할 수 있게 한다
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """항목별 판정 상태"""
    POSSIBLE = 'POSSIBLE'          # 가능
    CONDITIONAL = 'CONDITIONAL'    # 조건부 가능
    IMPOSSIBLE = 'IMPOSSIBLE'      # 불가
    UNKNOWN = 'UNKNOWN'            # 확인 필요 (데이터 미연동 / 기준 미확정)


class Difficulty(str, Enum):
    """난이도 및 리스크"""
    LOW = 'LOW'
    MEDIUM = 'MEDIUM'
    HIGH = 'HIGH'
    CRITICAL = 'CRITICAL'


class Confidence(str, Enum):
    """
    판정 기준 자체의 검증 수준.
    HIGH   = 법령 원문/공식 고시로 확인
    MEDIUM = 공신력 있는 2차 자료로 교차 확인
    LOW    = 미검증 — 실무 적용 전 원문 대조 필요
    """
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'


# 종합등급 산정 시 상태별 가중 점수 (100점 만점 감점 방식)
STATUS_PENALTY = {
    Status.POSSIBLE: 0,
    Status.CONDITIONAL: 12,
    Status.IMPOSSIBLE: 100,   # 하나라도 있으면 사실상 불가
    Status.UNKNOWN: 6,        # 미확인은 리스크로만 반영
}

DIFFICULTY_PENALTY = {
    Difficulty.LOW: 0,
    Difficulty.MEDIUM: 4,
    Difficulty.HIGH: 9,
    Difficulty.CRITICAL: 18,
}


@dataclass
class Coordinates:
    lat: float
    lng: float


@dataclass
class SiteInfo:
    address: str
    coordinates: Coordinates
    radius_m: int
    total_area_m2: float

    def to_dict(self) -> dict[str, Any]:
        return {
            'address': self.address,
            'coordinates': {'lat': self.coordinates.lat, 'lng': self.coordinates.lng},
            'radius_m': self.radius_m,
            'total_area_m2': round(self.total_area_m2, 1),
        }


@dataclass
class AnalysisItem:
    """분석 항목 1건의 판정 결과"""
    category: str          # 규제/법령 · 환경 · 산림 · 안전/문화재 · 지자체 조례 · 인프라 · 사업성
    item_name: str
    status: Status
    reason: str
    difficulty: Difficulty
    law: str

    # --- 확장 필드 (요청 스키마 + 실무 대응) ---
    article: str = ''                 # 조문 번호
    confidence: Confidence = Confidence.LOW
    source_url: str = ''
    data_source: str = ''             # 어떤 API/DB에서 왔는지
    raw: dict[str, Any] = field(default_factory=dict)   # 원본 응답 (디버깅/근거)
    action_required: str = ''         # 다음에 해야 할 실무 조치
    #: UNKNOWN이 된 이유. 화면에서 '조회 실패'와 '판정 불가'를 구분해 보여준다.
    #:   NO_KEY   인증키 미설정
    #:   FETCH    조회 실패 (네트워크·API 오류) — 재시도하면 판정될 수 있다
    #:   NO_DATA  조회는 됐으나 해당 지점에 자료가 없음
    #:   NO_RULE  자료는 있으나 판정 기준이 없음
    #:   BY_DESIGN 구조적으로 자동 판정 대상이 아님 (예: 풍황 실측)
    #: 빈 문자열이면 UNKNOWN이 아니거나 사유가 지정되지 않은 것이다.
    unknown_reason: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'category': self.category,
            'item_name': self.item_name,
            'status': self.status.value,
            'reason': self.reason,
            'difficulty': self.difficulty.value,
            'law': self.law,
            'article': self.article,
            'confidence': self.confidence.value,
            'source_url': self.source_url,
            'data_source': self.data_source,
            'action_required': self.action_required,
            'unknown_reason': self.unknown_reason,
            # raw는 판정 근거(최근접 거리·필지 면적·조회된 구역명 등)를 담는다.
            # 보고서 재생성과 화면 상세 표시가 이 값에 의존하므로 함께 직렬화한다.
            # (각 어댑터가 상위 N건으로 이미 제한해 보관한다)
            'raw': self.raw,
        }


@dataclass
class OverallFeasibility:
    score: int
    grade: str          # POSSIBLE | CONDITIONAL | IMPOSSIBLE | UNKNOWN
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {'score': self.score, 'grade': self.grade, 'summary': self.summary}


@dataclass
class PermitStepResult:
    """인허가 로드맵 1단계"""
    order: int
    phase: str              # 개발 | 인허가 | 건설 | 운영
    name: str
    authority: str
    law: str
    article: str
    statutory_days: int | None
    depends_on: list[str]
    applicable: bool        # 이 사업에 해당되는지
    applicability_reason: str
    confidence: Confidence
    source_url: str = ''
    note: str = ''
    #: 처리기간을 어디까지 확인했는가 — 'NONE' | 'UNKNOWN' | ''
    #: (PermitStep.statutory_basis 참조)
    statutory_basis: str = ''

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['confidence'] = self.confidence.value
        d['statutory_label'] = self.statutory_label
        return d

    @property
    def statutory_label(self) -> str:
        """
        표에 그대로 찍는 처리기간 문구.

        `-` 하나로 뭉뚱그리지 않는다. 기한이 없는 절차와 아직 확인하지 못한
        절차는 일정 계획에서 전혀 다르게 다뤄야 한다 — 앞은 협의 소요를
        따로 잡아야 하고, 뒤는 먼저 조문을 찾아봐야 한다.
        """
        if self.statutory_days:
            return f'{self.statutory_days}일'
        if self.statutory_basis == 'NONE':
            return '법정기간 없음 (협의 소요)'
        return '◇ 확인 필요'


@dataclass
class EvaluationResult:
    """최종 산출물"""
    site_info: SiteInfo
    overall_feasibility: OverallFeasibility
    analysis_items: list[AnalysisItem]
    permit_roadmap: list[PermitStepResult] = field(default_factory=list)
    applicable_laws: list[dict[str, Any]] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)   # 미연동/미검증 안내
    evaluated_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'site_info': self.site_info.to_dict(),
            'overall_feasibility': self.overall_feasibility.to_dict(),
            'analysis_items': [i.to_dict() for i in self.analysis_items],
            'permit_roadmap': [p.to_dict() for p in self.permit_roadmap],
            'applicable_laws': self.applicable_laws,
            'data_gaps': self.data_gaps,
            'evaluated_at': self.evaluated_at,
        }
