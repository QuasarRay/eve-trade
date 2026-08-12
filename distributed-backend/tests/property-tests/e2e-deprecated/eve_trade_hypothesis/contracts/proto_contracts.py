from __future__ import annotations

"""Exact Buf/Protobuf semantic bindings backed by executable canaries."""

import ast
import importlib.util
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


@dataclass(frozen=True)
class ProtoContractSpec:
    oracle: str
    canary_case: str | None = None
    capabilities: tuple[str, ...] = ("buf", "structured_proto")


PROTO_CONTRACTS: dict[str, ProtoContractSpec] = {
    "test_buf_breaking_detects_changed_field_number": ProtoContractSpec(
        "breaking_canary", "changed_field_number"
    ),
    "test_buf_breaking_detects_incompatible_field_type_change": ProtoContractSpec(
        "breaking_canary", "incompatible_field_type"
    ),
    "test_buf_breaking_detects_removed_enum_value": ProtoContractSpec(
        "breaking_canary", "removed_enum_value"
    ),
    "test_buf_breaking_detects_removed_field_from_main_branch_contract": ProtoContractSpec(
        "breaking_canary", "removed_field"
    ),
    "test_buf_generate_produces_no_diff_in_checked_in_generated_sources": ProtoContractSpec(
        "generated_go_equivalence",
        capabilities=("buf_generate", "git_diff", "go_protobuf"),
    ),
    "test_generated_go_proto_sources_match_current_proto_descriptors": ProtoContractSpec(
        "generated_go_equivalence",
        capabilities=("buf_generate", "git_diff", "go_protobuf"),
    ),
    "test_protovalidate_rules_are_present_on_every_required_business_identifier": ProtoContractSpec(
        "required_identifier_rules",
        capabilities=("grpc_tools_protoc", "protobuf_descriptor", "protovalidate_extensions"),
    ),
    "test_protovalidate_rules_require_positive_trade_quantity": ProtoContractSpec(
        "positive_trade_quantity",
        capabilities=("grpc_tools_protoc", "protobuf_descriptor", "protovalidate_extensions"),
    ),
    "test_protovalidate_rules_require_nonnegative_or_positive_isk_fields_according_to_domain_contract": ProtoContractSpec(
        "isk_domain_rules",
        capabilities=("grpc_tools_protoc", "protobuf_descriptor", "protovalidate_extensions"),
    ),
}


