# # project_name/settings.py
# from datetime import timedelta
# from pathlib import Path

# BASE_DIR = Path(__file__).resolve().parent.parent

# SECRET_KEY = 'django-insecure-REPLACE_THIS_IN_PROD'
# DEBUG = True
# ALLOWED_HOSTS = ['*',
#                  "silvora.cloud",
#     "api.silvora.cloud",
#     "localhost",
#     "127.0.0.1"]

# INSTALLED_APPS = [
#     # Django core
#     'django.contrib.admin',
#     'django.contrib.auth',
#     'django.contrib.contenttypes',
#     'django.contrib.sessions',
#     'django.contrib.messages',
#     'django.contrib.staticfiles',

#     # Third party
#     'rest_framework',
#     'rest_framework_simplejwt.token_blacklist',
#     'corsheaders',

#     # Your apps
#     'files',
#     'users',
# ]

# MIDDLEWARE = [
#     'corsheaders.middleware.CorsMiddleware',            # must be high in order
#     'django.middleware.security.SecurityMiddleware',
#     'django.contrib.sessions.middleware.SessionMiddleware',
#     'django.middleware.common.CommonMiddleware',
#     'django.middleware.csrf.CsrfViewMiddleware',
#     'django.contrib.auth.middleware.AuthenticationMiddleware',
#     'django.contrib.messages.middleware.MessageMiddleware',
#     'django.middleware.clickjacking.XFrameOptionsMiddleware',
# ]

# ROOT_URLCONF = 'project_name.urls'

# TEMPLATES = [
#     {
#         'BACKEND': 'django.template.backends.django.DjangoTemplates',
#         'DIRS': [],
#         'APP_DIRS': True,
#         'OPTIONS': {
#             'context_processors': [
#                 'django.template.context_processors.request',
#                 'django.contrib.auth.context_processors.auth',
#                 'django.contrib.messages.context_processors.messages',
#             ],
#         },
#     },
# ]

# WSGI_APPLICATION = 'project_name.wsgi.application'

# # Database (leave as sqlite for dev)
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

# # Password validation
# AUTH_PASSWORD_VALIDATORS = [
#     {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
#     {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
#     {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
#     {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
# ]

# LANGUAGE_CODE = 'en-us'
# TIME_ZONE = 'UTC'
# USE_I18N = True
# USE_TZ = True

# STATIC_URL = 'static/'
# STATIC_ROOT = BASE_DIR / 'staticfiles'

# DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# # Use Argon2 as first hasher if available (good)
# PASSWORD_HASHERS = [
#     "django.contrib.auth.hashers.Argon2PasswordHasher",
#     "django.contrib.auth.hashers.PBKDF2PasswordHasher",
# ]

# # REST framework + Simple JWT
# REST_FRAMEWORK = {
#     "DEFAULT_AUTHENTICATION_CLASSES": (
#         "rest_framework_simplejwt.authentication.JWTAuthentication",
#     ),
#     "DEFAULT_PERMISSION_CLASSES": (
#         "rest_framework.permissions.IsAuthenticated",
#     ),
# }

# SIMPLE_JWT = {
#     "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
#     "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
#     "ROTATE_REFRESH_TOKENS": False,
# }

# # CORS: development default - change for production
# CORS_ALLOW_ALL_ORIGINS = True

# # Media settings
# MEDIA_URL = "/media/"
# MEDIA_ROOT = BASE_DIR / "media"


# # =====================================================
# # Large encrypted upload support (important for chunks!)
# # =====================================================

# # Disable in-memory body limit (we handle chunks manually)
# DATA_UPLOAD_MAX_MEMORY_SIZE = 100 *1024*1024
# FILE_UPLOAD_MAX_MEMORY_SIZE = 100*1024*1024

# # If reverse proxy (Nginx) is later used, configure its body limit too



# CSRF_TRUSTED_ORIGINS = [
#     'https://leakily-potted-babette.ngrok-free.dev',
#     "https://silvora.cloud",
#     "https://api.silvora.cloud",
#     "https://app.silvora.cloud"

# ]


# ---------------------------------------------------------------------------

# project_name/settings.py
# import os
# from datetime import timedelta
# from pathlib import Path

# import dj_database_url

# BASE_DIR = Path(__file__).resolve().parent.parent

# SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-key-do-not-use")
# DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

# ALLOWED_HOSTS = ["*","app.silvora.cloud"]

# CSRF_TRUSTED_ORIGINS = [
#     "https://*.onrender.com",
#     "https://silvora.cloud",
#     "https://api.silvora.cloud",
#     "https://app.silvora.cloud",
#     "https://*.onrender.com",
# ]

