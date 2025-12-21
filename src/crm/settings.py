from importlib.metadata import version
from pathlib import Path

from decouple import Csv, config
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="dev-secret-key-change-in-production")
DEBUG = config("DEBUG", default=True, cast=bool)
ADMIN_URL = config("ADMIN_URL", default="admin/")

# Domain configuration - single source of truth for production
# Set DOMAIN=crm.example.com and everything else is derived
DOMAIN = config("DOMAIN", default="")

if DOMAIN:
    # Production: derive from DOMAIN
    ALLOWED_HOSTS = [DOMAIN]
    CSRF_TRUSTED_ORIGINS = [f"https://{DOMAIN}"]
else:
    # Development: use explicit config or defaults
    ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv(), default="localhost,127.0.0.1")
    CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", cast=Csv(), default="")

# Security settings for production (behind reverse proxy)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True

SITE_ID = 1
SITE_NAME = config("SITE_NAME", default="Micro CRM")

VERSION = version("crm")

# Gemini API
GEMINI_API_KEY = config("GEMINI_API_KEY", default="")

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.simple_history",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # third party
    "ninja_extra",
    "solo",
    "simple_history",
    "django_google_sso",
    "django_celery_results",
    "django_celery_beat",
    # apps
    "leads",
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
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "crm.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "crm.wsgi.application"

if DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("DB_NAME", default="crm"),
            "USER": config("DB_USER", default="crm"),
            "PASSWORD": config("DB_PASSWORD", default="crm"),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="5432"),
            "ATOMIC_REQUESTS": True,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# API
API_KEY = config("API_KEY", default="dev-api-key")

# Redis & Celery
REDIS_HOST = config("REDIS_HOST", default="localhost")
REDIS_PORT = config("REDIS_PORT", cast=int, default=6379)
REDIS_DB = config("REDIS_DB", cast=int, default=0)

CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_EXTENDED = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_RESULT_BACKEND = "django-db"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = config("CELERY_TASK_ALWAYS_EAGER", cast=bool, default=DEBUG)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Google SSO
GOOGLE_SSO_ALLOWABLE_DOMAINS = ["*"]
GOOGLE_SSO_CLIENT_ID = config("GOOGLE_SSO_CLIENT_ID", default="fake-id")
GOOGLE_SSO_CLIENT_SECRET = config("GOOGLE_SSO_CLIENT_SECRET", default="fake-secret")
GOOGLE_SSO_PROJECT_ID = config("GOOGLE_SSO_PROJECT_ID", default="fake-project-id")
GOOGLE_SSO_AUTO_CREATE_USERS = True
GOOGLE_SSO_ALWAYS_UPDATE_USER_DATA = True
GOOGLE_SSO_SUPERUSER_LIST = [
    email for email in config("GOOGLE_SSO_SUPERUSER_LIST", cast=Csv(), default="") if "@" in email
]
GOOGLE_SSO_STAFF_LIST = GOOGLE_SSO_SUPERUSER_LIST
SSO_SHOW_FORM_ON_ADMIN_PAGE = DEBUG

# Unfold
UNFOLD = {
    "SITE_TITLE": "MicroCRM",
    "SITE_HEADER": "MicroCRM",
    "SITE_URL": "/",
    "DASHBOARD_CALLBACK": "crm.dashboard.dashboard_callback",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "250 245 255",
            "100": "243 232 255",
            "200": "233 213 255",
            "300": "216 180 254",
            "400": "192 132 252",
            "500": "168 85 247",
            "600": "147 51 234",
            "700": "126 34 206",
            "800": "107 33 168",
            "900": "88 28 135",
            "950": "59 7 100",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Dashboard",
                "separator": False,
                "items": [
                    {"title": "Dashboard", "icon": "home", "link": reverse_lazy("admin:index")},
                ],
            },
            {
                "title": "Leads",
                "separator": True,
                "collapsible": True,
                "items": [
                    {"title": "Leads", "icon": "person_add", "link": reverse_lazy("admin:leads_lead_changelist")},
                    {"title": "Actions", "icon": "task_alt", "link": reverse_lazy("admin:leads_action_changelist")},
                    {
                        "title": "Lead Types",
                        "icon": "category",
                        "link": reverse_lazy("admin:leads_leadtype_changelist"),
                    },
                    {"title": "Tags", "icon": "label", "link": reverse_lazy("admin:leads_tag_changelist")},
                    {"title": "Cities", "icon": "location_city", "link": reverse_lazy("admin:leads_city_changelist")},
                ],
            },
            {
                "title": "Research",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Research Jobs",
                        "icon": "science",
                        "link": reverse_lazy("admin:leads_researchjob_changelist"),
                    },
                    {
                        "title": "Prompt Config",
                        "icon": "edit_note",
                        "link": reverse_lazy("admin:leads_researchpromptconfig_changelist"),
                    },
                ],
            },
            {
                "title": "Celery",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Periodic Tasks",
                        "icon": "schedule",
                        "link": reverse_lazy("admin:django_celery_beat_periodictask_changelist"),
                    },
                    {
                        "title": "Task Results",
                        "icon": "task_alt",
                        "link": reverse_lazy("admin:django_celery_results_taskresult_changelist"),
                    },
                ],
            },
            {
                "title": "Auth",
                "separator": True,
                "collapsible": True,
                "items": [
                    {"title": "Users", "icon": "person", "link": reverse_lazy("admin:auth_user_changelist")},
                    {"title": "Groups", "icon": "group", "link": reverse_lazy("admin:auth_group_changelist")},
                ],
            },
        ],
    },
}
