#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "eve_trade_hypothesis" / "catalog.json"
GENERATED = ROOT / "eve_trade_hypothesis" / "generated"


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
    expected_proposed = {n for c in catalog["categories"].values() for n in c["names"]}
    expected_existing = set(catalog["existing"])

    proposed: list[str] = []
    existing: list[str] = []
    categories: set[int] = set()
    errors: list[str] = []

    for path in GENERATED.glob("test_category_*.py"):
        c = constants(path)
        category = int(c["CATEGORY_ID"])
        categories.add(category)
        names = list(c["CONTRACT_NAMES"])
        proposed.extend(names)
        expected = catalog["categories"].get(str(category), {}).get("names")
        if names != expected:
            errors.append(f"{path.name}: CONTRACT_NAMES differs from catalog")

    for path in GENERATED.glob("test_existing_*.py"):
        c = constants(path)
        existing.extend(c["CONTRACT_NAMES"])

    for label, values, expected in (
        ("proposed", proposed, expected_proposed),
        ("existing", existing, expected_existing),
    ):
        duplicates = [n for n, count in Counter(values).items() if count > 1]
        if duplicates:
            errors.append(f"{label}: duplicate generated names: {duplicates[:10]}")
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        if missing:
            errors.append(f"{label}: missing {len(missing)} names: {missing[:10]}")
        if extra:
            errors.append(f"{label}: extra {len(extra)} names: {extra[:10]}")

    invalid = sorted(
        n for n in expected_proposed | expected_existing
        if not re.fullmatch(r"test_[a-z0-9_]+", n)
    )
    if invalid:
        errors.append(f"invalid test identifiers: {invalid[:10]}")

    if expected_existing & expected_proposed:
        errors.append("existing and proposed name sets overlap")

    # Every proposed name must compile to a concrete semantic evidence spec.
    sys.path.insert(0, str(ROOT))
    try:
        from eve_trade_hypothesis.evidence_specs import build_evidence_spec
        from eve_trade_hypothesis.semantic_overrides import SEMANTIC_EVIDENCE_OVERRIDES
        for category_id, category in catalog["categories"].items():
            for name in category["names"]:
                spec = build_evidence_spec(int(category_id), name)
                if len(spec.predicates) < 4:
                    errors.append(f"{name}: evidence spec has no semantic postcondition")
        if not SEMANTIC_EVIDENCE_OVERRIDES <= expected_proposed:
            errors.append("semantic override set contains names absent from proposed catalog")
        if len(SEMANTIC_EVIDENCE_OVERRIDES) < 202:
            errors.append(f"semantic override count fell below audited baseline: {len(SEMANTIC_EVIDENCE_OVERRIDES)}")
    except Exception as exc:
        errors.append(f"semantic evidence verification failed: {exc}")

    mode_text = (ROOT / "eve_trade_hypothesis" / "modes.py").read_text(encoding="utf-8")
    assigned = set()
    for set_name in ("FAULT_CATEGORIES", "EDGE_CATEGORIES", "TRADE_CATEGORIES", "REPO_CATEGORIES"):
        match = re.search(rf"{set_name}\s*=\s*(\{{.*?\}})", mode_text, flags=re.S)
        if match:
            assigned.update(ast.literal_eval(match.group(1)))
    unassigned = sorted(categories - assigned)
    if unassigned:
        errors.append(f"categories without runner: {unassigned}")

    if errors:
        print("CATALOG VERIFICATION FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"existing wrappers: {len(expected_existing)}")
    print(f"proposed Hypothesis contracts: {len(expected_proposed)}")
    print(f"combined named contracts: {len(expected_existing | expected_proposed)}")
    print(f"generated category modules: {len(categories)}")
    print("catalog registration: complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
