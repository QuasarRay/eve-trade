"""Fixtures for explicit real-deployment property tests."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest


E2E_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = E2E_ROOT.parent
REPOSITORY_ROOT = PROPERTY_ROOT.parents[2]
ORACLE_ROOT = PROPERTY_ROOT / "reusable-oracles"
if str(ORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORACLE_ROOT))


@pytest.fixture(scope="session")
def real_deployment_evidence() -> dict[str, Any]:
    configured = os.environ.get("EVE_TRADE_EMULATION_EVIDENCE")
    if not configured:
        pytest.fail(
            "EVE_TRADE_EMULATION_EVIDENCE is required; a missing real-deployment prerequisite is a failure, not a skip"
        )
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.fail(f"real-deployment evidence does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        pytest.fail(f"real-deployment evidence is unreadable: {error}")
    if not isinstance(value, dict):
        pytest.fail("real-deployment evidence must be a JSON object")
    return value


@pytest.fixture(scope="session")
def source_business_records() -> list[dict[str, str]]:
    root = PROPERTY_ROOT / "tests-to-implement"
    records: list[dict[str, str]] = []
    paths = sorted(path for path in root.rglob("*.md") if path.name != "test-rules.md")
    if len(paths) != 46:
        pytest.fail(f"expected 46 source leaves, found {len(paths)}")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        declared = re.search(r"\*\*Test count:\*\*\s+(\d+)", text)
        names = re.findall(r"^- `(test_[a-z0-9_]+)`$", text, re.MULTILINE)
        if declared is None or len(names) != int(declared.group(1)):
            pytest.fail(f"source leaf count is invalid: {path}")
        records.extend(
            {"name": name, "source_path": path.relative_to(PROPERTY_ROOT).as_posix()}
            for name in names
        )
    if not records:
        pytest.fail("source business-test domain is empty")
    return records


@pytest.fixture(scope="session")
def classified_business_records() -> list[dict[str, object]]:
    value = json.loads((PROPERTY_ROOT / "classification.json").read_text(encoding="utf-8"))
    records = value.get("records")
    if not isinstance(records, list) or not records:
        pytest.fail("classification contains no original business-test records")
    return records
