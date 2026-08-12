from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.supply_chain_contracts import (
    SUPPLY_CHAIN_CONTRACTS,
    binding_metadata,
    validate_supply_chain_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("name", sorted(SUPPLY_CHAIN_CONTRACTS))
def test_each_supply_chain_binding_executes_its_exact_release_oracle(name: str) -> None:
    validate_supply_chain_contract(name, ROOT)


def test_supply_chain_requirement_bindings_are_exact() -> None:
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "release_supply_chain"
    }
    assert bound == {
        name: binding_metadata(spec) for name, spec in SUPPLY_CHAIN_CONTRACTS.items()
    }
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


def _copy(root: Path, relative: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / relative, target)


def _replace(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _copy_release_sources(root: Path) -> None:
    for relative in (
        ".github/workflows/verify.yaml",
        ".github/dagger/release.py",
        "scripts/render_release_kubernetes.py",
        "scripts/verify_rendered_kubernetes.py",
    ):
        _copy(root, relative)


def test_release_digest_graph_rejects_cross_image_digest_substitution(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _copy_release_sources(root)
    _replace(
        root,
        ".github/workflows/verify.yaml",
        'eve-trade-encore-backend@sha256:${encore_digest}',
        'eve-trade-encore-backend@sha256:${settlement_digest}',
    )
    with pytest.raises(AssertionError):
        validate_supply_chain_contract(
            "test_release_container_digest_matches_digest_produced_by_ci_build_job", root
        )


def test_release_commit_gate_rejects_removed_mismatch_guard(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _copy_release_sources(root)
    _replace(root, ".github/dagger/release.py", "if verified_sha != sha:", "if False:")
    with pytest.raises(AssertionError, match="different commit"):
        validate_supply_chain_contract(
            "test_ci_refuses_to_deploy_image_built_from_different_commit_than_checked_out_source",
            root,
        )


def test_release_render_gate_rejects_missing_post_render_verifier(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _copy_release_sources(root)
    _replace(
        root,
        ".github/dagger/release.py",
        "python scripts/verify_rendered_kubernetes.py release-kubernetes.yaml",
        "true # verification removed",
    )
    with pytest.raises(ValueError):
        validate_supply_chain_contract(
            "test_release_container_image_is_referenced_by_digest_in_production_manifest", root
        )


def test_base_image_inventory_rejects_unpinned_release_stage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    dagger = root / ".github/dagger"
    dagger.mkdir(parents=True)
    for path in (ROOT / ".github/dagger").glob("*.py"):
        shutil.copy2(path, dagger / path.name)
    for relative in (
        "distributed-backend/docker/trade-settlement.Dockerfile",
        "distributed-backend/docker/quilkin.Dockerfile",
    ):
        _copy(root, relative)
    dockerfile = "distributed-backend/docker/quilkin.Dockerfile"
    source = (root / dockerfile).read_text(encoding="utf-8")
    old = next(line for line in source.splitlines() if line.startswith("FROM "))
    image = old.split()[1].split("@", 1)[0]
    _replace(root, dockerfile, old, f"FROM {image} AS build")
    with pytest.raises(AssertionError):
        validate_supply_chain_contract(
            "test_container_base_image_update_changes_locked_or_reviewable_provenance_input",
            root,
        )
