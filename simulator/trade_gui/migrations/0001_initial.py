from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GameGuiButton",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window", models.CharField(choices=[("regional_market", "Regional Market"), ("contracts", "Contracts"), ("direct_trade", "Direct Trade"), ("wallet_orders", "Wallet Orders")], max_length=40)),
                ("label", models.CharField(max_length=80)),
                ("action", models.CharField(max_length=80, unique=True)),
                ("tooltip", models.TextField(blank=True)),
                ("wiki_reference", models.URLField(blank=True)),
                ("default_payload", models.JSONField(blank=True, default=dict)),
                ("enabled", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ("sort_order", "window", "label")},
        ),
        migrations.CreateModel(
            name="GameGuiInteraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=80)),
                ("player_input", models.JSONField(blank=True, default=dict)),
                ("raw_packet", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")], default="pending", max_length=20)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("button", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="interactions", to="trade_gui.gameguibutton")),
            ],
            options={"ordering": ("-created_at",)},
        ),
    ]