# INSTALLED_APPS = [
#     "django.contrib.admin",
#     "django.contrib.auth",
#     "django.contrib.contenttypes",
#     "django.contrib.sessions",
#     "django.contrib.messages",
#     "django.contrib.staticfiles",

#     "rest_framework",
#     "rest_framework_simplejwt.token_blacklist",
#     "corsheaders",

#     "files",
#     "users",
# ]

# MIDDLEWARE = [
#     "corsheaders.middleware.CorsMiddleware",
#     "django.middleware.security.SecurityMiddleware",
#     "whitenoise.middleware.WhiteNoiseMiddleware",
#     "django.contrib.sessions.middleware.SessionMiddleware",
#     "django.middleware.common.CommonMiddleware",
#     "django.middleware.csrf.CsrfViewMiddleware",
#     "django.contrib.auth.middleware.AuthenticationMiddleware",
#     "django.contrib.messages.middleware.MessageMiddleware",
#     "django.middleware.clickjacking.XFrameOptionsMiddleware",
# ]

# ROOT_URLCONF = "project_name.urls"
# WSGI_APPLICATION = "project_name.wsgi.application"

# # DB: Prefer Postgres if DATABASE_URL exists (Render)
# DATABASES = {
#     "default": dj_database_url.config(
#         default=f"sqlite:///{BASE_DIR/'db.sqlite3'}",
#         conn_max_age=600,
#         ssl_require=False
#     )
# }

# STATIC_URL = "/static/"
# STATIC_ROOT = BASE_DIR / "staticfiles"
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# MEDIA_URL = "/media/"
# MEDIA_ROOT = BASE_DIR / "media"

# DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST_FRAMEWORK = {
#     "DEFAULT_AUTHENTICATION_CLASSES": [
#         "rest_framework_simplejwt.authentication.JWTAuthentication"
#     ],
#     "DEFAULT_PERMISSION_CLASSES": [
#         "rest_framework.permissions.IsAuthenticated"
#     ],
# }

# TEMPLATES = [
#     {
#         "BACKEND": "django.template.backends.django.DjangoTemplates",
#         "DIRS": [
#             BASE_DIR / "templates",  # Allow a custom templates folder
#         ],
#         "APP_DIRS": True,
#         "OPTIONS": {
#             "context_processors": [
#                 "django.template.context_processors.debug",
#                 "django.template.context_processors.request",
#                 "django.contrib.auth.context_processors.auth",
#                 "django.contrib.messages.context_processors.messages",
#             ],
#         },
#     },
# ]



# SIMPLE_JWT = {
#     "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
#     "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
# }

# DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
# FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

# CORS_ALLOW_ALL_ORIGINS = True




# ---------------------------------------------------------------------------

# project_name/settings.py 
# with r2 config at the bottom
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# =====================================================
# 🔐 SECURITY / ENV CONFIG
# =====================================================

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-key-do-not-use")
# DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"
DEBUG = "True"

ALLOWED_HOSTS = [
    "*",
    "app.silvora.cloud",
    "silvora.cloud",
    "silvora-demo.onrender.com",
    '10.69.186.236',
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "https://silvora.cloud",
    "https://api.silvora.cloud",
    "https://app.silvora.cloud",
]

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

ROOT_URLCONF = "project_name.urls"
WSGI_APPLICATION = "project_name.wsgi.application"

# =====================================================
# 🛢 DATABASE (Render: SQLite or Postgres)
# =====================================================

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR/'db.sqlite3'}",
        conn_max_age=600,
        ssl_require=False,
    )
}

# =====================================================
# 📁 STATIC & MEDIA — IMPORTANT FOR RENDER
# =====================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Media files (local fallback - R2 will override per implementation)
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
}

# =====================================================
# 🎨 DJANGO TEMPLATES (required for admin + DRF browsable API)
# =====================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # you can leave empty []
        "APP_DIRS": True,                  # <— REQUIRED for admin and DRF
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


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

# =====================================================
# 🔄 CORS (for Flutter app)
# =====================================================

CORS_ALLOW_ALL_ORIGINS = True

# OR to restrict later:
# CORS_ALLOWED_ORIGINS = [
#     "https://silvora.cloud",
#     "https://app.silvora.cloud",
# ]

# =====================================================
# 📤 UPLOAD LIMITS
# =====================================================

DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024   # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024   # 100MB

# =====================================================
# ☁️ CLOUDFLARE R2 CONFIG (used by custom storage backend)
# =====================================================
# =====================================================
# ☁️ Cloudflare R2 (MVP - store ONLY final files)
# =====================================================

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")

#for temp testing






# S3-compatible endpoint
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

# For public URL construction (optional)
R2_PUBLIC_BASE = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{R2_BUCKET_NAME}"
