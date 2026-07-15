#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
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

GO_STRING_LITERAL = re.compile(r'`(?P<raw>[^`]*)`|"(?P<quoted>(?:\\.|[^"\\])*)"', re.DOTALL)
SQL_MUTATION = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+[A-Za-z_][\w.\"]*\s+SET|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Diagnostic:
    rule_id: str
    path: str
    line: int
    token: str
    message: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def diagnostic(rule_id: str, path: Path | str, line: int, token: str, message: str) -> Diagnostic:
    return Diagnostic(rule_id=rule_id, path=str(path).replace("\\", "/"), line=line, token=token, message=message)


def token_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def go_string_matches(text: str, pattern: re.Pattern[str]) -> list[tuple[re.Match[str], int]]:
    matches: list[tuple[re.Match[str], int]] = []
    for literal in GO_STRING_LITERAL.finditer(text):
        content = literal.group("raw") if literal.group("raw") is not None else literal.group("quoted") or ""
        for match in pattern.finditer(content):
            matches.append((match, token_line(text, literal.start() + match.start())))
    return matches


def check_source_boundaries(root: Path) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    for path in (root / "gametrade").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("encore.dev/", "github.com/jackc/pgx", "/src/gateway", "/src/market"):
            if forbidden in text:
                errors.append(
                    diagnostic(
                        "GAME_DOMAIN_FORBIDDEN_DEPENDENCY",
                        path.relative_to(root),
                        token_line(text, text.index(forbidden)),
                        forbidden,
                        "game domain must not depend on service or infrastructure packages",
                    )
                )
    for path in (root / "distributed-backend" / "src" / "market").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        for match, line in go_string_matches(text, SQL_MUTATION):
            errors.append(
                diagnostic(
                    "MARKET_DATABASE_MUTATION",
                    path.relative_to(root),
                    line,
                    match.group(1).split()[0].upper(),
                    "Market must not mutate the settlement database directly",
                )
            )
    for path in (root / "distributed-backend" / "src" / "gateway").glob("*.go"):
        text = path.read_text(encoding="utf-8")
        if "/gametrade" in text or "/trade-settlement" in text:
            token = "/gametrade" if "/gametrade" in text else "/trade-settlement"
            errors.append(
                diagnostic(
                    "GATEWAY_DOMAIN_LEAK",
                    path.relative_to(root),
                    token_line(text, text.index(token)),
                    token,
                    "gateway transport must not depend on settlement domain implementations",
                )
            )
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


def check_simulator_packet_test(errors: list[Diagnostic]) -> None:
    test_path = ROOT / "simulator" / "trade_gui" / "tests.py"
    if not test_path.exists():
        errors.append(diagnostic("SIMULATOR_PACKET_TEST_MISSING", "simulator/trade_gui/tests.py", 0, "", "simulator packet boundary test file is missing"))
        return
    tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    test_name = "test_button_press_conforms_to_versioned_protocol_schema_and_golden_packet"
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name for node in ast.walk(tree)):
        errors.append(diagnostic("SIMULATOR_PACKET_TEST_MISSING", test_path.relative_to(ROOT), 0, test_name, "simulator packet boundary test is missing"))


def main() -> int:
    errors = check_source_boundaries(ROOT)
    if not (ROOT / "encore.app").exists():
        errors.append(diagnostic("ENCORE_APP_MISSING", "encore.app", 0, "encore.app", "Encore application marker is missing"))
    for removed in REMOVED_PATHS:
        if (ROOT / removed).exists():
            errors.append(diagnostic("REMOVED_PATH_PRESENT", removed, 0, removed, "removed architecture path is present"))
    docs = "\n".join(path.read_text(encoding="utf-8") for path in [ROOT / "README.md"] if path.exists())
    if CANONICAL_PATH not in docs:
        errors.append(diagnostic("CANONICAL_PATH_MISSING", "README.md", 0, CANONICAL_PATH, "README must document the canonical request path"))
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for forbidden in FORBIDDEN_ACTIVE_REFERENCES:
            if forbidden in text:
                errors.append(
                    diagnostic(
                        "STALE_ACTIVE_REFERENCE",
                        path.relative_to(ROOT),
                        token_line(text, text.index(forbidden)),
                        forbidden,
                        "active source contains a stale architecture reference",
                    )
                )
    check_simulator_packet_test(errors)
    if errors:
        for error in errors:
            print(json.dumps(error.to_dict(), sort_keys=True), file=sys.stderr)
        return 1
    print("architecture boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
