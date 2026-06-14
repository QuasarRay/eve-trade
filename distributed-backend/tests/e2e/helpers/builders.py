from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_millis() -> int:
    return int(time.time() * 1000)


@dataclass
class TradeScenarioIds:
    operation_id: str = field(default_factory=new_uuid)
    request_id: str = field(default_factory=new_uuid)
    correlation_id: str = field(default_factory=new_uuid)
    trade_instance_id: str = field(default_factory=new_uuid)
    transaction_id: str = field(default_factory=new_uuid)
    settlement_id: str = field(default_factory=new_uuid)
    buyer_destination_stack_id: str = field(default_factory=new_uuid)
    issuer_wallet_id: str = field(default_factory=new_uuid)
    buyer_wallet_id: str = field(default_factory=new_uuid)
    issuer_item_stack_id: str = field(default_factory=new_uuid)
    item_stack_escrow_id: str = field(default_factory=new_uuid)


def operation_metadata(proto, *, caused_by_capsuleer_id: int, purpose: str):
    identity = proto.identity
    return proto.metadata.OperationMetadata(
        operation_id=identity.OperationId(value=new_uuid()),
        request_id=identity.RequestId(value=new_uuid()),
        idempotency_key=proto.idempotency.IdempotencyKey(
            value=f"e2e:{purpose}:{new_uuid()}"
        ),
        correlation_id=identity.CorrelationId(value=f"e2e-{new_uuid()}"),
        trace_id=identity.TraceId(value=f"e2e-{new_uuid()}"),
        source_system=identity.SourceSystem(value="e2e-tests"),
        external_operation_id=identity.ExternalOperationId(value=f"e2e-{new_uuid()}"),
        caused_by_capsuleer_id=identity.CapsuleerId(value=caused_by_capsuleer_id),
        created_by_service=identity.CreatedByService(value="e2e-tests"),
        requested_at_unix_millis=now_millis(),
    )


def with_idempotency_key(proto, metadata, value: str):
    copy = proto.metadata.OperationMetadata()
    copy.CopyFrom(metadata)
    copy.idempotency_key.value = value
    return copy


def issue_command(
    proto,
    ids: TradeScenarioIds,
    world,
    *,
    total_quantity: int = 5,
    unit_price_minor: int = 10_000,
    expires_at_unix_millis: int | None = None,
    metadata=None,
):
    identity = proto.identity
    metadata = metadata or operation_metadata(
        proto,
        caused_by_capsuleer_id=world.issuer_id,
        purpose="issue-trade-instance",
    )
    issue = proto.issue.IssueTradeInstanceCommand(
        metadata=metadata,
        row_ids=proto.issue.IssueTradeInstanceRowIds(
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            issuer_id=identity.CapsuleerId(value=world.issuer_id),
            issuer_wallet_id=identity.WalletId(value=ids.issuer_wallet_id),
            item_type_id=identity.ItemTypeId(value=world.item_type_id),
            station_id=identity.StationId(value=world.station_id),
            region_id=identity.RegionId(value=world.region_id),
            source_item_stack_id=identity.ItemStackId(value=ids.issuer_item_stack_id),
            item_stack_escrow_id=identity.ItemStackEscrowId(
                value=ids.item_stack_escrow_id
            ),
        ),
        terms=proto.issue.IssueTradeInstanceTerms(
            total_quantity=proto.quantity.ItemQuantity(units=total_quantity),
            unit_price_isk=proto.money.IskAmount(minor_units=unit_price_minor),
            expires_at_unix_millis=(
                expires_at_unix_millis
                if expires_at_unix_millis is not None
                else now_millis() + 3_600_000
            ),
        ),
    )
    return proto.settlement_command.TradeSettlementCommand(
        metadata=metadata,
        operation_kind=proto.operation_kind.TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE,
        issue_trade_instance=issue,
    )


