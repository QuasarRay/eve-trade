import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eve_trade_simulator.settings")

application = get_asgi_application()
