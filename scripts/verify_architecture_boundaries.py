#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PATH = (
    "game frontend -> Quilkin UDP -> Encore gateway -> Market -> "
    "Encore Pub/Sub settlement work -> settlement worker -> Rust trade-settlement"
)
FORBIDDEN_ACTIVE_REFERENCES = (
    "connect" + "rpc.com/" + "connect",
    "protoc-gen-" + "connect-go",
    "rabbit" + "mqsettlement",
    "github.com/" + "rabbit" + "mq" + "/amqp091-go",
    "MARKET" + "_URL",
    "RABBIT" + "MQ_URL",
    "RABBIT" + "MQ_SETTLEMENT_",
)
REMOVED_PATHS = (
    "distributed-backend/src/" + "api" + "-" + "gateway",
    "distributed-backend/src/messaging",
    "distributed-backend/src/" + "settlement" + "-" + "worker",
    "distributed-backend/" + "proto/eve/" + "market",
    "distributed-backend/" + "proto/" + "gen",
)


def check_source_boundaries(root: Path) -> list[str]:
    errors: list[str] = []
    for path in (root / "gametrade").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("encore.dev/", "github.com/jackc/pgx", "/src/gateway", "/src/market"):
            if forbidden in text:
                errors.append(f"{path.relative_to(root)} crosses game-domain boundary via {forbidden}")
    for path in (root / "distributed-backend" / "src" / "market").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", text, re.IGNORECASE):
            errors.append(f"{path.relative_to(root)} contains database mutation outside Rust settlement")
    for path in (root / "distributed-backend" / "src" / "gateway").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        if "/gametrade" in text or "/trade-settlement" in text:
            errors.append(f"{path.relative_to(root)} leaks domain settlement into transport")
    return errors
SKIP_PARTS = {
    ".git",
    "vendor",
    "target",
    ".terraform",
    ".o11y",
    "artifacts",
    "fixtures",
    ".gomodcache",
    # Historical change logs are append-only records; current architecture
    # conformance is checked in active code, manifests, scripts, and docs.
    "changes",
}


def iter_text_files() -> list[Path]:
    result: list[Path] = []
    this_file = Path(__file__).resolve()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == this_file:
            continue
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in {".go", ".mod", ".sum", ".yaml", ".yml", ".json", ".py", ".ps1", ".md", ".tf"}:
            result.append(path)
    return sorted(result)


def check_simulator_packet_test(errors: list[str]) -> None:
    test_path = ROOT / "simulator" / "trade_gui" / "tests.py"
    if not test_path.exists():
        errors.append("simulator/trade_gui/tests.py is missing")
        return
    tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    test_name = "test_button_press_conforms_to_versioned_protocol_schema_and_golden_packet"
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name for node in ast.walk(tree)):
        errors.append(f"simulator packet boundary test {test_name} is missing")


def main() -> int:
    errors = check_source_boundaries(ROOT)
    if not (ROOT / "encore.app").exists():
        errors.append("encore.app is missing")
    for removed in REMOVED_PATHS:
        if (ROOT / removed).exists():
            errors.append(f"{removed} should have been removed")
    docs = "\n".join(path.read_text(encoding="utf-8") for path in [ROOT / "README.md"] if path.exists())
    if CANONICAL_PATH not in docs:
        errors.append(f"README must mention canonical path: {CANONICAL_PATH}")
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for forbidden in FORBIDDEN_ACTIVE_REFERENCES:
            if forbidden in text:
                errors.append(f"{path.relative_to(ROOT)} contains stale reference {forbidden}")
    check_simulator_packet_test(errors)
    if errors:
        for error in errors:
            print(f"architecture boundary violation: {error}", file=sys.stderr)
        return 1
    print("architecture boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
