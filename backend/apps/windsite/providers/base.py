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
import math
from abc import ABC, abstractmethod
from typing import Any

import httpx
from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status

logger = logging.getLogger(__name__)


class SiteQuery:
    """
    검토 대상 — 점+반경 또는 사업구역 폴리곤.

    대규모 육상풍력은 사업구역이 면이라 점 하나로 표현되지 않는다. 그렇다고
    어댑터 20여 종을 한꺼번에 갈아엎을 수는 없으므로, **두 모드를 한 객체로
    감싸고 기존 인터페이스를 그대로 유지**한다.

      · 점 모드   geom = 반경 원
      · 구역 모드 geom = 사업구역 폴리곤, lat/lng·radius_m는 그 외접원

    덕분에 아직 손대지 않은 어댑터도 구역 모드에서 '구역을 덮는 원'을 조회해
    말이 되는 결과를 낸다. 다만 판정 문구가 '검토 반경'이라 실제보다 넓게
    읽히므로, 면 판정이 중요한 어댑터부터 is_area로 분기해 옮겨간다.
    """

    def __init__(self, lat: float, lng: float, radius_m: int = 500,
                 address: str = '', capacity_mw: float | None = None,
                 area_ring: list | None = None):
        self.address = address
        self.capacity_mw = capacity_mw
        self.area_ring = area_ring or None

        self._geom = None
        if self.area_ring:
            from .. import geo
            self._geom = geo.polygon_metric(self.area_ring)
            if self._geom is None:
                raise ValueError('사업구역 폴리곤이 유효하지 않습니다 (꼭짓점 3개 이상 필요).')
            # 구역 모드에서도 점 기반 어댑터가 동작하도록 외접원을 대표값으로 둔다
            self.lat, self.lng, self.radius_m = geo.circumscribed(self._geom)
        else:
            self.lat = lat
            self.lng = lng
            self.radius_m = radius_m

    # ------------------------------------------------------------------
    @property
    def is_area(self) -> bool:
        return self._geom is not None

    @property
    def geom(self):
        """검토 도형(EPSG:5179). 점 모드면 반경 원을 만들어 돌려준다."""
        if self._geom is None:
            from .. import geo
            self._geom = geo.point_metric(self.lat, self.lng).buffer(self.radius_m)
        return self._geom

    @property
    def bounds(self) -> tuple:
        return self.geom.bounds

    @property
    def area_m2(self) -> float:
        """검토 면적. 구역이면 실제 폴리곤 면적, 점이면 반경 원 면적."""
        if self._geom is not None:
            return float(self._geom.area)
        return math.pi * (self.radius_m ** 2)

    def __repr__(self) -> str:
        if self.is_area:
            return f'<SiteQuery area {self.area_m2 / 1e6:.2f}km² r_out={self.radius_m}m>'
        return f'<SiteQuery {self.lat},{self.lng} r={self.radius_m}m>'


class LayerProvider(ABC):
    """데이터 레이어 어댑터"""

    #: 화면 분류
    category: str = ''
    #: 항목명
    item_name: str = ''
    #: 이 어댑터가 필요로 하는 settings 키 목록 (하나라도 비면 조회 자체를 하지 않고 UNKNOWN)
    required_settings: tuple[str, ...] = ()
    #: 있으면 판정이 풍부해지지만 없어도 동작하는 키.
    #: 예) 계통 연계는 KEPCO 키가 없어도 OSM으로 위치는 찾는다. 이런 키를
    #:     required_settings에 넣으면 키 하나 때문에 항목 전체가 죽는다.
    optional_settings: tuple[str, ...] = ()
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

    def configured_settings(self) -> list[str]:
        """실제로 값이 채워져 있는 키 (필수+선택) — 연동 현황 화면 표기용"""
        return [k for k in (*self.required_settings, *self.optional_settings)
                if getattr(settings, k, '')]

    def missing_optional(self) -> list[str]:
        return [k for k in self.optional_settings if not getattr(settings, k, '')]

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
                why='NO_KEY',
            )
        try:
            return self.analyze(q)
        except Exception as e:                              # noqa: BLE001
            logger.exception('%s 분석 실패', self.item_name)
            return self.unknown(
                reason=f'{self.data_source} 조회 중 오류가 발생했습니다: {type(e).__name__}',
                action_required='네트워크/인증키 상태를 확인한 뒤 재조회하십시오.',
                why='FETCH',
            )

    @abstractmethod
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        """실제 분석 수행"""

    # ------------------------------------------------------------------
    # 결과 생성 헬퍼
    def item(self, status: Status, reason: str, difficulty: Difficulty,
             law: str = '', article: str = '', confidence: Confidence = Confidence.LOW,
             source_url: str = '', action_required: str = '',
             raw: dict[str, Any] | None = None,
             unknown_reason: str = '') -> AnalysisItem:
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
            unknown_reason=unknown_reason if status is Status.UNKNOWN else '',
        )

    def unknown(self, reason: str, action_required: str = '',
                difficulty: Difficulty = Difficulty.MEDIUM,
                why: str = 'NO_DATA') -> AnalysisItem:
        """
        판정 보류.

        why에 사유를 넣는다. 종전에는 모든 UNKNOWN이 똑같이 보여서
        '일시적 조회 실패'와 '원래 자동 판정이 안 되는 항목'이 화면에서
        구분되지 않았다. 앞의 것은 재시도하면 되고 뒤의 것은 재시도해도 소용없다.
        """
        return self.item(
            status=Status.UNKNOWN,
            reason=reason,
            difficulty=difficulty,
            confidence=Confidence.LOW,
            action_required=action_required or '해당 기관에 직접 조회하여 확인이 필요합니다.',
            unknown_reason=why,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def get(url: str, params: dict[str, Any], timeout: float = 20.0) -> httpx.Response:
        return httpx.get(url, params=params, timeout=timeout)
