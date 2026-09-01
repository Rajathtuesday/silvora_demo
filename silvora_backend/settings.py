import os
import secrets
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
# No hardcoded fallback string: a fixed value committed to the repo would let
# anyone who has read the source forge session/CSRF tokens if DEBUG is ever
# accidentally left True in a real deployment. For local dev without the env
# var set, generate a fresh random key per process instead — sessions just
# don't survive a dev-server restart, which is the correct trade-off.
SECRET_KEY = _secret or secrets.token_urlsafe(64)
DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

ALLOWED_HOSTS = [
    "app.silvora.cloud",
    "silvora.cloud",
    "api.silvora.cloud",
    "silvora-demo.onrender.com",  # actual live Render hostname per Cloudflare DNS (api/root both CNAME here)
    "localhost",
    "127.0.0.1",
]

CSRF_TRUSTED_ORIGINS = [
    "https://silvora-demo.onrender.com",
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

# Brute-force lockout. DRF's throttle classes (see REST_FRAMEWORK below)
# only cover our own API views -- they have zero effect on the plain
# Django /admin/ login form. AxesStandaloneBackend sits in
# AUTHENTICATION_BACKENDS below, so this also covers every other call to
# Django's authenticate() -- including the JWT login endpoint
# (LowercaseTokenObtainPairSerializer forwards the request into
# authenticate(), which DRF's generic views always populate) -- giving the
# main login a real per-account lockout, not just IP-scoped rate limiting.
# Left on the default DB-backed handler (not the cache-backed one): no
# CACHES setting exists here, and a cache handler would desync lockout
# state across gunicorn's multiple worker processes.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_RESET_ON_SUCCESS = True

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
    "axes",

    "files",
    "users",
    "billing",
    "tenants.apps.TenantsConfig",
    "django_extensions",
]

AUTH_USER_MODEL = "users.User"

# AxesStandaloneBackend must come first -- it's the one that actually
# blocks a locked-out login attempt; ModelBackend does the real credential
# check once axes lets the attempt through.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# =====================================================
# 🔑 PASSWORD STRENGTH (critical for a Zero-Knowledge vault)
#
# As of the 2026-08-31 fix: this field NEVER receives the user's actual
# password. It receives login_auth_key = HKDF(KEK, "silvora-login-auth"),
# a one-way value the client derives locally, the same pattern already used
# for the recovery phrase (see MasterKeyEnvelope.recovery_auth_hash). The
# real password still derives the KEK that protects the master key -- that
# derivation just never leaves the device, and the server no longer sees or
# stores anything that could reproduce it.
#
# CommonPasswordValidator/NumericPasswordValidator/UserAttributeSimilarity-
# Validator are now effectively inert -- they're written for a human-chosen
# password, and this field holds a high-entropy derived key that will never
# match a common-password list, never be all-numeric, and never resemble a
# username/email. Left in place because they don't reject anything valid,
# not because they're doing meaningful work here. The REAL password-strength
# gate is client-side (register_screen.dart: min 12 chars) on the actual raw
# password, before it's ever run through Argon2 -- that requirement didn't
# move, only what the server sees afterward did.
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
    "axes.middleware.AxesMiddleware",  # must be last, per django-axes
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
STATICFILES_DIRS = [BASE_DIR / "static"]  # project-level assets (e.g. the downloadable APK)
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
    # `throttle_scope`. AnonRateThrottle is a general backstop for
    # unauthenticated traffic; UserRateThrottle is a blanket per-user cap so
    # an authenticated but unscoped view is never fully unlimited even if a
    # future endpoint forgets to declare a scope.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "600/min",
        "register": "5/min",
        "login": "10/min",
        "master_key": "10/min",
        "email_verify": "5/min",
        "billing": "10/min",
        # File chunk transfer happens at high frequency for large files
        # (a 1 GB upload at 5 MB/chunk is ~200 requests), so these scopes
        # sit well above normal usage while still bounding abuse.
        "file_chunk": "300/min",
        # start/commit/delete/restore/rename are once-per-file operations —
        # far lower legitimate frequency than chunk transfer.
        "file_mutate": "60/min",
        # list/resume/quota reads.
        "file_meta": "120/min",
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
# Resend SMTP relay. EMAIL_HOST_USER is the literal string "resend" for
# every Resend account (not a secret) -- the actual credential is the API
# key, passed as the SMTP password via RESEND_API_KEY.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = "smtp.resend.com"
# 2587, not the standard 587 -- Render (like many PaaS hosts) blocks or
# heavily restricts outbound traffic on standard SMTP ports to prevent spam
# relay abuse. Resend documents 2587/3587 as STARTTLS alternates precisely
# for hosts in this situation. A prior attempt on 587 failed with a raw
# socket timeout (not an auth error), which is the signature of a blocked
# port rather than a credentials problem.
EMAIL_PORT = 2587
EMAIL_USE_TLS = True
# Django's SMTP backend has no timeout at all by default — a slow/blocked
# connection hangs the entire request indefinitely instead of failing fast,
# which defeats the point of registration being designed to never block on
# email delivery. 10s is generous for a normal SMTP handshake and short
# enough that a stuck connection still fails fast.
EMAIL_TIMEOUT = 10
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = os.environ.get("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = "noreply@silvora.cloud"

# Public base URL used to build the verification link sent by email (no
# `request` object available outside the view that triggers the send, and
# this keeps the link host correct in dev vs prod without guessing from env).
# Points at api.silvora.cloud, not app.silvora.cloud: both links built from
# this value (verify-email, billing checkout) are Django views served by
# THIS backend, and api.silvora.cloud's DNS already correctly reaches it.
# app.silvora.cloud is still a dead Cloudflare Tunnel as of 2026-07-04 --
# switch back once that's pointed at this backend too.
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://api.silvora.cloud")

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

# =====================================================
# 🤖 GOOGLE PLAY BILLING (parallel to Razorpay -- required alongside it by
# Google's India Alternative Billing policy, not a replacement for it)
# =====================================================
GOOGLE_PLAY_PACKAGE_NAME = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME", "cloud.silvora.app")

# Either the raw JSON content (practical for a host like Render, where only
# env vars, not mounted files, are easy to set) or a path to a key file
# (for local dev). No hardcoded fallback for either -- same no-committed-
# secret principle as DJANGO_SECRET_KEY above.
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH", "")

# Must match exactly what's configured on the Pub/Sub push subscription
# that delivers Real-time developer notifications to /api/billing/play/rtdn/.
PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL = os.environ.get("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", "")
PUBSUB_PUSH_AUDIENCE = os.environ.get("PUBSUB_PUSH_AUDIENCE", "")
