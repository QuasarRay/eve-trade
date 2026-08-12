#!/usr/bin/env python3
"""Zero-dependency structural check of catalog, generated modules, and routes."""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROPERTY_ROOT = ROOT.parent
CATALOG_PATH = ROOT / "eve_trade_hypothesis" / "catalog.json"
GENERATED = ROOT / "eve_trade_hypothesis" / "generated"
AUTHORITATIVE = PROPERTY_ROOT / "tests-to-implement.md"
REQUIREMENTS = PROPERTY_ROOT / "infra" / "test-requirements.json"
LITMUS = PROPERTY_ROOT / "infra" / "litmus-contracts.json"


def constants(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return out


def main() -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    requirements_doc = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    litmus_doc = json.loads(LITMUS.read_text(encoding="utf-8"))
    authoritative = re.findall(
        r"`(test_[a-z0-9_]+)`", AUTHORITATIVE.read_text(encoding="utf-8")
    )
    expected_proposed = {name for category in catalog["categories"].values() for name in category["names"]}
    expected_existing = set(catalog["existing"])
    requirement_records = {record["name"]: record for record in requirements_doc["contracts"]}
    proposed: list[str] = []
    existing: list[str] = []
    categories: set[int] = set()
    errors: list[str] = []

    for path in GENERATED.glob("test_category_*.py"):
        item = constants(path)
        category = int(item["CATEGORY_ID"])
        categories.add(category)
        names = list(item["CONTRACT_NAMES"])
        proposed.extend(names)
        if names != catalog["categories"].get(str(category), {}).get("names"):
            errors.append(f"{path.name}: CONTRACT_NAMES differs from catalog")
    for path in GENERATED.glob("test_existing_*.py"):
        existing.extend(constants(path)["CONTRACT_NAMES"])

    for label, values, expected in (
        ("proposed", proposed, expected_proposed),
        ("existing", existing, expected_existing),
    ):
        duplicates = [name for name, count in Counter(values).items() if count > 1]
        if duplicates:
            errors.append(f"{label}: duplicate generated names: {duplicates[:10]}")
        if set(values) != expected:
            errors.append(
                f"{label}: missing={sorted(expected - set(values))[:10]} "
                f"extra={sorted(set(values) - expected)[:10]}"
            )

    combined = expected_existing | expected_proposed
    if len(authoritative) != len(set(authoritative)):
        errors.append("authoritative Markdown contains duplicate names")
    if combined != set(authoritative):
        errors.append("catalog names differ from authoritative Markdown")
    if set(requirement_records) != set(authoritative):
        errors.append("requirement names differ from authoritative Markdown")
    if expected_existing & expected_proposed:
        errors.append("existing and proposed name sets overlap")

    mode_text = (ROOT / "eve_trade_hypothesis" / "modes.py").read_text(encoding="utf-8")
    mode_categories: dict[str, set[int]] = {}
    assigned: set[int] = set()
    for set_name, runner in (
        ("FAULT_CATEGORIES", "fault"),
        ("EDGE_CATEGORIES", "edge"),
        ("TRADE_CATEGORIES", "trade"),
        ("REPO_CATEGORIES", "repo"),
    ):
        match = re.search(rf"{set_name}\s*=\s*(\{{.*?\}})", mode_text, flags=re.S)
        if match:
            mode_categories[runner] = set(ast.literal_eval(match.group(1)))
            assigned.update(mode_categories[runner])

    routes = {
        "DIRECT_LIVE": {"trade", "edge", "repo", "fault"},
        "LITMUS_CHAOS": {"litmus"},
        "PLATFORM_EXTERNAL": {"platform"},
        "REPOSITORY_STATIC": {"repo"},
        "NATIVE_EXISTING": {"native"},
        "NON_APPLICABLE": {"non_applicable"},
    }
    for name, record in requirement_records.items():
        if record.get("mechanism") not in routes:
            errors.append(f"{name}: unknown mechanism {record.get('mechanism')!r}")
            continue
        if record.get("runner") not in routes[record["mechanism"]]:
            errors.append(f"{name}: incompatible runner {record.get('runner')!r}")
        elif record["mechanism"] == "DIRECT_LIVE" and record.get("category") not in mode_categories.get(record["runner"], set()):
            errors.append(
                f"{name}: category {record.get('category')} is unsupported by direct runner "
                f"{record.get('runner')!r}"
            )
        if not record.get("prerequisite") or not record.get("observable"):
            errors.append(f"{name}: missing prerequisite/observable")
        if record.get("implementation_status") not in {
            "IMPLEMENTED", "SEMANTIC_ORACLE_REQUIRED", "EXTERNAL_CAPABILITY_REQUIRED",
            "INFRASTRUCTURE_READY", "JUSTIFIED_NON_APPLICABLE",
        }:
            errors.append(f"{name}: unknown implementation status")

    litmus_names = {record["contract"] for record in litmus_doc["contracts"]}
    required_litmus = {
        name for name, record in requirement_records.items() if record["mechanism"] == "LITMUS_CHAOS"
    }
    if litmus_names != required_litmus:
        errors.append("Litmus contract names differ from LITMUS_CHAOS requirements")

    if categories - assigned:
        errors.append(f"categories without native runner: {sorted(categories - assigned)}")

    if errors:
        print("CATALOG VERIFICATION FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "authoritative_contracts": len(authoritative),
                "existing": len(expected_existing),
                "proposed": len(expected_proposed),
                "generated_categories": len(categories),
                "mechanisms": requirements_doc["counts"],
                "implementation_statuses": requirements_doc["implementation_status_counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
