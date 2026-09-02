"""
풍력 입지·인허가 검토 — 데이터 모델
---------------------------------------------------------------
판정 기준(이격거리·등급 등)을 코드에 하드코딩하지 않고 DB로 분리한다.
지자체별 조례 편차와 잦은 개정에 데이터 추가만으로 대응하기 위함이며,
모든 레코드는 근거 조문·출처·검증수준·검증일자를 함께 보관한다.
"""
import uuid

from django.db import models

CONFIDENCE_CHOICES = [
    ('HIGH', 'HIGH — 법령 원문 확인'),
    ('MEDIUM', 'MEDIUM — 2차 자료 교차확인'),
    ('LOW', 'LOW — 미검증, 원문 대조 필요'),
]

DIFFICULTY_CHOICES = [
    ('LOW', 'LOW'), ('MEDIUM', 'MEDIUM'), ('HIGH', 'HIGH'), ('CRITICAL', 'CRITICAL'),
]

STATUS_CHOICES = [
    ('POSSIBLE', '가능'), ('CONDITIONAL', '조건부 가능'),
    ('IMPOSSIBLE', '불가'), ('UNKNOWN', '확인 필요'),
]

#: 에너지원. 규제 레이어·필지·환경 데이터는 무엇을 짓든 같은 규정으로
#: 판정되므로 공유하고, **에너지원마다 실제로 달라지는 것만** 이 값으로
#: 가른다 — 이격거리 조례, 인허가 절차, 적용 법령, 검토 이력.
#: 'ALL'은 양쪽에 공통으로 적용되는 레코드다.
ENERGY_CHOICES = [('WIND', '풍력'), ('SOLAR', '태양광'), ('ALL', '공통')]


