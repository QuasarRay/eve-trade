from rest_framework import serializers

from .models import GameGuiButton, GameGuiInteraction


class GameGuiButtonSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameGuiButton
        fields = [
            "id",
            "window",
            "label",
            "action",
            "tooltip",
            "wiki_reference",
            "default_payload",
            "enabled",
            "sort_order",
        ]


class GameGuiInteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameGuiInteraction
        fields = [
            "id",
            "button",
            "action",
            "player_input",
            "raw_packet",
            "response_payload",
            "status",
            "error_message",
            "created_at",
        ]
        read_only_fields = ["raw_packet", "response_payload", "status", "error_message", "created_at"]
