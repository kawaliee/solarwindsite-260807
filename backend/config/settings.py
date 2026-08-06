"""
재생E AI Agent — Django Settings
"""
import os
from pathlib import Path

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
DATA_GO_KR_KEY = os.environ.get('DATA_GO_KR_KEY', '')

# 환경공간정보서비스(EGIS) — 엔드포인트가 확인되면 URL도 함께 설정
EGIS_API_KEY = os.environ.get('EGIS_API_KEY', '')
EGIS_ECOMAP_URL = os.environ.get('EGIS_ECOMAP_URL', '')
EGIS_PROTECTED_URL = os.environ.get('EGIS_PROTECTED_URL', '')

# 산림청 (산사태위험등급)
FOREST_API_KEY = os.environ.get('FOREST_API_KEY', '')
FOREST_LANDSLIDE_URL = os.environ.get('FOREST_LANDSLIDE_URL', '')

# 국가유산청
HERITAGE_API_KEY = os.environ.get('HERITAGE_API_KEY', '')
HERITAGE_URL = os.environ.get('HERITAGE_URL', '')

# 기상청 ASOS
KMA_API_KEY = os.environ.get('KMA_API_KEY', '')

# 국가법령정보 공동활용 OPEN API (법제처) — 법령·자치법규 원문 대조용.
# 값은 신청 이메일의 ID(@ 앞부분). 비우면 공용 데모 계정 'test'로 동작하나
# 사용량 제한이 있어 운영에는 자체 발급 값을 넣는다. 발급: https://open.law.go.kr
LAW_API_OC = os.environ.get('LAW_API_OC', '')

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
