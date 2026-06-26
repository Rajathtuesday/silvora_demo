import os
from datetime import timedelta
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env if present. Real environment variables still take
# precedence (load_dotenv does not override existing os.environ), so
# production config set in the host's dashboard is unaffected.
load_dotenv(BASE_DIR / ".env")

# =====================================================
# 🔐 SECURITY / ENV CONFIG
# =====================================================

_secret = os.environ.get("DJANGO_SECRET_KEY")
if not _secret and not os.environ.get("DJANGO_DEBUG", "False") == "True":
    raise ValueError("DJANGO_SECRET_KEY environment variable is not set in production.")
SECRET_KEY = _secret or "django-insecure-dev-key-change-this"
DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

ALLOWED_HOSTS = [
    "app.silvora.cloud",
    "silvora.cloud",
    "api.silvora.cloud",
    ".onrender.com",  # any Render subdomain (e.g. silvora-backend.onrender.com)
    "localhost",
    "127.0.0.1",
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "https://silvora.cloud",
    "https://api.silvora.cloud",
    "https://app.silvora.cloud",
]

# Production security headers. Opt-in via DJANGO_SECURE=True so local dev and
# the test runner are never forced onto HTTPS. Render/most PaaS terminate TLS
# at their proxy, hence SECURE_PROXY_SSL_HEADER.
_SECURE = os.environ.get("DJANGO_SECURE", "False") == "True"
if _SECURE:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# =====================================================
# 📦 INSTALLED APPS
# =====================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",

    "files",
    "users",
    "billing",
    "tenants.apps.TenantsConfig",
    "django_extensions",
]

AUTH_USER_MODEL = "users.User"

# =====================================================
# 🔑 PASSWORD STRENGTH (critical for a Zero-Knowledge vault)
# The password derives the KEK that protects the master key, so a weak
# password = a weak vault no matter how strong the cipher is.
# =====================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]

# =====================================================
# 🧱 MIDDLEWARE
# =====================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "silvora_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "silvora_backend.wsgi.application"

# =====================================================
# 🛢 DATABASE
# =====================================================

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        ssl_require=os.environ.get("DATABASE_URL") is not None
    )
}

# =====================================================
# 📁 STATIC & MEDIA
# =====================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =====================================================
# 🔥 DRF / JWT AUTH
# =====================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated"
    ],
    # Throttling. ScopedRateThrottle only limits views that declare a
    # `throttle_scope` (login / register / master-key), so authenticated
    # file operations are NOT rate-limited. AnonRateThrottle is a general
    # backstop for unauthenticated traffic.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "register": "5/min",
        "login": "10/min",
        "master_key": "10/min",
        "email_verify": "5/min",
        "billing": "10/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# =====================================================
# 🔄 CORS
# =====================================================

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = [
        "https://silvora.cloud",
        "https://app.silvora.cloud",
    ]

# =====================================================
# 📤 UPLOAD LIMITS
# =====================================================

DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

# =====================================================
# ☁️ CLOUDFLARE R2 CONFIG
# =====================================================

# Logging: surface server-side errors (incl. 500 tracebacks) to stderr so they
# appear in the host's log stream even with DEBUG=False. Without this, Django's
# default config swallows request tracebacks in production.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else None
R2_PUBLIC_BASE = f"{R2_ENDPOINT}/{R2_BUCKET_NAME}" if R2_ENDPOINT else None

# =====================================================
# 📧 EMAIL (verification, billing notices)
# =====================================================
# Gmail SMTP relay — same mechanism as Rasova's setup, kept deliberately
# consistent across both projects. EMAIL_USER/EMAIL_PASSWORD are a Gmail App
# Password, not the account password.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
# Django's SMTP backend has no timeout at all by default — a slow/blocked
# connection to Gmail hangs the entire request indefinitely instead of
# failing fast, which defeats the point of registration being designed to
# never block on email delivery. 10s is generous for a normal SMTP
# handshake and short enough that a stuck connection still fails fast.
EMAIL_TIMEOUT = 10
EMAIL_HOST_USER = os.environ.get("EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_USER", "noreply@silvora.cloud")

# Public base URL used to build the verification link sent by email (no
# `request` object available outside the view that triggers the send, and
# this keeps the link host correct in dev vs prod without guessing from env).
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://app.silvora.cloud")

# Bump this string whenever the privacy policy materially changes — stored
# on each user at registration (privacy_policy_version) so old acceptances
# stay tied to the version they actually agreed to, not silently relabeled.
PRIVACY_POLICY_VERSION = "2026-06-26"

# =====================================================
# 💳 RAZORPAY (subscription billing — Silvora's own account,
# separate from any restaurant customer's Razorpay credentials elsewhere)
# =====================================================
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
