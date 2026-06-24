from django.core.management.base import BaseCommand

from trade_gui.models import GameGuiButton


BUTTONS = [
    {
        "window": GameGuiButton.Window.REGIONAL_MARKET,
        "label": "Sell This Item",
        "action": "market_place_sell_order",
        "tooltip": "Create a regional-market sell order from the selected item stack.",
        "wiki_reference": "https://wiki.eveuniversity.org/Trading",
        "sort_order": 10,
        "default_payload": {
            "idempotency_key": "sim-market-sell",
            "external_request_id": "sim-market-sell",
            "issued_by_capsuleer_id": 1001,
            "item_stack": {
                "item_stack_id": "",
                "owner_id": 1001,
                "item_type_id": 34,
                "station_id": 60003760,
                "quantity": 10,
            },
            "quantity": 4,
            "unit_price_isk": 25,
        },
    },
    {
        "window": GameGuiButton.Window.REGIONAL_MARKET,
        "label": "Buy",
        "action": "market_buy_from_sell_order",
        "tooltip": "Buy quantity from an available sell order.",
        "wiki_reference": "https://wiki.eveuniversity.org/Trading",
        "sort_order": 20,
        "default_payload": {
            "idempotency_key": "sim-market-buy",
            "external_request_id": "sim-market-buy",
            "trade_instance_id": "",
            "buyer_capsuleer_id": 2002,
            "quantity_requested": 1,
            "buyer_wallet_id": "",
            "buyer_destination_item_stack_id": "",
        },
    },
    {
        "window": GameGuiButton.Window.WALLET_ORDERS,
        "label": "Cancel Order",
        "action": "market_cancel_order",
        "tooltip": "Cancel an outstanding sell order from the Wallet orders tab.",
        "wiki_reference": "https://wiki.eveuniversity.org/Neocom",
        "sort_order": 30,
        "default_payload": {
            "idempotency_key": "sim-market-cancel",
            "external_request_id": "sim-market-cancel",
            "trade_instance_id": "",
            "cancelled_by_capsuleer_id": 1001,
        },
    },
    {
        "window": GameGuiButton.Window.CONTRACTS,
        "label": "Create Item Exchange",
        "action": "contract_create_item_exchange",
        "tooltip": "Create an item-exchange contract for items or ISK.",
        "wiki_reference": "https://wiki.eveuniversity.org/Contracts",
        "sort_order": 40,
        "default_payload": {
            "idempotency_key": "sim-contract-create",
            "external_request_id": "sim-contract-create",
            "issued_by_capsuleer_id": 1001,
            "item_stack": {
                "item_stack_id": "",
                "owner_id": 1001,
                "item_type_id": 34,
                "station_id": 60003760,
                "quantity": 10,
            },
            "quantity": 1,
            "unit_price_isk": 25,
        },
    },
    {
        "window": GameGuiButton.Window.CONTRACTS,
        "label": "Accept Item Exchange",
        "action": "contract_accept_item_exchange",
        "tooltip": "Accept an item-exchange contract.",
        "wiki_reference": "https://wiki.eveuniversity.org/Contracts",
        "sort_order": 50,
        "default_payload": {
            "idempotency_key": "sim-contract-accept",
            "external_request_id": "sim-contract-accept",
            "trade_instance_id": "",
            "buyer_capsuleer_id": 2002,
            "quantity_requested": 1,
            "buyer_wallet_id": "",
            "buyer_destination_item_stack_id": "",
        },
    },
    {
        "window": GameGuiButton.Window.DIRECT_TRADE,
        "label": "Offer",
        "action": "direct_trade_offer",
        "tooltip": "Offer selected items in a direct player-to-player trade.",
        "wiki_reference": "https://wiki.eveuniversity.org/Trading",
        "sort_order": 60,
        "default_payload": {
            "idempotency_key": "sim-direct-offer",
            "external_request_id": "sim-direct-offer",
            "issued_by_capsuleer_id": 1001,
            "item_stack": {
                "item_stack_id": "",
                "owner_id": 1001,
                "item_type_id": 34,
                "station_id": 60003760,
                "quantity": 10,
            },
            "quantity": 1,
            "unit_price_isk": 25,
        },
    },
    {
        "window": GameGuiButton.Window.DIRECT_TRADE,
        "label": "Accept",
        "action": "direct_trade_accept",
        "tooltip": "Accept a direct player-to-player trade.",
        "wiki_reference": "https://wiki.eveuniversity.org/Trading",
        "sort_order": 70,
        "default_payload": {
            "idempotency_key": "sim-direct-accept",
            "external_request_id": "sim-direct-accept",
            "trade_instance_id": "",
            "buyer_capsuleer_id": 2002,
            "quantity_requested": 1,
            "buyer_wallet_id": "",
            "buyer_destination_item_stack_id": "",
        },
    },
]


class Command(BaseCommand):
    help = "Seed the simulator with EVE trade GUI buttons."

    def handle(self, *args, **options):
        for row in BUTTONS:
            GameGuiButton.objects.update_or_create(
                action=row["action"],
                defaults=row,
            )
        self.stdout.write(self.style.SUCCESS(f"seeded {len(BUTTONS)} trade GUI buttons"))