class LawReference(models.Model):
    """에너지원별로 사업에 적용되는 법령 목록"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('법령명', max_length=200)
    category = models.CharField('분류', max_length=50)          # 전기/국토/환경/산림/문화재/군사/지자체
    #: 기존 레코드는 전부 육상풍력 기준으로 등록돼 있어 기본값을 WIND로 둔다.
    #: 태양광 법령을 등록할 때 SOLAR로, 양쪽 공통이면 ALL로 넣는다.
    energy_type = models.CharField('에너지원', max_length=10,
                                   choices=ENERGY_CHOICES, default='WIND')
    purpose = models.CharField('사업에서의 역할', max_length=300, blank=True)
    key_articles = models.TextField('주요 조문', blank=True)
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='LOW')
    source_url = models.URLField(blank=True)
    verified_at = models.DateField('원문 확인일', null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = 'windsite_law_reference'
        verbose_name = '관련 법령'
        verbose_name_plural = '관련 법령'
        ordering = ['category', 'name']

    def __str__(self):
        return self.name


class LawArticle(models.Model):
    """
    국가법령정보 OPEN API로 받아온 **조문 원문 캐시**.

    판정 근거를 "어디선가 본 숫자"가 아니라 원문으로 되돌릴 수 있게 보관한다.
    보고서에 조문을 인용할 때도 이 값을 쓴다.
    """
    SOURCE_CHOICES = [('LAW', '법령'), ('ORDINANCE', '자치법규')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField('구분', max_length=12, choices=SOURCE_CHOICES, default='LAW')
    law_name = models.CharField('법령·조례명', max_length=200, db_index=True)
    org = models.CharField('지자체/소관부처', max_length=100, blank=True)
    law_id = models.CharField('법령ID', max_length=30, blank=True)
    mst = models.CharField('일련번호', max_length=30, blank=True)

    article_label = models.CharField('조문 표기', max_length=100, db_index=True)  # '제61조'
    article_no = models.CharField('조번호', max_length=10, blank=True)
    article_sub_no = models.CharField('가지번호', max_length=10, blank=True)
    article_title = models.CharField('조제목', max_length=300, blank=True)
    article_text = models.TextField('조문 원문', blank=True)

    effective_date = models.CharField('시행일자', max_length=10, blank=True)
    promulgated_date = models.CharField('공포일자', max_length=10, blank=True)
    source_url = models.URLField(blank=True)
    #: 공용 데모 계정(OC=test)으로 받은 값인지 — 운영 전 자체 인증값으로 재수집 권장
    via_demo_account = models.BooleanField(default=False)
    fetched_at = models.DateTimeField('수집 일시', auto_now=True)

    class Meta:
        db_table = 'windsite_law_article'
        verbose_name = '법령 조문 원문'
        verbose_name_plural = '법령 조문 원문'
        unique_together = [('law_name', 'article_label')]
        ordering = ['law_name', 'article_no']

    def __str__(self):
        return f'{self.law_name} {self.article_label}'


class RegulationLayer(models.Model):
    """
    조회 대상 공간 레이어 정의.

    레이어 ID·속성명을 코드에 박지 않고 DB로 분리한다. 값은 추측이 아니라
    `scripts/vworld_probe.py`의 **서버 실측 결과**에서 시드된다
    (근거: docs/WINDSITE_VWORLD_LAYERS.md).
    """
    PROVIDER_CHOICES = [
        ('VWORLD', 'V-World 데이터 API'),
        ('LOCAL', '내부 적재 공간데이터(SHP)'),
        ('OSM', 'OpenStreetMap Overpass'),
    ]
    #: 이 레이어가 검토에서 수행하는 역할
    ROLE_CHOICES = [
        ('REGULATION', '규제 저촉 판정'),      # 교차/근접 시 판정 대상
        ('CONTEXT', '참고 정보'),              # 판정하지 않고 정보만 제공
        ('DISTANCE', '이격거리 산정 기초'),    # 건물·도로 등 조례 이격 대상
        ('PARCEL', '필지'),                    # 지적 — 면적 산정
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField('내부 키', max_length=60, unique=True)
    provider = models.CharField('데이터 출처', max_length=10,
                                choices=PROVIDER_CHOICES, default='VWORLD')
    layer_id = models.CharField('레이어 ID', max_length=100, db_index=True)
    title = models.CharField('레이어 제목', max_length=200, blank=True)
    role = models.CharField('역할', max_length=15, choices=ROLE_CHOICES, default='REGULATION')
    category = models.CharField('화면 분류', max_length=50, blank=True)

    #: 구역명이 담긴 속성명 (실측 확인값). 예) 'uname', 'dgm_nm', 'park_name'
    name_field = models.CharField('명칭 속성', max_length=50, blank=True)
    #: 함께 표기할 부가 속성 목록 (실측 확인값)
    extra_fields = models.JSONField('부가 속성', default=list, blank=True)
    geometry_type = models.CharField('지오메트리', max_length=30, blank=True)
    #: 공역 레이어의 **하한고도** 속성명 (예: aismoac의 moa_lbl_3 = '10 000 AMSL').
    #: 값이 있으면 평면 중첩만으로 저촉 판정하지 않고, 발전기 최고높이와 고도를 비교한다.
    #: (항공 공역은 대부분 일정 고도 이상에만 적용돼 평면 중첩만 보면 오탐이 난다)
    altitude_floor_field = models.CharField('하한고도 속성', max_length=50, blank=True)

    #: 피처가 검출됐을 때의 기본 판정 (세부 값별 판정은 RegulationRule이 우선)
    default_status = models.CharField('기본 판정', max_length=15,
                                      choices=STATUS_CHOICES, default='CONDITIONAL')
    default_difficulty = models.CharField('기본 난이도', max_length=10,
                                          choices=DIFFICULTY_CHOICES, default='MEDIUM')
    #: 교차하지 않아도 이 거리 이내면 "근접"으로 보고할 임계값. 0이면 교차만 판정.
    proximity_m = models.PositiveIntegerField('근접 판정 임계(m)', default=0)
    #: 조회 반경 가산 — 레이어 자체 보호범위(예: 역사문화환경 500m)를 반영
    search_margin_m = models.PositiveIntegerField('조회 반경 가산(m)', default=0)

    law = models.CharField('근거 법령', max_length=200, blank=True)
    article = models.CharField('조문', max_length=200, blank=True)
    action_required = models.TextField('필요 조치', blank=True)

    #: 실측 상태 — OK(피처 확인) / NOT_FOUND(표본지점 미해당) / ERROR(API 미제공)
    probe_status = models.CharField('실측 상태', max_length=20, blank=True)
    probe_note = models.CharField('실측 비고', max_length=300, blank=True)

    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='LOW')
    source_url = models.URLField(blank=True)
    verified_at = models.DateField('확인일', null=True, blank=True)
    display_order = models.PositiveIntegerField('표시 순서', default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'windsite_regulation_layer'
        verbose_name = '규제 레이어'
        verbose_name_plural = '규제 레이어'
        ordering = ['display_order', 'code']

    def __str__(self):
        return f'{self.code} ({self.layer_id})'


class RegulationRule(models.Model):
    """
    레이어별 판정 규칙.
    (예) layer='생태자연도', condition_key='1', → CONDITIONAL / CRITICAL
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer = models.CharField('레이어', max_length=100, db_index=True)
    condition_key = models.CharField('조건 키', max_length=100)
    condition_desc = models.CharField('조건 설명', max_length=300)
    status = models.CharField('판정', max_length=15, choices=STATUS_CHOICES)
    difficulty = models.CharField('난이도', max_length=10, choices=DIFFICULTY_CHOICES)
    reason_template = models.TextField('판정 사유')
    law = models.CharField('법령', max_length=200, blank=True)
    article = models.CharField('조문', max_length=200, blank=True)
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='LOW')
    source_url = models.URLField(blank=True)
    verified_at = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'windsite_regulation_rule'
        verbose_name = '입지 판정 규칙'
        verbose_name_plural = '입지 판정 규칙'
        unique_together = [('layer', 'condition_key')]
        ordering = ['layer', 'condition_key']

    def __str__(self):
        return f'{self.layer} / {self.condition_key} → {self.status}'


