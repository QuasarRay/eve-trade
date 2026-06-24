from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from trade_gui import views

router = DefaultRouter()
router.register("gui/buttons", views.GameGuiButtonViewSet, basename="game-gui-button")
router.register("gui/interactions", views.GameGuiInteractionViewSet, basename="game-gui-interaction")

urlpatterns = [
    path("", views.index, name="trade-gui-index"),
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
]
