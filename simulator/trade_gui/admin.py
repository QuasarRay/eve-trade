from django.contrib import admin

from .models import GameGuiButton, GameGuiInteraction


@admin.register(GameGuiButton)
class GameGuiButtonAdmin(admin.ModelAdmin):
    list_display = ("label", "window", "action", "enabled", "sort_order")
    list_filter = ("window", "enabled")
    search_fields = ("label", "action")


@admin.register(GameGuiInteraction)
class GameGuiInteractionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "status", "button")
    list_filter = ("status", "action")
    readonly_fields = ("created_at", "raw_packet", "response_payload")
