from __future__ import annotations

from django.conf import settings
from uuid import uuid4

from django.shortcuts import render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .models import GameGuiButton, GameGuiInteraction
from .serializers import GameGuiButtonSerializer, GameGuiInteractionSerializer
from .udp_client import send_gui_packet


def index(request):
    buttons = GameGuiButton.objects.filter(enabled=True).order_by("sort_order", "window", "label")
    return render(
        request,
        "trade_gui/index.html",
        {
            "buttons": buttons,
            "quilkin_endpoint": f"{settings.QUILKIN_UDP_HOST}:{settings.QUILKIN_UDP_PORT}",
        },
    )


class GameGuiButtonViewSet(viewsets.ModelViewSet):
    queryset = GameGuiButton.objects.all()
    serializer_class = GameGuiButtonSerializer

    @action(detail=True, methods=["post"])
    def press(self, request: Request, pk: str | None = None) -> Response:
        button = self.get_object()
        if not button.enabled:
            return Response({"detail": "button is disabled"}, status=status.HTTP_409_CONFLICT)

        player_input = dict(button.default_payload)
        player_input.update(request.data.get("player_input", request.data) or {})
        packet = build_gui_packet(button, player_input, interaction_id=request.data.get("interaction_id"))
        interaction = GameGuiInteraction.objects.create(
            button=button,
            action=button.action,
            player_input=player_input,
            raw_packet=packet,
        )

        try:
            response_payload = send_gui_packet(packet)
        except OSError as exc:
            interaction.status = GameGuiInteraction.Status.FAILED
            interaction.error_message = str(exc)
            interaction.save(update_fields=["status", "error_message"])
            return Response(
                GameGuiInteractionSerializer(interaction).data,
                status=status.HTTP_502_BAD_GATEWAY,
            )

        interaction.response_payload = response_payload
        interaction.status = GameGuiInteraction.Status.SENT
        interaction.save(update_fields=["response_payload", "status"])
        return Response(GameGuiInteractionSerializer(interaction).data, status=status.HTTP_202_ACCEPTED)


class GameGuiInteractionViewSet(viewsets.ModelViewSet):
    queryset = GameGuiInteraction.objects.all()
    serializer_class = GameGuiInteractionSerializer


def build_gui_packet(button: GameGuiButton, player_input: dict, interaction_id: str | None = None) -> dict:
    player_input = dict(player_input)
    interaction_id = (
        interaction_id
        or player_input.pop("interaction_id", None)
        or player_input.pop("idempotency_key", None)
        or str(uuid4())
    )
    interaction_id = str(interaction_id).strip() or str(uuid4())
    player_input.pop("external_request_id", None)
    return {
        "schema_version": "eve-trade-gui.v1",
        "interaction_id": interaction_id,
        "ui": {
            "window": button.window,
            "control_id": button.action,
            "action": button.action,
        },
        "input": player_input,
    }
