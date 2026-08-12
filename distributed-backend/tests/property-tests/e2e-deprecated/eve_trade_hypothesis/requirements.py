from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


INFRA_ROOT = Path(__file__).resolve().parents[2] / "infra"
REQUIREMENTS_PATH = INFRA_ROOT / "test-requirements.json"
LITMUS_CONTRACTS_PATH = INFRA_ROOT / "litmus-contracts.json"


@lru_cache(maxsize=1)
def requirement_document() -> dict[str, Any]:
    document = json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != "eve-trade.test-requirements/v1":
        raise RuntimeError("unsupported or missing canonical requirement manifest")
    return document


@lru_cache(maxsize=1)
def requirements_by_name() -> dict[str, dict[str, Any]]:
    records = requirement_document().get("contracts")
    if not isinstance(records, list):
        raise RuntimeError("canonical requirement manifest has no contract list")
    result = {str(record["name"]): record for record in records}
    if len(result) != len(records):
        raise RuntimeError("canonical requirement manifest contains duplicate names")
    return result


def requirement_for(name: str) -> dict[str, Any]:
    try:
        return requirements_by_name()[name]
    except KeyError as exc:
        raise RuntimeError(f"contract is absent from canonical requirement manifest: {name}") from exc


@lru_cache(maxsize=1)
def litmus_contracts_by_name() -> dict[str, dict[str, Any]]:
    document = json.loads(LITMUS_CONTRACTS_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != "eve-trade.litmus-contracts/v1":
        raise RuntimeError("unsupported or missing canonical Litmus contract manifest")
    records = document.get("contracts")
    if not isinstance(records, list):
        raise RuntimeError("canonical Litmus manifest has no contract list")
    result = {str(record["contract"]): record for record in records}
    if len(result) != len(records):
        raise RuntimeError("canonical Litmus manifest contains duplicate names")
    return result


def litmus_contract_for(name: str) -> dict[str, Any]:
    try:
        return litmus_contracts_by_name()[name]
    except KeyError as exc:
        raise RuntimeError(f"chaos contract has no canonical Litmus specification: {name}") from exc

