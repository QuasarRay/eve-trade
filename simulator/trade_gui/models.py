from django.db import models


class GameGuiButton(models.Model):
    class Window(models.TextChoices):
        REGIONAL_MARKET = "regional_market", "Regional Market"
        CONTRACTS = "contracts", "Contracts"
        DIRECT_TRADE = "direct_trade", "Direct Trade"
        WALLET_ORDERS = "wallet_orders", "Wallet Orders"

    window = models.CharField(max_length=40, choices=Window.choices)
    label = models.CharField(max_length=80)
    action = models.CharField(max_length=80, unique=True)
    tooltip = models.TextField(blank=True)
    wiki_reference = models.URLField(blank=True)
    default_payload = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "window", "label")

    def __str__(self) -> str:
        return f"{self.get_window_display()} / {self.label}"


class GameGuiInteraction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    button = models.ForeignKey(
        GameGuiButton,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="interactions",
    )
    action = models.CharField(max_length=80)
    player_input = models.JSONField(default=dict, blank=True)
    raw_packet = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M:%S}"
