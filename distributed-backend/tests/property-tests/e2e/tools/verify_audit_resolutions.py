#!/usr/bin/env python3
"""Verify the current audit revokes old v2 claims and remains generated/current."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROPERTY_ROOT = ROOT.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_hypothesis_audit import generate, markdown  # noqa: E402


def main() -> int:
    errors: list[str] = []
    expected = generate()
    actual = json.loads((ROOT / "HYPOTHESIS_AUDIT.json").read_text(encoding="utf-8"))
    if actual != expected:
        errors.append("HYPOTHESIS_AUDIT.json is stale")
    if (ROOT / "HYPOTHESIS_AUDIT.md").read_text(encoding="utf-8") != markdown(expected):
        errors.append("HYPOTHESIS_AUDIT.md is stale")

    requirements = json.loads(
        (PROPERTY_ROOT / "infra" / "test-requirements.json").read_text(encoding="utf-8")
    )
    semantic = json.loads((ROOT / "semantic_override_manifest.json").read_text(encoding="utf-8"))
    by_name = {record["name"]: record for record in requirements["contracts"]}
    for name in semantic:
        record = by_name[name]
        if record["mechanism"] not in {"LITMUS_CHAOS", "PLATFORM_EXTERNAL", "NON_APPLICABLE"}:
            if record.get("semantic_binding"):
                if record["implementation_status"] != "IMPLEMENTED":
                    errors.append(f"semantic binding is not implemented: {name}")
            elif record["implementation_status"] != "SEMANTIC_ORACLE_REQUIRED":
                errors.append(f"old semantic override is no longer fail-closed: {name}")

    external_source = (ROOT / "eve_trade_hypothesis" / "external.py").read_text(encoding="utf-8")
    if "PROTOCOL_VERSION" not in external_source or "validate_execution_identity" not in external_source:
        errors.append("external driver is not bound to protocol-v3 execution identity")
    if "protocol_version = 2" in external_source:
        errors.append("obsolete protocol-v2 implementation remains active")

    engine_source = (ROOT / "eve_trade_hypothesis" / "engine.py").read_text(encoding="utf-8")
    if "SEMANTIC_EVIDENCE_OVERRIDES" in engine_source:
        errors.append("weak direct contracts are still rerouted to generic evidence")
    if "SEMANTIC_ORACLE_REQUIRED" not in engine_source:
        errors.append("engine lacks the semantic fail-closed gate")

    for path in (ROOT / "eve_trade_hypothesis" / "contracts").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "uuid4":
                errors.append(f"uncontrolled uuid4 call remains: {path.name}:{node.lineno}")

    if errors:
        print("AUDIT VERIFICATION FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(json.dumps(expected["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
