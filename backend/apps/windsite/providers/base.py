"""
데이터 레이어 어댑터 공통 기반
---------------------------------------------------------------
각 공공 API/DB 어댑터는 LayerProvider를 상속해 analyze()만 구현한다.

핵심 규약
  - API 키가 없거나 호출이 실패하면 **절대 추측하지 않고** Status.UNKNOWN을 반환한다.
  - 반환 항목에는 반드시 근거 법령과 다음 조치(action_required)를 담는다.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status

logger = logging.getLogger(__name__)


class SiteQuery:
    """검토 대상 지점"""

    def __init__(self, lat: float, lng: float, radius_m: int = 500,
                 address: str = '', capacity_mw: float | None = None):
        self.lat = lat
        self.lng = lng
        self.radius_m = radius_m
        self.address = address
        self.capacity_mw = capacity_mw

    @property
    def area_m2(self) -> float:
        """검토 반경의 원 면적"""
        import math
        return math.pi * (self.radius_m ** 2)

    def __repr__(self) -> str:
        return f'<SiteQuery {self.lat},{self.lng} r={self.radius_m}m>'


class LayerProvider(ABC):
    """데이터 레이어 어댑터"""

    #: 화면 분류
    category: str = ''
    #: 항목명
    item_name: str = ''
    #: 이 어댑터가 필요로 하는 settings 키 목록 (하나라도 비면 UNKNOWN)
    required_settings: tuple[str, ...] = ()
    #: 데이터 출처 표기
    data_source: str = ''
    #: 근거 법령 (기본값 — 판정 시 구체화)
    default_law: str = ''
    default_article: str = ''

    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        return all(getattr(settings, k, '') for k in self.required_settings)

    def missing_settings(self) -> list[str]:
        return [k for k in self.required_settings if not getattr(settings, k, '')]

    # ------------------------------------------------------------------
    def run(self, q: SiteQuery) -> AnalysisItem:
        """예외를 삼키고 항상 AnalysisItem을 반환한다."""
        if self.required_settings and not self.is_configured():
            return self.unknown(
                reason=(
                    f'{self.data_source} 인증키가 설정되지 않아 자동 조회를 수행하지 못했습니다. '
                    f'(.env 미설정: {", ".join(self.missing_settings())})'
                ),
                action_required=f'{self.data_source} 인증키 발급 후 .env에 등록하면 자동 판정됩니다.',
            )
        try:
            return self.analyze(q)
        except Exception as e:                              # noqa: BLE001
            logger.exception('%s 분석 실패', self.item_name)
            return self.unknown(
                reason=f'{self.data_source} 조회 중 오류가 발생했습니다: {type(e).__name__}',
                action_required='네트워크/인증키 상태를 확인한 뒤 재조회하십시오.',
            )

    @abstractmethod
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        """실제 분석 수행"""

    # ------------------------------------------------------------------
    # 결과 생성 헬퍼
    def item(self, status: Status, reason: str, difficulty: Difficulty,
             law: str = '', article: str = '', confidence: Confidence = Confidence.LOW,
             source_url: str = '', action_required: str = '',
             raw: dict[str, Any] | None = None) -> AnalysisItem:
        return AnalysisItem(
            category=self.category,
            item_name=self.item_name,
            status=status,
            reason=reason,
            difficulty=difficulty,
            law=law or self.default_law,
            article=article or self.default_article,
            confidence=confidence,
            source_url=source_url,
            data_source=self.data_source,
            action_required=action_required,
            raw=raw or {},
        )

    def unknown(self, reason: str, action_required: str = '',
                difficulty: Difficulty = Difficulty.MEDIUM) -> AnalysisItem:
        return self.item(
            status=Status.UNKNOWN,
            reason=reason,
            difficulty=difficulty,
            confidence=Confidence.LOW,
            action_required=action_required or '해당 기관에 직접 조회하여 확인이 필요합니다.',
        )

    # ------------------------------------------------------------------
    @staticmethod
    def get(url: str, params: dict[str, Any], timeout: float = 20.0) -> httpx.Response:
        return httpx.get(url, params=params, timeout=timeout)