def settle_command(
    proto,
    ids: TradeScenarioIds,
    world,
    *,
    quantity: int,
    unit_price_minor: int = 10_000,
    total_price_minor: int | None = None,
    seller_capsuleer_id: int | None = None,
    seller_wallet_id: str | None = None,
    buyer_capsuleer_id: int | None = None,
    buyer_wallet_id: str | None = None,
    destination_item_stack_id: str | None = None,
    metadata=None,
):
    identity = proto.identity
    seller_capsuleer_id = seller_capsuleer_id or world.issuer_id
    seller_wallet_id = seller_wallet_id or ids.issuer_wallet_id
    buyer_capsuleer_id = buyer_capsuleer_id or world.buyer_id
    buyer_wallet_id = buyer_wallet_id or ids.buyer_wallet_id
    destination_item_stack_id = (
        destination_item_stack_id or ids.buyer_destination_stack_id
    )
    metadata = metadata or operation_metadata(
        proto,
        caused_by_capsuleer_id=buyer_capsuleer_id,
        purpose="settle-trade-instance",
    )
    total_price_minor = (
        total_price_minor if total_price_minor is not None else quantity * unit_price_minor
    )
    accepted = proto.accept.AcceptTradeInstanceCommand(
        metadata=metadata,
        row_ids=proto.accept.AcceptTradeInstanceRowIds(
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            buyer_capsuleer_id=identity.CapsuleerId(value=buyer_capsuleer_id),
            buyer_wallet_id=identity.WalletId(value=buyer_wallet_id),
            destination_item_stack_id=identity.ItemStackId(
                value=destination_item_stack_id
            ),
        ),
        terms=proto.accept.AcceptTradeInstanceTerms(
            quantity=proto.quantity.ItemQuantity(units=quantity),
            expected_unit_price_isk=proto.money.IskAmount(
                minor_units=unit_price_minor
            ),
            expected_total_price_isk=proto.money.IskAmount(
                minor_units=total_price_minor
            ),
            accepted_at_unix_millis=now_millis(),
        ),
    )
    settle = proto.settle.SettleTradeInstanceCommand(
        metadata=metadata,
        row_ids=proto.settle.SettleTradeInstanceRowIds(
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            source_item_stack_escrow_id=identity.ItemStackEscrowId(
                value=ids.item_stack_escrow_id
            ),
            trade_transaction_id=identity.TradeTransactionId(value=ids.transaction_id),
            settlement_id=identity.SettlementId(value=ids.settlement_id),
            seller_capsuleer_id=identity.CapsuleerId(value=seller_capsuleer_id),
            seller_wallet_id=identity.WalletId(value=seller_wallet_id),
            buyer_capsuleer_id=identity.CapsuleerId(value=buyer_capsuleer_id),
            buyer_wallet_id=identity.WalletId(value=buyer_wallet_id),
            destination_item_stack_id=identity.ItemStackId(
                value=destination_item_stack_id
            ),
        ),
        terms=proto.settle.SettleTradeInstanceTerms(
            quantity=proto.quantity.ItemQuantity(units=quantity),
            unit_price_isk=proto.money.IskAmount(minor_units=unit_price_minor),
            total_price_isk=proto.money.IskAmount(minor_units=total_price_minor),
            requested_at_unix_millis=now_millis(),
        ),
        accepted_trade=accepted,
    )
    return proto.settlement_command.TradeSettlementCommand(
        metadata=metadata,
        operation_kind=proto.operation_kind.TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE,
        settle_trade_instance=settle,
    )


def cancel_command(
    proto,
    ids: TradeScenarioIds,
    world,
    *,
    requesting_capsuleer_id: int | None = None,
    metadata=None,
):
    identity = proto.identity
    requesting_capsuleer_id = requesting_capsuleer_id or world.issuer_id
    metadata = metadata or operation_metadata(
        proto,
        caused_by_capsuleer_id=requesting_capsuleer_id,
        purpose="cancel-trade-instance",
    )
    cancel = proto.cancel.CancelTradeInstanceCommand(
        metadata=metadata,
        row_ids=proto.cancel.CancelTradeInstanceRowIds(
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            requesting_capsuleer_id=identity.CapsuleerId(value=requesting_capsuleer_id),
        ),
        reason="e2e cancellation",
    )
    return proto.settlement_command.TradeSettlementCommand(
        metadata=metadata,
        operation_kind=proto.operation_kind.TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE,
        cancel_trade_instance=cancel,
    )


def expire_command(
    proto,
    ids: TradeScenarioIds,
    world,
    *,
    evaluated_at_unix_millis: int | None = None,
    metadata=None,
):
    identity = proto.identity
    metadata = metadata or operation_metadata(
        proto,
        caused_by_capsuleer_id=world.issuer_id,
        purpose="expire-trade-instance",
    )
    expire = proto.expire.ExpireTradeInstanceCommand(
        metadata=metadata,
        row_ids=proto.expire.ExpireTradeInstanceRowIds(
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
        ),
        evaluated_at_unix_millis=evaluated_at_unix_millis or now_millis(),
    )
    return proto.settlement_command.TradeSettlementCommand(
        metadata=metadata,
        operation_kind=proto.operation_kind.TRADE_OPERATION_KIND_EXPIRE_TRADE_INSTANCE,
        expire_trade_instance=expire,
    )


