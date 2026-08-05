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


class LawReference(models.Model):
    """풍력 사업에 적용되는 법령 목록"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('법령명', max_length=200)
    category = models.CharField('분류', max_length=50)          # 전기/국토/환경/산림/문화재/군사/지자체
    purpose = models.CharField('풍력 사업에서의 역할', max_length=300, blank=True)
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
    ENERGY_CHOICES = [('WIND', '풍력'), ('SOLAR', '태양광'), ('ALL', '공통')]

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
    note = models.TextField(blank=True)

    class Meta:
        db_table = 'windsite_local_ordinance'
        verbose_name = '지자체 이격거리 조례'
        verbose_name_plural = '지자체 이격거리 조례'
        ordering = ['sido', 'sigungu', 'target']

    def __str__(self):
        return f'{self.sido} {self.sigungu} · {self.get_target_display()} {self.distance_m}m'


class PermitStep(models.Model):
    """육상풍력 인허가 단계 마스터"""
    PHASE_CHOICES = [
        ('DEV', '개발'), ('PERMIT', '인허가'), ('BUILD', '건설'), ('OPS', '운영'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.PositiveIntegerField('순서', db_index=True)
    phase = models.CharField('단계', max_length=10, choices=PHASE_CHOICES)
    name = models.CharField('절차명', max_length=200)
    authority = models.CharField('소관기관', max_length=300)
    law = models.CharField('근거 법령', max_length=200, blank=True)
    article = models.CharField('조문', max_length=200, blank=True)
    statutory_days = models.PositiveIntegerField('법정 처리기간(일)', null=True, blank=True)
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