REQUIRED_IDENTIFIER_RULES: Mapping[str, str] = {
    # Edge identity and correlation boundaries.
    "eve.api_gateway.v1.UdpEdgeAuth.key_id": "non_blank_string",
    "eve.api_gateway.v1.AuthenticatedTradeGuiActor.authenticated_capsuleer_id": "positive_int64",
    "eve.api_gateway.v1.MarketResponseIdentity.request_interaction_id": "non_blank_string",
    "eve.api_gateway.v1.MarketResponseIdentity.response_interaction_id": "non_blank_string",
    # Market public requests and their authoritative item snapshot.
    "eve.market.v1.ItemStackRow.item_stack_id": "uuid_string",
    "eve.market.v1.ItemStackRow.owner_id": "positive_int64",
    "eve.market.v1.ItemStackRow.item_type_id": "positive_int64",
    "eve.market.v1.ItemStackRow.station_id": "positive_int64",
    "eve.market.v1.IssueTradeInstanceRequest.idempotency_key": "non_blank_string",
    "eve.market.v1.IssueTradeInstanceRequest.issued_by_capsuleer_id": "positive_int64",
    "eve.market.v1.AcceptTradeInstanceRequest.idempotency_key": "non_blank_string",
    "eve.market.v1.AcceptTradeInstanceRequest.trade_instance_id": "uuid_string",
    "eve.market.v1.AcceptTradeInstanceRequest.buyer_capsuleer_id": "positive_int64",
    "eve.market.v1.AcceptTradeInstanceRequest.buyer_wallet_id": "uuid_string",
    "eve.market.v1.AcceptTradeInstanceRequest.buyer_destination_item_stack_id": "optional_uuid_string",
    "eve.market.v1.CancelTradeInstanceRequest.idempotency_key": "non_blank_string",
    "eve.market.v1.CancelTradeInstanceRequest.trade_instance_id": "uuid_string",
    "eve.market.v1.CancelTradeInstanceRequest.cancelled_by_capsuleer_id": "positive_int64",
    "eve.market.v1.TradeGuiInteraction.interaction_id": "non_blank_string",
    "eve.market.v1.TradeGuiInput.trade_instance_id": "optional_uuid_string",
    "eve.market.v1.TradeGuiInput.buyer_wallet_id": "optional_uuid_string",
    "eve.market.v1.TradeGuiInput.buyer_destination_item_stack_id": "optional_uuid_string",
    "eve.market.v1.TradeGuiInput.seller_wallet_id": "optional_uuid_string",
    # Market-to-settlement planning contract.
    "eve.trade.v1.ItemStackSnapshot.item_stack_id": "uuid_string",
    "eve.trade.v1.ItemStackSnapshot.owner_id": "positive_int64",
    "eve.trade.v1.ItemStackSnapshot.item_type_id": "positive_int64",
    "eve.trade.v1.ItemStackSnapshot.station_id": "positive_int64",
    "eve.trade.v1.IssueTradeInstanceInput.idempotency_key": "non_blank_string",
    "eve.trade.v1.IssueTradeInstanceInput.issued_by_capsuleer_id": "positive_int64",
    "eve.trade.v1.IssueTradeInstanceInput.trade_instance_id": "optional_uuid_string",
    "eve.trade.v1.IssueTradeInstanceInput.item_stack_escrow_id": "optional_uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.idempotency_key": "non_blank_string",
    "eve.trade.v1.AcceptTradeInstanceInput.trade_instance_id": "uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.buyer_capsuleer_id": "positive_int64",
    "eve.trade.v1.AcceptTradeInstanceInput.seller_capsuleer_id": "positive_int64",
    "eve.trade.v1.AcceptTradeInstanceInput.item_type_id": "positive_int64",
    "eve.trade.v1.AcceptTradeInstanceInput.station_id": "positive_int64",
    "eve.trade.v1.AcceptTradeInstanceInput.buyer_wallet_id": "uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.seller_wallet_id": "uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.item_stack_escrow_id": "uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.buyer_destination_item_stack_id": "optional_uuid_string",
    "eve.trade.v1.AcceptTradeInstanceInput.wallet_escrow_id": "optional_uuid_string",
    "eve.trade.v1.CancelTradeInstanceInput.idempotency_key": "non_blank_string",
    "eve.trade.v1.CancelTradeInstanceInput.trade_instance_id": "uuid_string",
    "eve.trade.v1.CancelTradeInstanceInput.cancelled_by_capsuleer_id": "positive_int64",
    "eve.trade.v1.CancelTradeInstanceInput.item_stack_escrow_id": "optional_uuid_string",
    "eve.trade.v1.CancelTradeInstanceInput.return_item_stack_id": "optional_uuid_string",
    "eve.trade.v1.CancelTradeInstanceInput.wallet_escrow_id": "optional_uuid_string",
    "eve.trade.v1.CancelTradeInstanceInput.return_wallet_id": "optional_uuid_string",
    # Worker/Rust command boundary and every operation-owned entity identity.
    "eve.trade_settlement.v1.ExecuteSettlementBatchRequest.idempotency_key": "non_blank_string",
    "eve.trade_settlement.v1.ExecuteSettlementBatchRequest.caused_by_capsuleer_id": "positive_int64",
    "eve.trade_settlement.v1.ExecuteSettlementBatchRequest.created_by_service": "non_blank_string",
    "eve.trade_settlement.v1.ExecuteSettlementBatchRequest.request_id": "optional_uuid_string",
    "eve.trade_settlement.v1.QueueSettlementOperationRequest.idempotency_key": "non_blank_string",
    "eve.trade_settlement.v1.QueueSettlementOperationRequest.caused_by_capsuleer_id": "positive_int64",
    "eve.trade_settlement.v1.GetSettlementOperationRequest.operation_id": "uuid_string",
    "eve.trade_settlement.v1.UpdateSettlementOperationRequest.operation_id": "uuid_string",
    "eve.trade_settlement.v1.UpdateSettlementOperationRequest.settlement_batch_id": "optional_uuid_string",
    "eve.trade_settlement.v1.ClaimSettlementOutboxRequest.worker_id": "non_blank_string",
    "eve.trade_settlement.v1.CompleteSettlementOutboxRequest.operation_id": "uuid_string",
    "eve.trade_settlement.v1.CompleteSettlementOutboxRequest.worker_id": "non_blank_string",
    "eve.trade_settlement.v1.CompleteSettlementOutboxRequest.message_id": "non_blank_string",
    "eve.trade_settlement.v1.ReleaseSettlementOutboxRequest.operation_id": "uuid_string",
    "eve.trade_settlement.v1.ReleaseSettlementOutboxRequest.worker_id": "non_blank_string",
    "eve.trade_settlement.v1.EntityReference.entity_id": "uuid_string",
    "eve.trade_settlement.v1.CreateNewTradeInstanceRow.trade_instance_id": "optional_uuid_string",
    "eve.trade_settlement.v1.CreateNewTradeInstanceRow.issuer_id": "positive_int64",
    "eve.trade_settlement.v1.CreateNewTradeInstanceRow.item_type_id": "positive_int64",
    "eve.trade_settlement.v1.CreateNewTradeInstanceRow.station_id": "positive_int64",
    "eve.trade_settlement.v1.ModifyTradeInstanceState.trade_instance_id": "uuid_string",
    "eve.trade_settlement.v1.ModifyTradeInstanceState.changed_by_service": "non_blank_string",
    "eve.trade_settlement.v1.CreateNewEmptyItemStack.item_stack_id": "optional_uuid_string",
    "eve.trade_settlement.v1.CreateNewEmptyItemStack.owner_id": "positive_int64",
    "eve.trade_settlement.v1.CreateNewEmptyItemStack.item_type_id": "positive_int64",
    "eve.trade_settlement.v1.CreateNewEmptyItemStack.station_id": "positive_int64",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackToItemStackEscrow.source_item_stack_id": "uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackToItemStackEscrow.item_stack_escrow_id": "optional_uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackToItemStackEscrow.trade_instance_id": "uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner.item_stack_escrow_id": "uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner.destination_item_stack_id": "uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner.item_stack_escrow_id": "uuid_string",
    "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner.destination_item_stack_id": "uuid_string",
    "eve.trade_settlement.v1.MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner.source_item_stack_id": "uuid_string",
    "eve.trade_settlement.v1.MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner.destination_item_stack_id": "uuid_string",
    "eve.trade_settlement.v1.CreateNewEmptyWalletEscrow.wallet_escrow_id": "optional_uuid_string",
    "eve.trade_settlement.v1.CreateNewEmptyWalletEscrow.trade_instance_id": "uuid_string",
    "eve.trade_settlement.v1.CreateNewEmptyWalletEscrow.owner_id": "positive_int64",
    "eve.trade_settlement.v1.CreateNewEmptyWalletEscrow.source_wallet_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletToWalletEscrow.source_wallet_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletToWalletEscrow.wallet_escrow_id": "optional_uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletToWalletEscrow.trade_instance_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithNewOwner.wallet_escrow_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithNewOwner.destination_wallet_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner.wallet_escrow_id": "uuid_string",
    "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner.destination_wallet_id": "uuid_string",
}