class LocalOrdinance(models.Model):
    """
    지자체 이격거리 조례.
    ⚠️ 조례는 개정이 잦다. 반드시 verified_at을 갱신하며 관리할 것.
    """
    TARGET_CHOICES = [
        ('RESIDENTIAL', '주거밀집지역'),
        ('QUIET_FACILITY', '정온시설'),
        ('ROAD', '도로'),
        ('RAILWAY', '철도'),
        ('OTHER', '기타'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sido = models.CharField('시·도', max_length=50, db_index=True)
    sigungu = models.CharField('시·군·구', max_length=50, db_index=True)
    energy_type = models.CharField(max_length=10, choices=ENERGY_CHOICES, default='WIND')
    target = models.CharField('이격 대상', max_length=20, choices=TARGET_CHOICES)
    target_detail = models.CharField('대상 상세', max_length=200, blank=True)
    distance_m = models.PositiveIntegerField('이격거리(m)')
    ordinance_name = models.CharField('조례명', max_length=200)
    article = models.CharField('조문', max_length=100, blank=True)
    exemption = models.TextField('완화·예외 규정', blank=True)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default='HIGH')
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='LOW')
    source_url = models.URLField(blank=True)
    verified_at = models.DateField('조례 확인일', null=True, blank=True)
    #: 조례 시행일. 이 날짜 **이전에** 발전사업허가를 받은 사업은 부칙
    #: 경과조치로 종전 기준이 적용될 수 있어, 결론이 통째로 뒤집힌다.
    #: 시행일을 모르면 그 검토 자체가 촉발되지 않으므로 반드시 보관한다.
    effective_date = models.DateField('조례 시행일', null=True, blank=True)
    #: 발전사업 경과조치를 담은 **개정 조례의 시행일**. 소급 여부는 조례 전체의
    #: 최신 시행일이 아니라 이 날짜로 가른다 — 부칙의 "이 조례 시행 전에"에서
    #: '이 조례'는 조례 전문이 아니라 그 개정 조례를 가리키기 때문이다.
    #: (삼척시: 조례 최신 시행일 2025-08-08, 풍력 경과조치는 2025-02-28 개정)
    grandfather_date = models.DateField('경과조치 기준일', null=True, blank=True)
    #: 시행일을 어떻게 정했는지. PROMULGATED는 문언을 못 읽어 공포일로 대신한
    #: 경우라 화면에서 단정하면 안 된다.
    grandfather_basis = models.CharField('기준일 근거', max_length=20, blank=True)
    #: 부칙 원문 중 경과조치·적용례 부분. 적용 여부는 관할 지자체가 판단하므로
    #: 시스템은 판정하지 않고 근거를 그대로 보여주기만 한다.
    addenda = models.TextField('부칙(경과조치·적용례)', blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = 'windsite_local_ordinance'
        verbose_name = '지자체 이격거리 조례'
        verbose_name_plural = '지자체 이격거리 조례'
        ordering = ['sido', 'sigungu', 'target']

    def __str__(self):
        return f'{self.sido} {self.sigungu} · {self.get_target_display()} {self.distance_m}m'


class PermitStep(models.Model):
    """에너지원별 인허가 단계 마스터"""
    PHASE_CHOICES = [
        ('DEV', '개발'), ('PERMIT', '인허가'), ('BUILD', '건설'), ('OPS', '운영'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: 기존 시드는 전부 육상풍력 절차다. 발전사업허가 소관만 해도 풍력과
    #: 태양광이 갈리므로(용량 경계·소관 기관), 섞어 쓰면 로드맵이 틀린다.
    energy_type = models.CharField('에너지원', max_length=10,
                                   choices=ENERGY_CHOICES, default='WIND')
    order = models.PositiveIntegerField('순서', db_index=True)
    phase = models.CharField('단계', max_length=10, choices=PHASE_CHOICES)
    name = models.CharField('절차명', max_length=200)
    authority = models.CharField('소관기관', max_length=300)
    law = models.CharField('근거 법령', max_length=200, blank=True)
    article = models.CharField('조문', max_length=200, blank=True)
    statutory_days = models.PositiveIntegerField('법정 처리기간(일)', null=True, blank=True)
    #: 처리기간을 **어디까지 확인했는가.** `statutory_days`가 비어 있는 까닭이
    #: 두 가지라 값 하나로는 가릴 수 없다 —
    #:
    #:   'NONE'    원문을 찾아봤고, 처리기간 규정이 없다 (협의·심의)
    #:   'UNKNOWN' 아직 원문에서 확인하지 못했다
    #:   ''        `statutory_days`에 값이 있어 따질 것이 없다
    #:
    #: 종전에는 태양광 16개 절차가 전부 None이라 표에 `-`만 찍혔고, 읽는
    #: 사람은 기한이 없는 것인지 조사를 안 한 것인지 알 수 없었다.
    statutory_basis = models.CharField('처리기간 확인 상태', max_length=10, blank=True)
    depends_on = models.JSONField('선행 절차', default=list, blank=True)
    capacity_rule = models.TextField('용량별 소관 구분', blank=True)
    #: 조건부 절차 여부 — 부지 조건에 따라 발생 (예: 농지 포함 시 농지전용)
    conditional_on = models.CharField('발생 조건 키', max_length=100, blank=True)
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='LOW')
    source_url = models.URLField(blank=True)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'windsite_permit_step'
        verbose_name = '인허가 절차'
        verbose_name_plural = '인허가 절차'
        ordering = ['order']

    def __str__(self):
        return f'{self.order}. {self.name}'


class SpatialDataset(models.Model):
    """
    내부 적재 공간데이터셋 1건 (SHP 파일 하나에 대응).

    공개 API가 없거나 API로는 상세 속성을 못 받는 레이어를 SHP로 직접 적재한다.
    (예) 국가유산청 지정유산·현상변경 허용기준
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField('데이터셋 키', max_length=80, unique=True)
    name = models.CharField('데이터셋명', max_length=200)
    category = models.CharField('분류', max_length=50, blank=True)
    source = models.CharField('출처 기관', max_length=200, blank=True)
    source_file = models.CharField('원본 파일', max_length=300, blank=True)
    srs_epsg = models.IntegerField('원본 좌표계 EPSG', null=True, blank=True)
    name_field = models.CharField('명칭 속성', max_length=80, blank=True)
    feature_count = models.PositiveIntegerField('피처 수', default=0)
    loaded_at = models.DateTimeField('적재 일시', null=True, blank=True)
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='HIGH')
    source_url = models.URLField(blank=True)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'windsite_spatial_dataset'
        verbose_name = '공간데이터셋'
        verbose_name_plural = '공간데이터셋'
        ordering = ['category', 'code']

    def __str__(self):
        return f'{self.name} ({self.feature_count:,}건)'


class SpatialFeature(models.Model):
    """
    적재된 공간 피처 1건.

    ⚠️ PostGIS를 쓰지 않으므로 공간 인덱스가 없다. 대신
      1) bbox(min/max) 컬럼으로 후보를 1차 필터하고
      2) shapely로 정확한 거리·교차를 계산한다.
    좌표는 **원본 좌표계 그대로(국내 데이터는 EPSG:5179)** WKB로 보관해
    조회 때마다 재투영하는 비용을 없앤다.
    """
    id = models.BigAutoField(primary_key=True)
    dataset = models.ForeignKey(SpatialDataset, on_delete=models.CASCADE,
                                related_name='features')
    name = models.CharField('명칭', max_length=300, blank=True, db_index=True)
    kind = models.CharField('유형', max_length=100, blank=True, db_index=True)
    sido = models.CharField('시·도', max_length=50, blank=True)
    sigungu = models.CharField('시·군·구', max_length=50, blank=True, db_index=True)
    attrs = models.JSONField('원본 속성', default=dict, blank=True)

    geom_wkb = models.BinaryField('도형(WKB)')
    #: bbox — 원본 좌표계(EPSG:5179) 기준. 1차 후보 필터용.
    min_x = models.FloatField(db_index=True)
    min_y = models.FloatField(db_index=True)
    max_x = models.FloatField(db_index=True)
    max_y = models.FloatField(db_index=True)
    #: 지도 표시용 대표점 (WGS84)
    centroid_lat = models.FloatField(null=True, blank=True)
    centroid_lng = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'windsite_spatial_feature'
        verbose_name = '공간 피처'
        verbose_name_plural = '공간 피처'
        indexes = [
            models.Index(fields=['dataset', 'min_x', 'max_x']),
            models.Index(fields=['dataset', 'min_y', 'max_y']),
        ]

    def __str__(self):
        return f'{self.name or "(무명)"} · {self.dataset_id}'


class SiteEvaluation(models.Model):
    """입지 검토 실행 이력"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    energy_type = models.CharField('에너지원', max_length=10,
                                   choices=ENERGY_CHOICES, default='WIND')
    address = models.CharField(max_length=300, blank=True)
    lat = models.FloatField()
    lng = models.FloatField()
    radius_m = models.PositiveIntegerField(default=500)
    capacity_mw = models.FloatField(null=True, blank=True)
    sido = models.CharField(max_length=50, blank=True)
    sigungu = models.CharField(max_length=50, blank=True)

    score = models.IntegerField(default=0)
    grade = models.CharField(max_length=15, choices=STATUS_CHOICES, default='UNKNOWN')
    summary = models.TextField(blank=True)
    result_json = models.JSONField(default=dict)

    created_by = models.ForeignKey('accounts.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='wind_evaluations')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'windsite_evaluation'
        verbose_name = '입지 검토 이력'
        verbose_name_plural = '입지 검토 이력'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.address or f"{self.lat},{self.lng}"} · {self.grade}'


