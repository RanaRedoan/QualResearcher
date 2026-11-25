"""Django settings for QualResearcher (Bangla interview transcription SaaS).

Every deploy-specific value comes from environment variables (see .env.example).
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# APP_MODE=local   → single PC run from start.bat in a browser: SQLite, background thread (default)
# APP_MODE=desktop → the installed Windows app (desktop_app.py): like local, plus no login,
#                    a Gemini-key setup screen and an app-style window
# APP_MODE=saas    → public website: Postgres, Celery + Redis, credits, bKash, approval
APP_MODE = os.getenv("APP_MODE", "local").strip().lower()
LOCAL_MODE = APP_MODE != "saas"
DESKTOP = APP_MODE == "desktop"

# Where the database and uploaded audio live. On Windows (local mode) this is kept
# OUTSIDE the project folder, in %LOCALAPPDATA%\Kothon, so that a project folder inside
# Google Drive / OneDrive never syncs participant audio to the cloud or corrupts the database.
if os.getenv("KOTHON_DATA_DIR"):
    DATA_DIR = Path(os.getenv("KOTHON_DATA_DIR"))
elif DESKTOP and os.getenv("LOCALAPPDATA"):
    DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "QualResearcher"
elif DESKTOP:
    DATA_DIR = Path.home() / ".qualresearcher"
elif LOCAL_MODE and os.name == "nt" and os.getenv("LOCALAPPDATA"):
    DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "Kothon"
else:
    DATA_DIR = BASE_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _desktop_secret():
    """Each installed copy gets its own random key, kept in its data folder."""
    path = DATA_DIR / "secret.key"
    if not path.exists():
        import secrets
        path.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or (_desktop_secret() if DESKTOP else "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", LOCAL_MODE and not DESKTOP)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts",
    "billing",
    "transcription",
    "pages",
    "desktop",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "desktop.middleware.DesktopMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pages.context_processors.site",
                "desktop.context_processors.desktop",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{(DATA_DIR / 'db.sqlite3').as_posix()}", conn_max_age=600
    )
}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 30

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Dhaka")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = (DATA_DIR / "static_root") if DESKTOP else BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if DEBUG or DESKTOP:
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
WHITENOISE_USE_FINDERS = DESKTOP  # the app serves static files straight from static/, no collectstatic
if DESKTOP:
    Path(STATIC_ROOT).mkdir(parents=True, exist_ok=True)

# Uploaded audio is private: it lives outside any web-served directory and is
# only ever read by the worker. It is never exposed through a URL.
PRIVATE_MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", DATA_DIR / "private_media"))
FILE_UPLOAD_TEMP_DIR = os.getenv("FILE_UPLOAD_TEMP_DIR") or None
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 1024)
ALLOWED_AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".aac", ".flac", ".webm", ".amr", ".3gp"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email (password reset, notifications) ---
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@localhost")
ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL", "")

# --- Security when running behind HTTPS (Caddy) ---
if not DEBUG and not LOCAL_MODE:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"

# --- Background processing ---
# "thread": jobs run inside the web process (local PC). "celery": separate worker (server).
TASK_RUNNER = os.getenv("TASK_RUNNER", "thread" if LOCAL_MODE else "celery")
LOCAL_WORKERS = env_int("LOCAL_WORKERS", 1)  # recordings processed at the same time

# --- Celery (only used when TASK_RUNNER=celery) ---
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "purge-expired-audio": {
        "task": "transcription.tasks.purge_expired_audio",
        "schedule": 60 * 30,  # every 30 minutes
    },
}

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_TRANSCRIBE_MODEL = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.8-flash")
GEMINI_TRANSLATE_MODEL = os.getenv("GEMINI_TRANSLATE_MODEL", "gemini-3.8-flash")
# Audio sent per request. 20 min = half as many requests as 10 min (the free tier's daily
# request limit is usually what runs out first) and still well under the output limit.
CHUNK_SECONDS = env_int("CHUNK_SECONDS", 1200)
TRANSLATE_BATCH_SIZE = env_int("TRANSLATE_BATCH_SIZE", 60)
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")  # used when the main model is busy
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low").strip()  # "" = model default
# How English words spoken inside Bangla are written: "bangla" (লোন) or "latin" (loan)
ENGLISH_WORDS_SCRIPT = os.getenv("ENGLISH_WORDS_SCRIPT", "bangla").strip().lower()
# When Google is busy, a job waits and retries by itself this many times before giving up
AUTO_RETRY_LIMIT = env_int("AUTO_RETRY_LIMIT", 6)
AUTO_RETRY_MINUTES = env_int("AUTO_RETRY_MINUTES", 10)
GEMINI_MAX_RETRIES = env_int("GEMINI_MAX_RETRIES", 6)  # per model; waits grow 5 s → 90 s (≈4 min in total)

# --- Business settings ---
SITE_NAME = os.getenv("SITE_NAME", "QualResearcher")
if SITE_NAME.strip().lower() == "kothon":  # old default copied into .env files made before the rename
    SITE_NAME = "QualResearcher"
SITE_TAGLINE = os.getenv("SITE_TAGLINE", "Bangla interview transcription and English translation for researchers")
SUPPORT_WHATSAPP = os.getenv("SUPPORT_WHATSAPP", "")  # e.g. 8801XXXXXXXXX
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "")
BKASH_ACCOUNT_TYPE = os.getenv("BKASH_ACCOUNT_TYPE", "Personal")
BILLING_ENABLED = env_bool("BILLING_ENABLED", not LOCAL_MODE)
REQUIRE_APPROVAL = env_bool("REQUIRE_APPROVAL", not LOCAL_MODE)
TRIAL_MINUTES = env_int("TRIAL_MINUTES", 30)
TRIAL_VALID_DAYS = env_int("TRIAL_VALID_DAYS", 14)
MIN_BILLABLE_MINUTES = env_int("MIN_BILLABLE_MINUTES", 1)
RETENTION_CHOICES_DAYS = [1, 2, 3]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}

# --- Desktop app ---
from .version import __version__ as APP_VERSION  # noqa: E402
# GitHub "owner/repo" whose Releases page holds the installer; used for "Update available".
GITHUB_REPO = os.getenv("GITHUB_REPO", "RanaRedoan/QualResearcher").strip()
# Where "Save as Word" puts files in the desktop app (can be changed in Settings).
DEFAULT_EXPORT_DIR = Path.home() / "Documents" / "QualResearcher"
DEVELOPER_NAME = "Rana Redoan"
DEVELOPER_URL = "https://github.com/RanaRedoan"
DEVELOPER_EMAIL = "redoanhossain630@gmail.com"
