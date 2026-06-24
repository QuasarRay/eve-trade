from __future__ import annotations

from django.conf import settings
from uuid import uuid4

from django.shortcuts import render
from django.utils import timezone
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
        packet = {
            "schema_version": "eve-trade-gui.v1",
            "source": "django-rest-framework-simulator",
            "sent_at": timezone.now().isoformat(),
            "interaction_id": str(uuid4()),
            "ui": {
                "window": button.window,
                "button": button.label,
                "action": button.action,
            },
            "input": player_input,
        }
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