POSITIVE_TRADE_QUANTITY_FIELDS = frozenset(
    {
        "eve.market.v1.IssueTradeInstanceRequest.quantity",
        "eve.market.v1.AcceptTradeInstanceRequest.quantity_requested",
        "eve.trade.v1.IssueTradeInstanceInput.quantity",
        "eve.trade.v1.AcceptTradeInstanceInput.quantity_requested",
        "eve.trade_settlement.v1.CreateNewTradeInstanceRow.total_quantity",
        "eve.trade_settlement.v1.TransferQuantityFromItemStackToItemStackEscrow.quantity",
        "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner.quantity",
        "eve.trade_settlement.v1.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner.quantity",
    }
)


POSITIVE_ISK_FIELDS = frozenset(
    {
        "eve.market.v1.IssueTradeInstanceRequest.unit_price_isk",
        "eve.trade.v1.IssueTradeInstanceInput.unit_price_isk",
        "eve.trade.v1.AcceptTradeInstanceInput.isk_amount_paid",
        "eve.trade_settlement.v1.CreateNewTradeInstanceRow.unit_price_isk",
        "eve.trade_settlement.v1.TransferIskAmountFromWalletToWalletEscrow.isk_amount",
        "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithNewOwner.isk_amount",
        "eve.trade_settlement.v1.TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner.isk_amount",
    }
)


NONNEGATIVE_ISK_FIELDS = frozenset(
    {
        "eve.market.v1.TradeGuiInput.unit_price_isk",
        "eve.trade.v1.CancelTradeInstanceInput.return_isk_amount",
    }
)