# ======================================================================
# 검토 프로젝트와 배치안
# ----------------------------------------------------------------------
# 배치선 검토는 한 번 찍고 끝나지 않는다. 이격거리에 걸린 호기를 옮기고
# 다시 돌리기를 반복하며, 몇 달 뒤 "그때 그 배치가 왜 안 됐더라"를 다시
# 들춘다. 좌표를 남겨두지 않으면 그 반복이 매번 처음부터다.
#
# 그래서 사업(프로젝트) 아래에 배치안을 여러 개 둔다. 배치안 하나가
# 지도에 찍은 호기 좌표 한 벌이고, 검토 조건(반경·허가일)과 그때의 요약
# 판정을 함께 들고 있어 다시 불러 그대로 재검토하거나 서로 견줄 수 있다.
#
# 판정 전문(62개 항목)은 담지 않는다. 규제·조례는 개정되므로 몇 달 뒤의
# 옛 판정은 오히려 오해를 부른다. 좌표와 조건만 남기고 판정은 그때그때
# 다시 낸다. 저장된 요약은 '그때는 이랬다'는 기록으로만 쓴다.
# ======================================================================
class SiteProject(models.Model):
    """검토 프로젝트 — 배치안·후보 필지를 묶는 단위 (예: '삼척 천봉풍력')"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: 풍력 배치안과 태양광 후보 필지가 한 목록에 섞이면 무엇을 견주는지
    #: 알 수 없다. 저장 화면도 카테고리별로 갈라져 있으므로 목록도 가른다.
    energy_type = models.CharField('에너지원', max_length=10,
                                   choices=ENERGY_CHOICES, default='WIND')
    name = models.CharField('사업명', max_length=120, unique=True)
    description = models.TextField('설명', blank=True)
    sido = models.CharField('시·도', max_length=50, blank=True)
    sigungu = models.CharField('시·군·구', max_length=50, blank=True)

    created_by = models.ForeignKey('accounts.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='wind_projects')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'windsite_project'
        verbose_name = '검토 프로젝트'
        verbose_name_plural = '검토 프로젝트'
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class SitePlan(models.Model):
    """
    배치안·후보 — 좌표 한 벌과 그때의 검토 조건.

    풍력은 호기 좌표, 태양광은 후보 필지를 고른 좌표가 들어간다. 같은 표를
    쓰되 `mode`로 무엇인지 밝힌다 — 불러올 때 어느 모드로 되돌릴지가 갈린다.
    """
    MODE_CHOICES = [('layout', '배치선'), ('area', '구역'), ('parcel', '필지')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mode = models.CharField('검토 방식', max_length=10,
                            choices=MODE_CHOICES, default='layout')
    project = models.ForeignKey(SiteProject, on_delete=models.CASCADE,
                                related_name='plans')
    name = models.CharField('배치안명', max_length=120)
    note = models.TextField('메모', blank=True)

    #: 검토를 수행한 사람. 계정(`created_by`)과 따로 두는 까닭은 **실제 검토자와
    #: 로그인 계정이 다른 경우가 흔하기** 때문이다 — 대리 입력, 공용 계정,
    #: 외주 검토가 그렇다. 보고서와 이력에 남는 것은 계정이 아니라 이 이름이다.
    reviewer = models.CharField('검토자', max_length=60, blank=True)

    #: [[위도, 경도], …] — 1호기부터의 순서가 곧 연결선 순서다
    turbines = models.JSONField('호기 좌표', default=list)
    turbine_radius_m = models.PositiveIntegerField('발전기 검토반경(m)', default=500)
    corridor_radius_m = models.PositiveIntegerField('연결선 검토반경(m)', default=100)
    capacity_mw = models.FloatField('설비용량(MW)', null=True, blank=True)
    permit_date = models.CharField('발전사업허가일', max_length=10, blank=True)
    sido = models.CharField('시·도', max_length=50, blank=True)
    sigungu = models.CharField('시·군·구', max_length=50, blank=True)

    #: 저장 시점의 요약. 규제는 개정되므로 '그때는 이랬다'는 기록이다.
    summary = models.JSONField('저장 시점 요약', default=dict, blank=True)
    evaluated_at = models.DateTimeField('요약 산출 시점', null=True, blank=True)

    created_by = models.ForeignKey('accounts.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='wind_plans')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'windsite_plan'
        verbose_name = '배치안'
        verbose_name_plural = '배치안'
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'name'],
                                    name='windsite_plan_unique_name'),
        ]

    def __str__(self):
        return f'{self.project.name} · {self.name}'


class RawWindSample(models.Model):
    """
    기상청 재현바람장 수집 결과 (지점·고도·기간 단위).

    「발전사업 세부허가기준 등에 관한 고시」 개정으로 풍력 발전사업허가에
    풍황계측기 대신 재현바람장 자료를 제출하게 되면서 필요해진 자료다.

    ⚠️ **왜 저장하는가** — API가 느리다. 30분 간격으로도 1년치가 약 50분
    걸려(실측) 보고서 생성 중에 받을 수 없다. 미리 받아 두고 보고서는 이
    표를 읽는다.

    ⚠️ **왜 지점 단위인가** — 격자가 촘촘해 2.3km 떨어진 두 지점의 같은 시각
    풍속이 1.0과 1.6m/s로 달랐다(평창 문재풍력 실측). 사업지마다 따로 받아야
    하며, 한 지점 값을 사업지 전체로 말하면 안 된다.
    """

    plan = models.ForeignKey(SitePlan, null=True, blank=True,
                             on_delete=models.CASCADE,
                             related_name='rawwind_samples',
                             verbose_name='배치안')
    #: 조회한 대표 지점. plan이 지워져도 어디를 쟀는지는 남는다.
    lat = models.FloatField('위도')
    lng = models.FloatField('경도')
    height_m = models.PositiveIntegerField('바람 고도(m)')
    start = models.DateTimeField('자료 시작(KST)')
    end = models.DateTimeField('자료 종료(KST)')
    interval_min = models.PositiveIntegerField('자료 간격(분)', default=30)

    #: rawwind.WindStats.to_dict() 그대로. 평균·정온비율·주풍향 등.
    stats = models.JSONField('통계', default=dict)
    #: 받으려던 조각 수 대비 실제로 받은 표본 수를 남긴다. 조각이 일부
    #: 실패해도 나머지는 쓰되, 얼마나 성긴 자료인지 밝혀야 한다.
    samples = models.PositiveIntegerField('유효 표본 수', default=0)
    expected_samples = models.PositiveIntegerField('기대 표본 수', default=0)

    collected_at = models.DateTimeField('수집 시점', auto_now_add=True)

    class Meta:
        db_table = 'windsite_rawwind'
        verbose_name = '재현바람장 표본'
        verbose_name_plural = '재현바람장 표본'
        ordering = ['-collected_at']
        indexes = [models.Index(fields=['lat', 'lng', 'height_m'])]

    def __str__(self):
        return (f'{self.lat:.4f},{self.lng:.4f} {self.height_m}m '
                f'{self.start:%Y-%m-%d}~{self.end:%Y-%m-%d}')

    @property
    def coverage(self) -> float:
        """기대 대비 실제 표본 비율. 1.0이면 빠짐없이 받은 것이다."""
        return (self.samples / self.expected_samples) if self.expected_samples else 0.0
