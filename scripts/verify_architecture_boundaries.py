#!/usr/bin/env python3
from __future__ import annotations

import ast
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
    tree = ast.parse(read(test_path), filename=str(test_path))
    test_name = "test_button_press_conforms_to_versioned_protocol_schema_and_golden_packet"
    test = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name),
        None,
    )
    if test is None:
        fail(errors, f"simulator packet boundary test {test_name} is missing")
        return
    decorators = {ast.unparse(node) for node in test.decorator_list}
    if any("skip" in decorator.lower() for decorator in decorators):
        fail(errors, "simulator packet boundary test must not be skipped")
    calls = [node for node in ast.walk(test) if isinstance(node, ast.Call)]
    assert_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr.startswith("assert")
    ]
    if len(assert_calls) < 8:
        fail(errors, "simulator packet boundary test must contain executable protocol assertions")
    test_source = ast.get_source_segment(read(test_path), test) or ""
    for required in (
        "Draft202012Validator(schema).validate(game_packet)",
        "sell-order.packet.json",
        "trade_gui.udp_client.socket.socket",
        "FORBIDDEN_PACKET_TERMS",
    ):
        if required not in test_source:
            fail(errors, f"simulator packet boundary test is missing executable contract {required}")
    forbidden_values = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for term in SIMULATOR_FORBIDDEN_TERMS:
        if term not in forbidden_values:
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
    for path in kustomize_resource_graph(prod_root):
        text = read(path)
        if "app.kubernetes.io/name: simulator" in text or "name: simulator" in text:
            fail(errors, f"{path.relative_to(ROOT)} includes simulator in production overlay")
        if "/eve.api_gateway.v1.GameTradeGatewayService/" in text:
            fail(errors, f"{path.relative_to(ROOT)} exposes removed API Gateway RPC route")


def check_test_reliability_contracts(errors: list[str]) -> None:
    critical_roots = (
        ROOT / "distributed-backend" / "tests" / "e2e",
        ROOT / "simulator" / "trade_gui",
        ROOT / "observability" / "tests",
    )
    assertion_helpers = {"expect_rpc_error", "expect_grpc_error"}
    for root in critical_roots:
        for path in iter_files(root, "test*.py"):
            source = read(path)
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                    continue
                has_assertion = any(
                    isinstance(child, ast.Assert)
                    or (
                        isinstance(child, ast.Call)
                        and (
                            isinstance(child.func, ast.Name)
                            and child.func.id in assertion_helpers
                            or isinstance(child.func, ast.Attribute)
                            and (child.func.attr.startswith(("assert", "fail")) or child.func.attr == "raises")
                        )
                    )
                    for child in ast.walk(node)
                )
                if not has_assertion:
                    fail(errors, f"{path.relative_to(ROOT)}:{node.lineno} test {node.name} has no executable assertion")

                for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
                    if not isinstance(call.func, ast.Name) or call.func.id != "expect_rpc_error":
                        continue
                    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
                    for required in ("code", "contains"):
                        value = keywords.get(required)
                        if not isinstance(value, ast.Constant) or not isinstance(value.value, str) or not value.value.strip():
                            fail(
                                errors,
                                f"{path.relative_to(ROOT)}:{call.lineno} expect_rpc_error must supply a non-empty literal {required}",
                            )


def kustomize_resource_graph(root: Path) -> list[Path]:
    pending = [root]
    seen_directories: set[Path] = set()
    resources: set[Path] = set()
    while pending:
        directory = pending.pop().resolve()
        if directory in seen_directories:
            continue
        seen_directories.add(directory)
        kustomization = next(
            (candidate for candidate in (directory / "kustomization.yaml", directory / "kustomization.yml") if candidate.exists()),
            None,
        )
        if kustomization is None:
            continue
        lines = read(kustomization).splitlines()
        in_resources = False
        for line in lines:
            if line and not line.startswith((" ", "\t", "#")):
                in_resources = line.rstrip() in {"resources:", "bases:", "components:"}
                continue
            match = re.match(r"^\s+-\s+([^#]+?)\s*$", line) if in_resources else None
            if not match:
                continue
            target = (directory / match.group(1).strip()).resolve()
            if target.is_dir():
                pending.append(target)
            elif target.suffix in {".yaml", ".yml"} and target.exists():
                resources.add(target)
        resources.add(kustomization.resolve())
    return sorted(resources)


def main() -> int:
    errors: list[str] = []
    check_protos(errors)
    check_api_gateway(errors)
    check_simulator_packet_test(errors)
    check_docs(errors)
    check_kubernetes(errors)
    check_test_reliability_contracts(errors)
    if errors:
        for error in errors:
            print(f"architecture boundary violation: {error}", file=sys.stderr)
        return 1
    print("architecture boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
