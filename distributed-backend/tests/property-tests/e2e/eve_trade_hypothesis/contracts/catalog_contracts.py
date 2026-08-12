from __future__ import annotations

"""Structured identity checks for the observed native-test inventory."""

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CatalogContractSpec:
    oracle: str
    capabilities: tuple[str, ...]


CATALOG_CONTRACTS = {
    "test_existing_repository_test_names_are_never_silently_rewritten_in_observed_inventory": CatalogContractSpec(
        oracle="native_test_ast_identity",
        capabilities=("catalog_native_file_binding", "python_ast", "exact_cardinality"),
    ),
}


def binding_metadata(spec: CatalogContractSpec) -> dict[str, Any]:
    return {
        "family": "catalog_identity",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }


def observed_native_inventory(root: Path, branch: str = "experimental") -> dict[str, str]:
    catalog_path = (
        root
        / "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/catalog.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    existing = catalog.get("existing")
    assert isinstance(existing, dict) and existing, "catalog existing-test inventory is empty"
    parsed_files: dict[str, dict[str, int]] = {}
    observed: dict[str, str] = {}
    for name, metadata in sorted(existing.items()):
        assert isinstance(metadata, dict), name
        native_files = metadata.get("native_files")
        assert isinstance(native_files, dict), f"{name} has no branch-to-native-file binding"
        relative = native_files.get(branch)
        assert isinstance(relative, str) and relative, f"{name} has no native file for {branch}"
        path = root / "distributed-backend/tests/e2e" / relative
        assert path.is_file(), f"native E2E file missing for {name}: {path}"
        if relative not in parsed_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            counts: dict[str, int] = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    counts[node.name] = counts.get(node.name, 0) + 1
            parsed_files[relative] = counts
        cardinality = parsed_files[relative].get(name, 0)
        assert cardinality == 1, (
            f"catalog identity {name} must occur exactly once in {relative}; observed {cardinality}"
        )
        observed[name] = relative
    assert set(observed) == set(existing)
    return observed


def validate_catalog_contract(name: str, root: Path) -> None:
    assert name in CATALOG_CONTRACTS, f"unknown catalog semantic contract: {name}"
    observed = observed_native_inventory(root)
    assert observed