def project_trade_interaction(proto, world, *, selected_quantity: int = 5):
    identity = proto.identity
    return proto.project_interaction.ProjectTradeInteraction(
        interaction_id=identity.ProjectTradeInteractionId(value=new_uuid()),
        source_activity_id=identity.GameUiActivityId(value=new_uuid()),
        correlation_id=identity.CorrelationId(value=f"e2e-{new_uuid()}"),
        trace_id=identity.TraceId(value=f"e2e-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.issuer_id),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        interaction_kind=proto.project_interaction.PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE,
        trade_window=proto.project_interaction.KNOWN_TRADE_WINDOW_MARKET_WINDOW,
        trade_button=proto.project_interaction.KNOWN_TRADE_BUTTON_ISSUE,
        visible_trade_context=proto.project_interaction.VisibleTradeContext(
            station_id=identity.StationId(value=world.station_id),
            region_id=identity.RegionId(value=world.region_id),
            trade_instance_id=identity.TradeInstanceId(value=""),
            wallet_id=identity.WalletId(value=world.issuer_wallet_id),
            source_item_stack_id=identity.ItemStackId(value=world.issuer_item_stack_id),
        ),
        selected_items=[
            proto.project_interaction.PlayerSelectedItem(
                item_type_id=identity.ItemTypeId(value=world.item_type_id),
                item_stack_id=identity.ItemStackId(value=world.issuer_item_stack_id),
                quantity=proto.quantity.ItemQuantity(units=selected_quantity),
            )
        ],
        typed_values=proto.project_interaction.PlayerTypedTradeValues(
            quantity=proto.quantity.ItemQuantity(units=selected_quantity),
            unit_price_isk=proto.money.IskAmount(minor_units=10_000),
            total_price_isk=proto.money.IskAmount(
                minor_units=selected_quantity * 10_000
            ),
        ),
        occurred_at_unix_millis=now_millis(),
    )


def project_accept_interaction(
    proto,
    world,
    ids: TradeScenarioIds,
    *,
    selected_quantity: int = 5,
    unit_price_minor: int = 10_000,
):
    identity = proto.identity
    return proto.project_interaction.ProjectTradeInteraction(
        interaction_id=identity.ProjectTradeInteractionId(value=new_uuid()),
        source_activity_id=identity.GameUiActivityId(value=new_uuid()),
        correlation_id=identity.CorrelationId(value=f"e2e-{new_uuid()}"),
        trace_id=identity.TraceId(value=f"e2e-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.buyer_id),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        interaction_kind=proto.project_interaction.PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE,
        trade_window=proto.project_interaction.KNOWN_TRADE_WINDOW_MARKET_WINDOW,
        trade_button=proto.project_interaction.KNOWN_TRADE_BUTTON_ACCEPT,
        visible_trade_context=proto.project_interaction.VisibleTradeContext(
            station_id=identity.StationId(value=world.station_id),
            region_id=identity.RegionId(value=world.region_id),
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            wallet_id=identity.WalletId(value=ids.buyer_wallet_id),
            source_item_stack_id=identity.ItemStackId(value=ids.issuer_item_stack_id),
            destination_item_stack_id=identity.ItemStackId(
                value=ids.buyer_destination_stack_id
            ),
        ),
        typed_values=proto.project_interaction.PlayerTypedTradeValues(
            quantity=proto.quantity.ItemQuantity(units=selected_quantity),
            unit_price_isk=proto.money.IskAmount(minor_units=unit_price_minor),
            total_price_isk=proto.money.IskAmount(
                minor_units=selected_quantity * unit_price_minor
            ),
        ),
        occurred_at_unix_millis=now_millis(),
    )


def project_cancel_interaction(proto, world, ids: TradeScenarioIds):
    identity = proto.identity
    return proto.project_interaction.ProjectTradeInteraction(
        interaction_id=identity.ProjectTradeInteractionId(value=new_uuid()),
        source_activity_id=identity.GameUiActivityId(value=new_uuid()),
        correlation_id=identity.CorrelationId(value=f"e2e-{new_uuid()}"),
        trace_id=identity.TraceId(value=f"e2e-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.issuer_id),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        interaction_kind=proto.project_interaction.PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE,
        trade_window=proto.project_interaction.KNOWN_TRADE_WINDOW_MARKET_WINDOW,
        trade_button=proto.project_interaction.KNOWN_TRADE_BUTTON_CANCEL,
        visible_trade_context=proto.project_interaction.VisibleTradeContext(
            station_id=identity.StationId(value=world.station_id),
            region_id=identity.RegionId(value=world.region_id),
            trade_instance_id=identity.TradeInstanceId(value=ids.trade_instance_id),
            wallet_id=identity.WalletId(value=ids.issuer_wallet_id),
            source_item_stack_id=identity.ItemStackId(value=ids.issuer_item_stack_id),
        ),
        occurred_at_unix_millis=now_millis(),
    )


def game_trade_ui_activity(proto, world, *, selected_quantity: int = 5):
    identity = proto.identity
    fields = [
        ("station_id", str(world.station_id)),
        ("region_id", str(world.region_id)),
        ("wallet_id", world.issuer_wallet_id),
        ("item_stack_id", world.issuer_item_stack_id),
        ("item_type_id", str(world.item_type_id)),
        ("quantity", str(selected_quantity)),
        ("unit_price_isk", "10000"),
        ("total_price_isk", str(selected_quantity * 10_000)),
    ]
    return proto.gateway_activity.GameTradeUiActivity(
        activity_id=identity.GameUiActivityId(value=new_uuid()),
        game_server_id=identity.GameServerId(value=f"e2e-server-{new_uuid()}"),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.issuer_id),
        game_ui_version=identity.GameUiVersion(value="e2e-ui-v1"),
        activity_kind=proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_KIND_ISSUE_BUTTON_PRESSED,
        raw_game_screen_name="market-window",
        raw_game_button_name="issue",
        visible_fields=[
            proto.gateway_activity.GameTradeUiField(
                raw_game_field_name=name,
                raw_game_field_value=value,
            )
            for name, value in fields
        ],
        occurred_at_unix_millis=now_millis(),
    )


def game_accept_ui_activity(
    proto,
    world,
    ids: TradeScenarioIds,
    *,
    selected_quantity: int = 5,
    unit_price_minor: int = 10_000,
):
    fields = [
        ("trade_instance_id", ids.trade_instance_id),
        ("station_id", str(world.station_id)),
        ("region_id", str(world.region_id)),
        ("wallet_id", ids.buyer_wallet_id),
        ("destination_item_stack_id", ids.buyer_destination_stack_id),
        ("quantity", str(selected_quantity)),
        ("unit_price_isk", str(unit_price_minor)),
        ("total_price_isk", str(selected_quantity * unit_price_minor)),
    ]
    identity = proto.identity
    return proto.gateway_activity.GameTradeUiActivity(
        activity_id=identity.GameUiActivityId(value=new_uuid()),
        game_server_id=identity.GameServerId(value=f"e2e-server-{new_uuid()}"),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.buyer_id),
        game_ui_version=identity.GameUiVersion(value="e2e-ui-v1"),
        activity_kind=proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_KIND_ACCEPT_BUTTON_PRESSED,
        raw_game_screen_name="market-window",
        raw_game_button_name="accept",
        visible_fields=[
            proto.gateway_activity.GameTradeUiField(
                raw_game_field_name=name,
                raw_game_field_value=value,
            )
            for name, value in fields
        ],
        occurred_at_unix_millis=now_millis(),
    )


def game_cancel_ui_activity(proto, world, ids: TradeScenarioIds):
    fields = [
        ("trade_instance_id", ids.trade_instance_id),
        ("station_id", str(world.station_id)),
        ("region_id", str(world.region_id)),
        ("wallet_id", ids.issuer_wallet_id),
        ("item_stack_id", ids.issuer_item_stack_id),
    ]
    identity = proto.identity
    return proto.gateway_activity.GameTradeUiActivity(
        activity_id=identity.GameUiActivityId(value=new_uuid()),
        game_server_id=identity.GameServerId(value=f"e2e-server-{new_uuid()}"),
        game_session_id=identity.GameSessionId(value=f"e2e-session-{new_uuid()}"),
        capsuleer_id=identity.CapsuleerId(value=world.issuer_id),
        game_ui_version=identity.GameUiVersion(value="e2e-ui-v1"),
        activity_kind=proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_KIND_CANCEL_BUTTON_PRESSED,
        raw_game_screen_name="market-window",
        raw_game_button_name="cancel",
        visible_fields=[
            proto.gateway_activity.GameTradeUiField(
                raw_game_field_name=name,
                raw_game_field_value=value,
            )
            for name, value in fields
        ],
        occurred_at_unix_millis=now_millis(),
    )
