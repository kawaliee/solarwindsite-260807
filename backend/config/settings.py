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
