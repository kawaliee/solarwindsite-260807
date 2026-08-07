"""
재생E AI Agent — Django Settings
"""
import os
from pathlib import Path
from urllib.parse import unquote

import dj_database_url
from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).resolve().parent.parent.parent / '.env')

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-secret-key')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = ['*']

# ───── Applications ─────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'corsheaders',
    'django_filters',
    # Local apps
    'apps.accounts',
    'apps.workspaces',
    'apps.documents',
    'apps.chat',
    'apps.contracts',
    'apps.windsite',
    'apps.factsheets',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ───── Database ─────
DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    f"postgres://{os.environ.get('POSTGRES_USER', 're_user')}:{os.environ.get('POSTGRES_PASSWORD', 're_pass')}@localhost:5432/{os.environ.get('POSTGRES_DB', 're_agent')}"
)
DATABASES = {
    'default': dj_database_url.parse(DATABASE_URL)
}

# ───── Auth ─────
AUTH_USER_MODEL = 'accounts.User'

# ───── REST Framework ─────
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',  # PoC 단계: 인증 미적용
    ],
}

# ───── CORS ─────
CORS_ALLOW_ALL_ORIGINS = True  # 개발 환경

# ───── Celery ─────
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# ───── Cache ─────
# 풍력 입지검토는 한 지점당 수십 건의 외부 API를 호출한다. 같은 지점을 다시 보거나
# 보고서를 만들 때 동일 호출이 반복되므로 응답을 캐시해 지연과 사용량 제한을 줄인다.
_REDIS_URL = os.environ.get('REDIS_URL', '')
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': _REDIS_URL,
    } if _REDIS_URL else {
        # Redis가 없으면 프로세스 로컬 캐시로 동작 (개발/단독 실행용)
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'windsite-local',
    }
}
#: 외부 공간정보 응답 캐시 수명(초). 규제 레이어는 자주 바뀌지 않는다.
WINDSITE_CACHE_TTL = int(os.environ.get('WINDSITE_CACHE_TTL', 60 * 60 * 24))

# ───── Qdrant ─────
QDRANT_URL = os.environ.get('QDRANT_URL', 'http://localhost:6333')
QDRANT_COLLECTION = 're_documents'

# ───── Embedding ─────
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'BAAI/bge-m3')
EMBEDDING_DEVICE = os.environ.get('EMBEDDING_DEVICE', 'api')  # api | cpu | cuda
EMBEDDING_API_BASE = os.environ.get('EMBEDDING_API_BASE', '')
EMBEDDING_API_KEY = os.environ.get('EMBEDDING_API_KEY', '')

# ───── LLM ─────
LLM_API_BASE = os.environ.get('LLM_API_BASE', '')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', 'mock')

# ───── 풍력 입지·인허가 검토 (windsite) ─────
# 각 공공 API 인증키. 비어 있으면 해당 항목은 자동으로 UNKNOWN(확인 필요) 판정된다.
# 발급처는 docs/WINDSITE_API_KEYS.md 참고.
VWORLD_API_KEY = os.environ.get('VWORLD_API_KEY', '')
VWORLD_DOMAIN = os.environ.get('VWORLD_DOMAIN', 'localhost')

# 공공데이터포털(data.go.kr) 일반 인증키
#
# 포털은 인증키를 Encoding(퍼센트 인코딩)/Decoding 두 형태로 보여준다. HTTP 클라이언트가
# 쿼리스트링을 만들 때 다시 인코딩하므로, Encoding 값을 그대로 쓰면 '%2B'가 '%252B'가 되어
# SERVICE_KEY_IS_NOT_REGISTERED_ERROR(403)가 난다. 어느 쪽을 넣어도 동작하도록
# '%'가 포함돼 있으면 여기서 한 번 디코딩해 Decoding 형태로 통일한다.
_RAW_DATA_GO_KR_KEY = os.environ.get('DATA_GO_KR_KEY', '')
DATA_GO_KR_KEY = (
    unquote(_RAW_DATA_GO_KR_KEY) if '%' in _RAW_DATA_GO_KR_KEY else _RAW_DATA_GO_KR_KEY
)

# 국립생태원 생태자연도 (공공데이터포털 B553084) — 생태·자연도 등급 조회.
# 인증키는 별도 지정이 없으면 DATA_GO_KR_KEY를 그대로 쓴다.
# ⚠️ 반드시 **Decoding(일반 인증키)** 값을 넣을 것. Encoding 값을 넣으면
#    HTTP 클라이언트가 다시 인코딩해 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 난다.
_RAW_ECO_API_KEY = os.environ.get('ECO_API_KEY', '')
ECO_API_KEY = (
    (unquote(_RAW_ECO_API_KEY) if '%' in _RAW_ECO_API_KEY else _RAW_ECO_API_KEY)
    or DATA_GO_KR_KEY
)

