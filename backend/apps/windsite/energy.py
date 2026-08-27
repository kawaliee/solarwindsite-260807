"""
에너지원 프로파일 — 풍력·태양광이 갈리는 지점을 한 곳에 모은다
---------------------------------------------------------------
입지검토의 대부분은 에너지원과 무관하다. 용도지역·산지구분·생태자연도·
국가유산·군사보호구역·필지·행정경계는 무엇을 짓든 같은 규정으로 판정된다.

에너지원에 따라 실제로 달라지는 것은 좁다.

  · **지자체 이격거리 조례** — 같은 조례 안에서 태양광과 풍력에 다른 거리를
    적는다. 삼척시 별표 29는 아예 2열 비교표다(도로 태양광 500m / 풍력 1,000m).
    열을 잘못 고르면 판정이 통째로 뒤집힌다.
  · **자원 평가** — 풍황은 풍력에만 성립한다.
  · **표기** — 보고서 제목·파일명·화면 문구.

그 갈림길을 `if energy == 'SOLAR'`로 코드 곳곳에 흩뿌리면, 다음 에너지원을
붙일 때 어디를 고쳐야 하는지 아무도 모르게 된다. 그래서 갈리는 값만 이
표에 모아 두고, 엔진은 프로파일을 받아 그대로 쓴다.

⚠️ 조례 원문에서 에너지원 구간을 잘라내는 정규식이 여기 있다. 이 값을
   느슨하게 잡으면 **다른 에너지원의 수치를 집어간다**. 삼척시에서 풍력
   주거밀집 2,000m를 태양광 500m로 잘못 읽던 버그가 그 사례다.
"""
from __future__ import annotations

from dataclasses import dataclass

WIND = 'WIND'
SOLAR = 'SOLAR'


@dataclass(frozen=True)
class EnergyProfile:
    code: str
    #: 사람이 읽는 이름 — 화면·보고서·판정 사유 문구에 그대로 쓴다
    label: str
    #: 조례가 규율 대상으로 적는 이름
    facility_label: str

    # ── 조례 원문 판독 ────────────────────────────────────────────
    #: 조문·별표에 이 에너지원 규정이 있는지 보는 낱말
    keywords: tuple[str, ...]
    #: 자치법규 검색에 넣을 낱말 (토큰을 모두 만족하는 조례만 오므로 낱말 하나씩)
    search_queries: tuple[str, ...]
    #: 검색 결과 중 별도 제정 조례로 채택할 제명 낱말
    search_keywords: tuple[str, ...]
    #: 태양광·풍력 2열 비교표 머리글에서 이 에너지원 열을 찾는 낱말
    table_marker: str
    #: 별표 평문에서 이 에너지원 구간이 시작하는 지점 (앞에서부터 차례로 시도)
    block_patterns: tuple[str, ...]
    #: 다음 대항목에서 끊을 때, 같은 에너지원이면 끊지 않기 위한 부정 선견
    block_stop_negative: str

    # ── 자원·표기 ─────────────────────────────────────────────────
    #: 풍황 어댑터와 보고서 '풍력자원' 절을 실을지
    has_wind_resource: bool
    #: 일사량 어댑터를 실을지 — 태양광의 사업성 축
    has_solar_resource: bool
    #: 구역 검토에 **필지별 채색**을 얹을지.
    #: 태양광은 필지가 사업 단위라 '이 구역의 30%가 조건부'만으로는 후보를
    #: 고를 수 없다. 풍력은 배치선이 단위라 필지로 나눌 이유가 없다.
    has_screening: bool
    #: 부지 종류를 못 읽었을 때 산지로 가정할지 (인허가 로드맵 분기)
    assume_forest_site: bool
    #: 발전설비 최고높이(m).
    #:
    #: 항공 공역은 대부분 **하한고도 이상**에만 적용된다. 이 높이가 그 하한보다
    #: 낮으면 평면이 겹쳐도 저촉이 아니다. 200m급 풍력발전기는 대부분의 공역에
    #: 걸리지만, 3~5m인 태양광 모듈은 지표를 포함하는 공역(비행금지구역 등)에만
    #: 걸린다. 이 값을 넘기지 않으면 태양광 필지가 접근관제구역·경계구역 따위에
    #: 걸려 조건부로 뜨는 **오판**이 난다.
    facility_height_m: int
    #: 보고서 표제
    report_title: str
    #: 보고서 PART 2(공간 분석)에 실을 지도 카드의 **차례**.
    #:
    #: 발전원마다 사업 가부를 가르는 항목이 다르다 — 태양광은 농지(용도지역·
    #: 농업진흥구역), 육상풍력은 산지·지형(보전산지·산사태·경사도)이다. 그
    #: 분기를 코드가 아니라 이 표가 쥐게 해서, 발전원을 늘릴 때 보고서 본문을
    #: 뒤지지 않아도 되게 한다. 열쇠는 report_cards.CARD_BUILDERS가 푼다.
    gis_cards: tuple[str, ...]
    #: 부지 1MW를 채우는 데 드는 면적(m²). 없으면 면적으로 용량을 어림하지
    #: 않는다 — 풍력은 호기 수 × 대당 용량으로 세므로 면적 환산이 성립하지
    #: 않는다.
    #:
    #: 이 값이 있어야 **가용면적에서 예상 설비용량을 뽑아** 환경영향평가
    #: 분기에 넣을 수 있다. 종전에는 이 환산이 보고서 본문에만 있어
    #: (report_cards.SOLAR_M2_PER_MW), 화면에 「약 182MW」를 적어 놓고도
    #: 인허가 로드맵은 「설비용량이 입력되지 않아 판단하지 못했습니다」로
    #: 나갔다.
    m2_per_mw: int | None = None

    def mentioned_in(self, text: str) -> bool:
        return any(k in text for k in self.keywords)


