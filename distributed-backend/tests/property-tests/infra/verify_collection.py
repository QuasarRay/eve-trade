#!/usr/bin/env python3
"""Collect generated pytest nodes and compare exact names to the Markdown catalog."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


INFRA_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = INFRA_ROOT.parent
E2E_ROOT = PROPERTY_ROOT / "e2e"
CATALOG = PROPERTY_ROOT / "tests-to-implement.md"
GENERATED = E2E_ROOT / "eve_trade_hypothesis" / "generated"


def main() -> int:
    authoritative = re.findall(r"`(test_[a-z0-9_]+)`", CATALOG.read_text(encoding="utf-8"))
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(GENERATED)],
        cwd=E2E_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"pytest collection failed ({process.returncode})\n"
            f"stdout={process.stdout[-12000:]}\nstderr={process.stderr[-12000:]}"
        )
    collected: list[str] = []
    for line in process.stdout.splitlines():
        if "::test_" not in line:
            continue
        node_name = line.rsplit("::", 1)[-1].split("[", 1)[0]
        if re.fullmatch(r"test_[a-z0-9_]+", node_name):
            collected.append(node_name)
    duplicate_collected = sorted(name for name, count in Counter(collected).items() if count != 1)
    missing = sorted(set(authoritative) - set(collected))
    extra = sorted(set(collected) - set(authoritative))
    if duplicate_collected or missing or extra or len(collected) != len(authoritative):
        raise RuntimeError(
            "collected catalog mismatch: "
            f"duplicates={duplicate_collected}, missing={missing}, extra={extra}, "
            f"authoritative_count={len(authoritative)}, collected_count={len(collected)}"
        )
    print(
        json.dumps(
            {
                "schema_version": "eve-trade.property-collection/v1",
                "authoritative_count": len(authoritative),
                "collected_count": len(collected),
                "missing": [],
                "duplicates": [],
                "extra": [],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