# 토지이용규제 행위제한정보 (국토교통부 / 토지이음 연계, 1613000)
#   DTarLandUseInfo  — 지역지구코드(ucode)별 토지이용행위 가능여부
#   DTsearchLunCd    — 토지이용행위명 → 코드 검색
# ⚠️ 이 API는 "이 필지가 무슨 지역지구인가"를 알려주지 않는다. 지역지구코드를 **입력**받아
#    행위 가능여부를 돌려준다. 지역지구 판별은 V-World 레이어가 담당한다.
LANDUSE_ACT_API_BASE = os.environ.get(
    'LANDUSE_ACT_API_BASE', 'https://apis.data.go.kr/1613000/arLandUseInfoService')
ECO_API_BASE = os.environ.get(
    'ECO_API_BASE', 'https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService')

# 환경공간정보서비스(EGIS) — 생태자연도를 위 공공데이터포털 API로 대체했으므로
# 아래는 EGIS가 별도 REST를 공개할 경우를 위한 예비 설정이다.
EGIS_API_KEY = os.environ.get('EGIS_API_KEY', '')
EGIS_ECOMAP_URL = os.environ.get('EGIS_ECOMAP_URL', '')
EGIS_PROTECTED_URL = os.environ.get('EGIS_PROTECTED_URL', '')

# 산사태위험지도 — 생활안전지도(safemap.go.kr) 오픈API (제공기관 산림청).
# 공공데이터포털과 별개 포털이라 인증키도 별개다. DATA_GO_KR_KEY를 쓰면 안 된다.
# 데이터 포맷이 WMS(지도 이미지)이므로 GetFeatureInfo로 지점 등급을 조회한다.
FOREST_API_KEY = os.environ.get('FOREST_API_KEY', '')
FOREST_LANDSLIDE_URL = os.environ.get(
    'FOREST_LANDSLIDE_URL', 'https://safemap.go.kr/openapi2/IF_0046_WMS')
# 산사태위험지도 WMS 레이어명 — 키 발급 후 GetCapabilities로 실측해 확정한다.
FOREST_LANDSLIDE_LAYER = os.environ.get('FOREST_LANDSLIDE_LAYER', '')

# 국가유산청
HERITAGE_API_KEY = os.environ.get('HERITAGE_API_KEY', '')
HERITAGE_URL = os.environ.get('HERITAGE_URL', '')

# 국가유산 공간정보 WMS (gis-heritage.go.kr) — 인증키·도메인 검증이 없음을 실측 확인.
# SHP으로 배포되지 않는 문화유적분포지도·국가유산조사구역을 이 경로로 조회한다.
HERITAGE_WMS_URL = os.environ.get(
    'HERITAGE_WMS_URL', 'https://gis-heritage.go.kr/checkKey.do')
HERITAGE_WMS_DOMAIN = os.environ.get(
    'HERITAGE_WMS_DOMAIN', 'https://gis-heritage.go.kr/')

# 기상청 ASOS (공공데이터포털) — 종관기상관측 일자료·시간자료.
# 값은 DATA_GO_KR_KEY와 동일한 포털 인증키다.
_RAW_KMA_API_KEY = os.environ.get('KMA_API_KEY', '')
KMA_API_KEY = (
    (unquote(_RAW_KMA_API_KEY) if '%' in _RAW_KMA_API_KEY else _RAW_KMA_API_KEY)
    or DATA_GO_KR_KEY
)

# 기상청 API허브(apihub.kma.go.kr) — **관측지점 목록(위경도·표고)** 조회용.
# 공공데이터포털과 별개 사이트라 인증키도 별개다.
#   발급: https://apihub.kma.go.kr → 회원가입 → 마이페이지 → 인증키
# 이 값이 없으면 최근접 관측소 자동 선정이 불가해 지점번호를 수동 지정해야 한다.
KMA_APIHUB_KEY = os.environ.get('KMA_APIHUB_KEY', '')
KMA_APIHUB_BASE = os.environ.get('KMA_APIHUB_BASE', 'https://apihub.kma.go.kr/api/typ01/url')

# 국가법령정보 공동활용 OPEN API (법제처) — 법령·자치법규 원문 대조용.
# 값은 신청 이메일의 ID(@ 앞부분). 비우면 공용 데모 계정 'test'로 동작하나
# 사용량 제한이 있어 운영에는 자체 발급 값을 넣는다. 발급: https://open.law.go.kr
LAW_API_OC = os.environ.get('LAW_API_OC', '')

# 한전 전력데이터 개방 포털 — 분산전원 연계정보(계통 여유용량).
# 공공데이터포털과 별개 포털이라 인증키도 별개다(40자리).
#   발급: https://bigdata.kepco.co.kr → 데이터공개 → OPEN API → 인증키 신청
# ⚠️ 호출 간격 제한이 있어 반드시 캐시를 경유해 호출한다.
KEPCO_API_KEY = os.environ.get('KEPCO_API_KEY', '')
KEPCO_GRID_URL = os.environ.get(
    'KEPCO_GRID_URL', 'https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do')

# OpenStreetMap Overpass — 변전소·송전선로·정온시설 탐색 (인증키 불필요).
# 공개 인스턴스는 사용량 제한이 있으므로 필요 시 자체 인스턴스 URL로 교체한다.
OVERPASS_URL = os.environ.get('OVERPASS_URL', 'https://overpass-api.de/api/interpreter')

# ───── File Storage ─────
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'

STATIC_URL = 'static/'

# ───── Internationalization ─────
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
