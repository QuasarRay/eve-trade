from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SIMULATOR_SECRET_KEY", "local-eve-trade-simulator")
DEBUG = os.environ.get("SIMULATOR_DEBUG", "1") != "0"
ALLOWED_HOSTS = os.environ.get("SIMULATOR_ALLOWED_HOSTS", "127.0.0.1,localhost,*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "trade_gui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "eve_trade_simulator.urls"

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

WSGI_APPLICATION = "eve_trade_simulator.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SIMULATOR_DB", str(BASE_DIR / "db.sqlite3")),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

QUILKIN_UDP_HOST = os.environ.get("QUILKIN_UDP_HOST", "127.0.0.1")
QUILKIN_UDP_PORT = int(os.environ.get("QUILKIN_UDP_PORT", "26001"))
QUILKIN_UDP_TIMEOUT_SECONDS = float(os.environ.get("QUILKIN_UDP_TIMEOUT_SECONDS", "6.0"))
QUILKIN_UDP_MAX_ATTEMPTS = int(os.environ.get("QUILKIN_UDP_MAX_ATTEMPTS", "3"))
QUILKIN_UDP_RETRY_BACKOFF_SECONDS = float(os.environ.get("QUILKIN_UDP_RETRY_BACKOFF_SECONDS", "0.1"))
GAME_PACKET_HMAC_SECRET = os.environ.get("GAME_PACKET_HMAC_SECRET", "local-game-edge-secret")
GAME_PACKET_HMAC_KEY_ID = os.environ.get("GAME_PACKET_HMAC_KEY_ID", "primary")
