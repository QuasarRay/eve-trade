from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .paths import PROTO_ROOT


@dataclass(frozen=True)
class ProtoModules:
    identity: ModuleType
    money: ModuleType
    quantity: ModuleType
    errors: ModuleType
    idempotency: ModuleType
    metadata: ModuleType
    trade_state: ModuleType
    trade_instance: ModuleType
    trade_transaction: ModuleType
    trade_escrow: ModuleType
    trade_claim: ModuleType
    operation_kind: ModuleType
    issue: ModuleType
    accept: ModuleType
    cancel: ModuleType
    expire: ModuleType
    settle: ModuleType
    gateway_activity: ModuleType
    gateway_service: ModuleType
    gateway_service_grpc: ModuleType
    project_interaction: ModuleType
    market_decision: ModuleType
    market_service: ModuleType
    market_service_grpc: ModuleType
    settlement_command: ModuleType
    settlement_result: ModuleType
    settlement_service: ModuleType
    settlement_service_grpc: ModuleType


def compile_proto_stubs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    proto_files = sorted(PROTO_ROOT.rglob("*.proto"))
    if not proto_files:
        raise AssertionError(f"No proto files found under {PROTO_ROOT}")

    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_ROOT}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        *[str(path) for path in proto_files],
    ]
    result = subprocess.run(
        command,
        cwd=PROTO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Python protobuf generation failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def load_proto_modules(generated_dir: Path) -> ProtoModules:
    generated = str(generated_dir)
    if generated not in sys.path:
        sys.path.insert(0, generated)

    def load(name: str) -> ModuleType:
        return importlib.import_module(name)

    return ProtoModules(
        identity=load("eve_trade.common.v1.identity_pb2"),
        money=load("eve_trade.common.v1.money_pb2"),
        quantity=load("eve_trade.common.v1.quantity_pb2"),
        errors=load("eve_trade.common.v1.errors_pb2"),
        idempotency=load("eve_trade.common.v1.idempotency_pb2"),
        metadata=load("eve_trade.common.v1.operation_metadata_pb2"),
        trade_state=load("eve_trade.domain.trade.v1.trade_state_pb2"),
        trade_instance=load("eve_trade.domain.trade.v1.trade_instance_pb2"),
        trade_transaction=load("eve_trade.domain.trade.v1.trade_transaction_pb2"),
        trade_escrow=load("eve_trade.domain.trade.v1.trade_escrow_pb2"),
        trade_claim=load("eve_trade.domain.trade.v1.trade_claim_pb2"),
        operation_kind=load("eve_trade.operation.v1.trade_operation_kind_pb2"),
        issue=load("eve_trade.operation.v1.issue_trade_instance_pb2"),
        accept=load("eve_trade.operation.v1.accept_trade_instance_pb2"),
        cancel=load("eve_trade.operation.v1.cancel_trade_instance_pb2"),
        expire=load("eve_trade.operation.v1.expire_trade_instance_pb2"),
        settle=load("eve_trade.operation.v1.settle_trade_instance_pb2"),
        gateway_activity=load("eve_trade.gateway.v1.game_ui_activity_pb2"),
        gateway_service=load("eve_trade.gateway.v1.game_trade_gateway_service_pb2"),
        gateway_service_grpc=load(
            "eve_trade.gateway.v1.game_trade_gateway_service_pb2_grpc"
        ),
        project_interaction=load("eve_trade.market.v1.project_trade_interaction_pb2"),
        market_decision=load("eve_trade.market.v1.trade_decision_pb2"),
        market_service=load("eve_trade.market.v1.market_trade_service_pb2"),
        market_service_grpc=load("eve_trade.market.v1.market_trade_service_pb2_grpc"),
        settlement_command=load("eve_trade.settlement.v1.settlement_command_pb2"),
        settlement_result=load("eve_trade.settlement.v1.settlement_result_pb2"),
        settlement_service=load(
            "eve_trade.settlement.v1.trade_settlement_service_pb2"
        ),
        settlement_service_grpc=load(
            "eve_trade.settlement.v1.trade_settlement_service_pb2_grpc"
        ),
    )
