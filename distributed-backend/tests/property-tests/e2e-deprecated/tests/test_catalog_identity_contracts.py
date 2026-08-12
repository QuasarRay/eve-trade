from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.catalog_contracts import (
    CATALOG_CONTRACTS,
    binding_metadata,
    observed_native_inventory,
    validate_catalog_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


ROOT = Path(__file__).resolve().parents[5]
CONTRACT = "test_existing_repository_test_names_are_never_silently_rewritten_in_observed_inventory"


def _copy_inventory(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    source_catalog = ROOT / "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/catalog.json"
    target_catalog = root / "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/catalog.json"
    target_catalog.parent.mkdir(parents=True)
    shutil.copy2(source_catalog, target_catalog)
    catalog = json.loads(source_catalog.read_text(encoding="utf-8"))
    files = {
        metadata["native_files"]["experimental"]
        for metadata in catalog["existing"].values()
    }
    for relative in files:
        target = root / "distributed-backend/tests/e2e" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "distributed-backend/tests/e2e" / relative, target)
    return root


def test_catalog_identity_binding_is_exact_and_implemented() -> None:
    requirements = requirements_by_name()
    assert set(CATALOG_CONTRACTS) == {CONTRACT}
    assert requirements[CONTRACT]["semantic_binding"] == binding_metadata(CATALOG_CONTRACTS[CONTRACT])
    assert requirements[CONTRACT]["implementation_status"] == "IMPLEMENTED"
    validate_catalog_contract(CONTRACT, ROOT)


def test_native_inventory_rejects_a_silent_function_rename(tmp_path: Path) -> None:
    root = _copy_inventory(tmp_path)
    inventory = observed_native_inventory(root)
    name, relative = next(iter(sorted(inventory.items())))
    path = root / "distributed-backend/tests/e2e" / relative
    source = path.read_text(encoding="utf-8")
    assert f"def {name}(" in source
    path.write_text(source.replace(f"def {name}(", f"def {name}_renamed(", 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="must occur exactly once"):
        observed_native_inventory(root)
