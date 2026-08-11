from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any


INFRA_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = INFRA_ROOT.parent
E2E_ROOT = PROPERTY_ROOT / "e2e"
REQUIREMENTS_PATH = INFRA_ROOT / "test-requirements.json"
LITMUS_CONTRACTS_PATH = INFRA_ROOT / "litmus-contracts.json"


class ExecutionMechanism(StrEnum):
    DIRECT_LIVE = "DIRECT_LIVE"
    LITMUS_CHAOS = "LITMUS_CHAOS"
    PLATFORM_EXTERNAL = "PLATFORM_EXTERNAL"
    REPOSITORY_STATIC = "REPOSITORY_STATIC"
    NATIVE_EXISTING = "NATIVE_EXISTING"
    NON_APPLICABLE = "NON_APPLICABLE"


class ImplementationStatus(StrEnum):
    IMPLEMENTED = "IMPLEMENTED"
    INFRASTRUCTURE_READY = "INFRASTRUCTURE_READY"
    EXTERNAL_CAPABILITY_REQUIRED = "EXTERNAL_CAPABILITY_REQUIRED"
    SEMANTIC_ORACLE_REQUIRED = "SEMANTIC_ORACLE_REQUIRED"
    JUSTIFIED_NON_APPLICABLE = "JUSTIFIED_NON_APPLICABLE"


@dataclass(frozen=True)
class ContractRequirement:
    name: str
    mechanism: ExecutionMechanism
    status: ImplementationStatus
    category: int | None
    category_title: str
    runner: str
    rationale: str
    prerequisite: str
    observable: str
    source: str
    blockers: tuple[str, ...]

    @property
    def semantically_implemented(self) -> bool:
        return self.status in {
            ImplementationStatus.IMPLEMENTED,
            ImplementationStatus.JUSTIFIED_NON_APPLICABLE,
        }


def _require_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"contract requirement lacks non-empty {key!r}: {record!r}")
    return value


@lru_cache(maxsize=1)
def load_requirement_document() -> dict[str, Any]:
    document = json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != "eve-trade.test-requirements/v1":
        raise ValueError(f"unsupported requirement schema: {document.get('schema_version')!r}")
    contracts = document.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("test-requirements.json contracts must be a list")
    names = [record.get("name") for record in contracts]
    if len(names) != len(set(names)):
        raise ValueError("test-requirements.json contains duplicate contract names")
    return document


@lru_cache(maxsize=1)
def load_requirements() -> dict[str, ContractRequirement]:
    result: dict[str, ContractRequirement] = {}
    for record in load_requirement_document()["contracts"]:
        name = _require_string(record, "name")
        category = record.get("category")
        if category is not None and (isinstance(category, bool) or not isinstance(category, int)):
            raise ValueError(f"invalid category for {name}: {category!r}")
        blockers = record.get("blockers", [])
        if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
            raise ValueError(f"invalid blockers for {name}")
        result[name] = ContractRequirement(
            name=name,
            mechanism=ExecutionMechanism(_require_string(record, "mechanism")),
            status=ImplementationStatus(_require_string(record, "implementation_status")),
            category=category,
            category_title=str(record.get("category_title") or "Existing native E2E"),
            runner=_require_string(record, "runner"),
            rationale=_require_string(record, "rationale"),
            prerequisite=_require_string(record, "prerequisite"),
            observable=_require_string(record, "observable"),
            source=_require_string(record, "source"),
            blockers=tuple(blockers),
        )
    return result


def requirement_for(name: str) -> ContractRequirement:
    try:
        return load_requirements()[name]
    except KeyError as exc:
        raise KeyError(f"contract has no canonical requirement classification: {name}") from exc


@lru_cache(maxsize=1)
def load_litmus_contracts() -> dict[str, dict[str, Any]]:
    document = json.loads(LITMUS_CONTRACTS_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != "eve-trade.litmus-contracts/v1":
        raise ValueError(f"unsupported Litmus contract schema: {document.get('schema_version')!r}")
    contracts = document.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("litmus-contracts.json contracts must be a list")
    result = {record["contract"]: record for record in contracts}
    if len(result) != len(contracts):
        raise ValueError("litmus-contracts.json contains duplicate contract names")
    return result

