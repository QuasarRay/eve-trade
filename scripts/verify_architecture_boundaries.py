#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REMOVED_PUBLIC_RPCS = (
    "IssueTradeInstance",
    "AcceptTradeInstance",
    "CancelTradeInstance",
)
GATEWAY_MARKET_FORBIDDEN = (
    "source_transport",
    "source_address",
)
SIMULATOR_FORBIDDEN_TERMS = (
    "django",
    "rest",
    "framework",
    "simulator",
    "test",
    "debug",
    "environment",
    "browser",
    "source",
    "source_transport",
    "source_address",
)
CANONICAL_PATH = (
    "game frontend -> Quilkin UDP -> API gateway UDP edge -> "
    "Market GUI interaction -> settlement operations -> trade-settlement"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def iter_files(base: Path, pattern: str) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob(pattern) if path.is_file())


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_protos(errors: list[str]) -> None:
    proto_root = ROOT / "distributed-backend" / "proto" / "eve"
    proto_files = iter_files(proto_root, "*.proto")
    for path in proto_files:
        text = read(path)
        for rpc in REMOVED_PUBLIC_RPCS:
            if re.search(rf"\brpc\s+{re.escape(rpc)}\b", text):
                fail(errors, f"{path.relative_to(ROOT)} still exposes rpc {rpc}")

    market_proto = read(ROOT / "distributed-backend" / "proto" / "eve" / "market" / "v1" / "market.proto")
    if "rpc SubmitTradeGuiInteraction" not in market_proto:
        fail(errors, "Market production proto must expose SubmitTradeGuiInteraction")
    request_match = re.search(
        r"message\s+SubmitTradeGuiInteractionRequest\s*\{(?P<body>.*?)\n\}",
        market_proto,
        re.DOTALL,
    )
    if not request_match:
        fail(errors, "SubmitTradeGuiInteractionRequest is missing")
        return
    body = request_match.group("body")
    if "bytes raw_payload = 1;" not in body:
        fail(errors, "SubmitTradeGuiInteractionRequest must contain bytes raw_payload = 1")
    for forbidden in GATEWAY_MARKET_FORBIDDEN:
        if forbidden in body:
            fail(errors, f"SubmitTradeGuiInteractionRequest must not contain {forbidden}")


def check_api_gateway(errors: list[str]) -> None:
    api_gateway_root = ROOT / "distributed-backend" / "src" / "api-gateway"
    for path in iter_files(api_gateway_root, "*.go"):
        text = read(path)
        for forbidden in GATEWAY_MARKET_FORBIDDEN:
            if forbidden in text:
                fail(errors, f"{path.relative_to(ROOT)} contains forbidden Market metadata field {forbidden}")
    if (api_gateway_root / "distributed-backend" / "handler.go").exists():
        fail(errors, "API gateway handler.go still exists; direct trade command RPC handler must be deleted")


def check_simulator_packet_test(errors: list[str]) -> None:
    test_path = ROOT / "simulator" / "trade_gui" / "tests.py"
    if not test_path.exists():
        fail(errors, "simulator/trade_gui/tests.py is missing the outbound packet boundary test")
        return
    text = read(test_path)
    required = (
        "test_button_press_sends_production_identical_signed_game_packet",
        "CapturingSocket",
        "trade_gui.udp_client.socket.socket",
        "FORBIDDEN_PACKET_TERMS",
    )
    for marker in required:
        if marker not in text:
            fail(errors, f"simulator packet test is missing marker {marker}")
    for term in SIMULATOR_FORBIDDEN_TERMS:
        if f'"{term}"' not in text:
            fail(errors, f"simulator packet boundary test does not assert forbidden term {term}")


def check_docs(errors: list[str]) -> None:
    docs = [
        ROOT / "README.md",
        *iter_files(ROOT / "Architecture" / "ISO-42010", "*.md"),
    ]
    combined = "\n".join(read(path) for path in docs if path.exists())
    if CANONICAL_PATH not in combined:
        fail(errors, f"architecture docs must mention canonical path: {CANONICAL_PATH}")
    forbidden_patterns = (
        "GameTradeGatewayService",
        "rpc IssueTradeInstance",
        "rpc AcceptTradeInstance",
        "rpc CancelTradeInstance",
        "/eve.api_gateway.v1.GameTradeGatewayService/",
        "/eve.market.v1.MarketService/IssueTradeInstance",
        "/eve.market.v1.MarketService/AcceptTradeInstance",
        "/eve.market.v1.MarketService/CancelTradeInstance",
    )
    for path in docs:
        if not path.exists():
            continue
        text = read(path)
        for forbidden in forbidden_patterns:
            if forbidden in text:
                fail(errors, f"{path.relative_to(ROOT)} still documents removed production path {forbidden}")


def check_kubernetes(errors: list[str]) -> None:
    prod_root = ROOT / "distributed-backend" / "orchestration" / "kubernetes" / "overlay" / "prod"
    for path in iter_files(prod_root, "*.yaml") + iter_files(prod_root, "*.yml"):
        text = read(path)
        if "app.kubernetes.io/name: simulator" in text or "name: simulator" in text:
            fail(errors, f"{path.relative_to(ROOT)} includes simulator in production overlay")
        if "/eve.api_gateway.v1.GameTradeGatewayService/" in text:
            fail(errors, f"{path.relative_to(ROOT)} exposes removed API Gateway RPC route")


def main() -> int:
    errors: list[str] = []
    check_protos(errors)
    check_api_gateway(errors)
    check_simulator_packet_test(errors)
    check_docs(errors)
    check_kubernetes(errors)
    if errors:
        for error in errors:
            print(f"architecture boundary violation: {error}", file=sys.stderr)
        return 1
    print("architecture boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
