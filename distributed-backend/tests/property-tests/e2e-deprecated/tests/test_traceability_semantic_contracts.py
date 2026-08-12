from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.traceability_contracts import (
    TRACEABILITY_CONTRACTS,
    assert_exact_collected_catalog,
    binding_metadata,
    validate_traceability_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("name", sorted(TRACEABILITY_CONTRACTS))
def test_each_traceability_binding_executes_exact_inventory_and_reference_oracle(name: str) -> None:
    validate_traceability_contract(name, ROOT)


def test_traceability_requirement_bindings_are_exact() -> None:
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "repository_traceability"
    }
    assert bound == {
        name: binding_metadata(spec) for name, spec in TRACEABILITY_CONTRACTS.items()
    }
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


def _copy_file(root: Path, relative: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / relative, target)


def _replace(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def test_pubsub_traceability_rejects_duplicate_test_that_only_executes_once(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for relative in (
        "scripts/validate_nsq_configuration.py",
        "distributed-backend/src/settlement/work.go",
        "distributed-backend/src/settlementworker/service.go",
        "distributed-backend/src/settlementworker/config.go",
        "distributed-backend/src/settlementworker/regression_lifecycle_test.go",
        "distributed-backend/src/market/settlement_result.go",
        "distributed-backend/src/market/settlement_lifecycle_test.go",
        "distributed-backend/src/trade-settlement/config/app.toml",
        "distributed-backend/orchestration/kubernetes/base/nsq.yaml",
        "distributed-backend/orchestration/kubernetes/overlay/local/nsq-local.yaml",
        "infra/encore/self-host.nsq.json",
        "infra/encore/self-host.local.nsq.json",
    ):
        _copy_file(root, relative)
    _replace(
        root,
        "distributed-backend/src/market/settlement_lifecycle_test.go",
        "attempt <= 2",
        "attempt <= 1",
    )
    with pytest.raises(AssertionError):
        validate_traceability_contract(
            "test_every_pubsub_handler_has_duplicate_delivery_test_reference", root
        )


def test_terraform_traceability_rejects_root_without_native_fixture(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "distributed-backend/terraform", root / "distributed-backend/terraform")
    _copy_file(root, ".github/workflows/verify.yaml")
    missing = root / "distributed-backend/terraform/gke/tests/production.tftest.hcl"
    missing.unlink()
    with pytest.raises(AssertionError, match="no native terraform-test fixture"):
        validate_traceability_contract(
            "test_every_terraform_root_has_terraform_test_fixture_reference", root
        )


def test_kubernetes_traceability_rejects_overlay_omitted_from_security_scan(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(
        ROOT / "distributed-backend/orchestration/kubernetes",
        root / "distributed-backend/orchestration/kubernetes",
    )
    _copy_file(root, ".github/dagger/kubernetes.py")
    _copy_file(root, ".github/dagger/security.py")
    _replace(
        root,
        ".github/dagger/security.py",
        '    "distributed-backend/orchestration/kubernetes/platform/gateway/prod",\n',
        "",
    )
    with pytest.raises(AssertionError, match="missing from Trivy"):
        validate_traceability_contract(
            "test_every_kubernetes_production_overlay_has_render_and_security_test_reference",
            root,
        )


def test_physical_collection_oracle_rejects_silently_missing_catalog_identity() -> None:
    with pytest.raises(AssertionError, match="missing from collected suite"):
        assert_exact_collected_catalog(
            ["test_first_contract", "test_second_contract"],
            ["test_first_contract"],
        )
