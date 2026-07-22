from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SIMULATOR_ENVIRONMENT = os.environ.get("SIMULATOR_ENVIRONMENT", "development").strip().lower()
SECRET_KEY = os.environ.get("SIMULATOR_SECRET_KEY", "local-eve-trade-simulator")
DEBUG = os.environ.get("SIMULATOR_DEBUG", "1") != "0"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("SIMULATOR_ALLOWED_HOSTS", "127.0.0.1,localhost,*").split(",") if host.strip()]

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
QUILKIN_UDP_ADDRESS_FAMILY = os.environ.get("QUILKIN_UDP_ADDRESS_FAMILY", "ipv4").strip().lower()
QUILKIN_UDP_TIMEOUT_SECONDS = float(os.environ.get("QUILKIN_UDP_TIMEOUT_SECONDS", "6.0"))
QUILKIN_UDP_MAX_ATTEMPTS = int(os.environ.get("QUILKIN_UDP_MAX_ATTEMPTS", "3"))
QUILKIN_UDP_RETRY_BACKOFF_SECONDS = float(os.environ.get("QUILKIN_UDP_RETRY_BACKOFF_SECONDS", "0.1"))
QUILKIN_UDP_SESSION_POOL_SIZE = int(os.environ.get("QUILKIN_UDP_SESSION_POOL_SIZE", "4"))
GAME_PACKET_HMAC_SECRET = os.environ.get("GAME_PACKET_HMAC_SECRET", "local-game-edge-secret")
GAME_PACKET_HMAC_KEY_ID = os.environ.get("GAME_PACKET_HMAC_KEY_ID", "primary")
GAME_PACKET_PRINCIPAL_KEYS_JSON = os.environ.get(
    "GAME_PACKET_PRINCIPAL_KEYS_JSON",
    '{"1001":{"key_id":"seller","secret":"seller-player-secret"},'
    '"2002":{"key_id":"buyer","secret":"buyer-player-secret"}}',
)


def _validate_runtime_settings() -> None:
    if QUILKIN_UDP_ADDRESS_FAMILY not in {"ipv4", "ipv6", "dual"}:
        raise ImproperlyConfigured(
            "QUILKIN_UDP_ADDRESS_FAMILY must be one of ipv4, ipv6, or dual"
        )
    if SIMULATOR_ENVIRONMENT in {"development", "local", "test"}:
        return
    unsafe: list[str] = []
    if not SECRET_KEY or SECRET_KEY == "local-eve-trade-simulator":
        unsafe.append("SIMULATOR_SECRET_KEY")
    if DEBUG:
        unsafe.append("SIMULATOR_DEBUG")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        unsafe.append("SIMULATOR_ALLOWED_HOSTS")
    if not GAME_PACKET_HMAC_SECRET or GAME_PACKET_HMAC_SECRET == "local-game-edge-secret":
        unsafe.append("GAME_PACKET_HMAC_SECRET")
    if unsafe:
        raise ImproperlyConfigured(
            "unsafe simulator production settings: " + ", ".join(unsafe)
        )


_validate_runtime_settings()
