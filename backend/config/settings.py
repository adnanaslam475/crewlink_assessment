import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(key, default=None):
    return os.environ.get(key, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-secret-change-me")
DEBUG = env("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "crewlink",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database: two roles -----------------------------------------------------
# default : the APP role (non-superuser). Django's RUNTIME uses this. RLS applies.
# admin   : the OWNER/superuser role. migrations + seed use this; bypasses RLS.
# ATOMIC_REQUESTS wraps each request in a transaction, which is what lets us set
# the RLS session var with SET LOCAL (transaction-scoped) per request.
DB_NAME = env("POSTGRES_DB", "crewlink")
DB_HOST = env("DB_HOST", "postgres")
DB_PORT = env("DB_PORT", "5432")
DB_APP_USER = env("DB_APP_USER", "crewlink_app")
DB_APP_PASSWORD = env("DB_APP_PASSWORD", "crewlink_app_pw")
DB_ADMIN_USER = env("POSTGRES_USER", "crewlink_admin")
DB_ADMIN_PASSWORD = env("POSTGRES_PASSWORD", "crewlink_admin_pw")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "USER": DB_APP_USER,
        "PASSWORD": DB_APP_PASSWORD,
        "ATOMIC_REQUESTS": True,
        # Tests migrate as the owner, then exercise RLS through the app connection.
        "TEST": {"MIRROR": "admin"},
    },
    "admin": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "USER": DB_ADMIN_USER,
        "PASSWORD": DB_ADMIN_PASSWORD,
        "TEST": {"DEPENDENCIES": []},
    },
}

# Name of the app role, referenced by the RLS/grant migration + command.
DB_APP_ROLE = DB_APP_USER

# No password validators in dev (seed uses simple logins); note this in the README.
AUTH_PASSWORD_VALIDATORS = []

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

# --- Celery / Redis ----------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER", "0") == "1"
SEND_RECOVERY_INTERVAL_SECONDS = int(env("SEND_RECOVERY_INTERVAL_SECONDS", "30"))
CELERY_BEAT_SCHEDULE = {
    "recover-pending-announcements": {
        "task": "crewlink.tasks.recover_pending_announcements",
        "schedule": SEND_RECOVERY_INTERVAL_SECONDS,
        "options": {"expires": SEND_RECOVERY_INTERVAL_SECONDS},
    }
}

# Fan-out tuning
FANOUT_CHUNK_SIZE = int(env("FANOUT_CHUNK_SIZE", "500"))
STATUS_CACHE_TTL = int(env("STATUS_CACHE_TTL", "3"))

# --- AI feature --------------------------------------------------------------
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")
AI_TIMEOUT_SECONDS = float(env("AI_TIMEOUT_SECONDS", "8"))

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CORS_ALLOW_ALL_ORIGINS = True  # dev only

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