WIND_PROFILE = EnergyProfile(
    code=WIND,
    label='풍력',
    facility_label='풍력발전시설',
    keywords=('풍력',),
    search_queries=('풍력', '이격거리', '재생에너지', '신재생에너지'),
    search_keywords=('풍력', '풍력발전', '재생에너지', '신재생에너지', '이격거리'),
    table_marker='풍력',
    block_patterns=(
        r'[가-힣]\.\s*풍력\s*(?:발전시설|에너지)',
        r'풍력\s*(?:발전시설|에너지\s*설비)\s*(?:은|는)',
        r'풍력',
    ),
    block_stop_negative='풍력',
    has_wind_resource=True,
    has_solar_resource=False,
    has_screening=False,
    assume_forest_site=True,
    facility_height_m=200,
    report_title='풍력 입지타당성 검토 보고서',
    # 육상풍력은 산지가 무대다 — 보전산지·산사태·경사도가 배치를 정한다.
    gis_cards=('setback', 'terrain', 'environment', 'heritage', 'grid'),
)

#: 조례 원문은 '태양광'과 '태양에너지'를 섞어 쓴다. 표 머리글은 대개
#: '태양에너지 설비'이고 본문 항목은 '태양광발전시설'인 식이라 둘 다 본다.
SOLAR_PROFILE = EnergyProfile(
    code=SOLAR,
    label='태양광',
    facility_label='태양광발전시설',
    keywords=('태양광', '태양에너지'),
    search_queries=('태양광', '이격거리', '재생에너지', '신재생에너지'),
    search_keywords=('태양광', '태양에너지', '재생에너지', '신재생에너지', '이격거리'),
    table_marker='태양',
    block_patterns=(
        r'[가-힣]\.\s*태양(?:광|에너지)',
        r'태양(?:광|에너지)\s*(?:발전시설|설비)?\s*(?:은|는)',
        r'태양(?:광|에너지)',
    ),
    block_stop_negative='태양',
    has_wind_resource=False,
    has_solar_resource=True,
    has_screening=True,
    assume_forest_site=False,
    # 지상형 태양광 어레이의 통상 최고높이. 추적식·영농형이라도 5m 안팎이다.
    facility_height_m=5,
    report_title='태양광 입지타당성 검토 보고서',
    # 태양광은 농지가 무대다 — 용도지역·농업진흥구역이 사업 경로를 정한다.
    gis_cards=('setback', 'landuse', 'environment', 'heritage', 'grid'),
    #: 지상형 고정식 어레이 기준 어림값. 설계 결과가 아니라 환산이므로,
    #: 이 값에서 나온 용량은 **어디서든 '예상'으로 표기**해야 한다.
    m2_per_mw=10_000,
)

PROFILES = {WIND: WIND_PROFILE, SOLAR: SOLAR_PROFILE}

#: 값이 없거나 모르는 값이면 풍력으로 본다 — 종전 동작을 그대로 유지한다.
DEFAULT = WIND


def profile(energy: str | None = None) -> EnergyProfile:
    return PROFILES.get((energy or '').upper(), WIND_PROFILE)


def normalize(energy: str | None = None) -> str:
    """요청에서 받은 값을 'WIND'|'SOLAR'로 정규화한다."""
    return profile(energy).code