@dataclass(frozen=True)
class FieldRuleObservation:
    rules: frozenset[str]
    int64_gte: int | None


def binding_metadata(spec: ProtoContractSpec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "family": "protobuf_buf",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }
    if spec.canary_case is not None:
        result["parameters"] = {"canary_case": spec.canary_case}
    return result


def _load_script(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("eve_trade_buf_breaking_canaries", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dagger_strings(root: Path) -> frozenset[str]:
    path = root / ".github" / "dagger" / "proto.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


@lru_cache(maxsize=4)
def _execute_canaries(script_text: str) -> tuple[dict[str, object], ...]:
    module = _load_script(Path(script_text))
    return module.verify_breaking_canaries()


def _add_descriptor_files(pool: descriptor_pool.DescriptorPool, document: descriptor_pb2.FileDescriptorSet) -> None:
    pending = list(document.file)
    last_errors: dict[str, Exception] = {}
    while pending:
        progressed = False
        for file_descriptor in list(pending):
            try:
                pool.Add(file_descriptor)
            except Exception as exc:  # dependency may not have been added yet
                last_errors[file_descriptor.name] = exc
                continue
            pending.remove(file_descriptor)
            progressed = True
        if not progressed:
            details = "; ".join(f"{name}: {error}" for name, error in sorted(last_errors.items()))
            raise AssertionError(f"could not load compiled descriptor graph: {details}")


def _walk_messages(
    messages: Any,
    prefix: str,
):
    for message in messages:
        full_name = f"{prefix}.{message.name}" if prefix else message.name
        yield full_name, message
        yield from _walk_messages(message.nested_type, full_name)


@lru_cache(maxsize=8)
def _compile_field_rules(root_text: str) -> Mapping[str, FieldRuleObservation]:
    root = Path(root_text)
    proto_root = root / "proto"
    sources = tuple(
        sorted(
            str(path.relative_to(proto_root)).replace("\\", "/")
            for path in (proto_root / "eve").rglob("*.proto")
        )
    )
    assert sources, "no EVE protobuf sources found"
    with tempfile.TemporaryDirectory(prefix="eve-trade-proto-rules-") as temporary:
        descriptor_path = Path(temporary) / "rules.pb"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                "-I",
                str(proto_root),
                f"--descriptor_set_out={descriptor_path}",
                "--include_imports",
                *sources,
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        document = descriptor_pb2.FileDescriptorSet.FromString(descriptor_path.read_bytes())

    pool = descriptor_pool.DescriptorPool()
    _add_descriptor_files(pool, document)
    field_extension = pool.FindExtensionByName("buf.validate.field")
    options_class = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("google.protobuf.FieldOptions")
    )
    custom_extensions = {
        name: pool.FindExtensionByName(f"eve.validation.v1.{name}")
        for name in (
            "non_blank_string",
            "uuid_string",
            "optional_uuid_string",
            "positive_int64",
        )
    }
    observations: dict[str, FieldRuleObservation] = {}
    for file_descriptor in document.file:
        if not file_descriptor.package.startswith("eve.") or file_descriptor.package == "eve.validation.v1":
            continue
        for message_name, message in _walk_messages(file_descriptor.message_type, file_descriptor.package):
            for field in message.field:
                field_name = f"{message_name}.{field.name}"
                options = options_class.FromString(field.options.SerializeToString())
                rules: set[str] = set()
                int64_gte: int | None = None
                if options.HasExtension(field_extension):
                    constraints = options.Extensions[field_extension]
                    for rule_name, extension in custom_extensions.items():
                        if extension.containing_type.full_name == "buf.validate.StringRules":
                            target = constraints.string
                        elif extension.containing_type.full_name == "buf.validate.Int64Rules":
                            target = constraints.int64
                        else:
                            raise AssertionError(
                                f"unexpected custom validation extension target: {extension.containing_type.full_name}"
                            )
                        if target.HasExtension(extension) and target.Extensions[extension] is True:
                            rules.add(rule_name)
                    if constraints.HasField("int64") and constraints.int64.HasField("gte"):
                        int64_gte = int(constraints.int64.gte)
                observations[field_name] = FieldRuleObservation(
                    rules=frozenset(rules),
                    int64_gte=int64_gte,
                )
    return observations


def _assert_custom_rule(
    observations: Mapping[str, FieldRuleObservation],
    field_name: str,
    rule: str,
) -> None:
    assert field_name in observations, f"reviewed protobuf field disappeared: {field_name}"
    assert rule in observations[field_name].rules, (
        f"{field_name} must retain Protovalidate rule {rule}; "
        f"observed {sorted(observations[field_name].rules)}"
    )


def validate_proto_contract(
    name: str,
    root: Path,
    *,
    execute_buf: bool = True,
) -> None:
    assert name in PROTO_CONTRACTS, f"unknown Protobuf semantic contract: {name}"
    spec = PROTO_CONTRACTS[name]
    if spec.oracle == "breaking_canary":
        commands = _dagger_strings(root)
        assert "python3 scripts/verify_buf_breaking_canaries.py" in commands
        assert "buf breaking --against '.git#branch=main'" in commands
        module = _load_script(root / "scripts" / "verify_buf_breaking_canaries.py")
        assert set(module.BREAKING_CASES) == {
            "changed_field_number",
            "incompatible_field_type",
            "removed_enum_value",
            "removed_field",
        }
        assert spec.canary_case in module.BREAKING_CASES
        baseline = module.BASELINE_PROTO
        candidate = module.BREAKING_CASES[spec.canary_case]
        assert candidate != baseline
        if spec.canary_case == "changed_field_number":
            assert "trade_id = 1" in baseline and "trade_id = 3" in candidate
        elif spec.canary_case == "incompatible_field_type":
            assert "int64 quantity = 2" in baseline and "string quantity = 2" in candidate
        elif spec.canary_case == "removed_enum_value":
            assert "TRADE_STATE_OPEN = 1" in baseline and "TRADE_STATE_OPEN = 1" not in candidate
        elif spec.canary_case == "removed_field":
            assert "string trade_id = 1" in baseline and "string trade_id = 1" not in candidate
        if execute_buf:
            observations = _execute_canaries(
                str(root / "scripts" / "verify_buf_breaking_canaries.py")
            )
            by_case = {str(item["case"]): item for item in observations}
            assert spec.canary_case in by_case
            assert int(by_case[spec.canary_case]["candidate_build_exit_code"]) == 0
            assert int(by_case[spec.canary_case]["breaking_exit_code"]) != 0
        return

    if spec.oracle == "generated_go_equivalence":
        commands = _dagger_strings(root)
        assert "buf generate" in commands
        assert "git diff --exit-code -- proto/gen" in commands
        assert (root / "buf.gen.yaml").is_file()
        generated = tuple(sorted((root / "proto" / "gen").rglob("*.pb.go")))
        sources = tuple(
            sorted(
                path
                for path in (root / "proto" / "eve").rglob("*.proto")
                if "validation" not in path.parts
            )
        )
        assert generated and sources
        generated_stems = {path.name.removesuffix(".pb.go") for path in generated}
        source_stems = {path.stem for path in sources}
        assert source_stems <= generated_stems, (source_stems, generated_stems)
        return

    if spec.oracle == "required_identifier_rules":
        observations = _compile_field_rules(str(root))
        assert len(REQUIRED_IDENTIFIER_RULES) >= 80, "reviewed identifier matrix unexpectedly shrank"
        for field_name, rule in REQUIRED_IDENTIFIER_RULES.items():
            _assert_custom_rule(observations, field_name, rule)
        return

    if spec.oracle == "positive_trade_quantity":
        observations = _compile_field_rules(str(root))
        assert len(POSITIVE_TRADE_QUANTITY_FIELDS) == 8
        for field_name in POSITIVE_TRADE_QUANTITY_FIELDS:
            _assert_custom_rule(observations, field_name, "positive_int64")
        return

    if spec.oracle == "isk_domain_rules":
        observations = _compile_field_rules(str(root))
        assert len(POSITIVE_ISK_FIELDS) == 7 and len(NONNEGATIVE_ISK_FIELDS) == 2
        for field_name in POSITIVE_ISK_FIELDS:
            _assert_custom_rule(observations, field_name, "positive_int64")
        for field_name in NONNEGATIVE_ISK_FIELDS:
            assert field_name in observations, f"reviewed protobuf field disappeared: {field_name}"
            assert observations[field_name].int64_gte == 0, (
                f"{field_name} must remain nonnegative; observed gte={observations[field_name].int64_gte}"
            )
        return

    raise AssertionError(f"unhandled Protobuf oracle: {spec.oracle}")
