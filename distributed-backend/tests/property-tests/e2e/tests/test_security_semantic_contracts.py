from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.security_contracts import (
    SECURITY_CONTRACTS,
    binding_metadata,
    load_security_plan,
    python_requirement_is_bounded,
    validate_security_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def security_plan():
    return load_security_plan(REPO_ROOT)


@pytest.mark.parametrize("name", sorted(SECURITY_CONTRACTS))
def test_each_security_binding_executes_its_exact_oracle(name, security_plan):
    validate_security_contract(name, REPO_ROOT, plan=security_plan)


def test_security_requirement_bindings_are_exactly_the_executable_registry():
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "security_ci"
    }
    expected = {
        name: binding_metadata(spec)
        for name, spec in SECURITY_CONTRACTS.items()
    }
    assert bound == expected
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


def test_go_audit_oracle_rejects_package_wildcard_removal(security_plan):
    mutated = replace(
        security_plan,
        go_commands=tuple(
            "govulncheck ./cmd/..." if command == "govulncheck ./..." else command
            for command in security_plan.go_commands
        ),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_govulncheck_scans_every_go_package_in_module", REPO_ROOT, plan=mutated
        )


def test_cargo_audit_oracle_rejects_unlocked_tool_install(security_plan):
    mutated = replace(
        security_plan,
        rust_commands=tuple(command.replace(" --locked", "") for command in security_plan.rust_commands),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_cargo_audit_scans_locked_dependencies_used_by_trade_settlement_build",
            REPO_ROOT,
            plan=mutated,
        )


def test_ignored_dependency_oracle_rejects_runtime_only_feature_scope(security_plan):
    mutated = replace(
        security_plan,
        rust_commands=tuple(command.replace("--all-features", "--no-default-features") for command in security_plan.rust_commands),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_ignored_rust_advisory_dependency_is_absent_from_cargo_tree_for_runtime_target_and_all_enabled_features",
            REPO_ROOT,
            plan=mutated,
            execute_cargo_tree=False,
        )


def test_new_vulnerability_oracle_rejects_unlisted_ignore_flag(security_plan):
    mutated = replace(
        security_plan,
        rust_commands=security_plan.rust_commands
        + ("cargo audit --deny warnings --ignore RUSTSEC-2099-9999",),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_new_critical_vulnerability_fails_ci_unless_exact_advisory_has_explicit_active_exception",
            REPO_ROOT,
            plan=mutated,
        )


def test_python_audit_oracle_rejects_one_omitted_committed_requirements_file(security_plan):
    omitted = security_plan.security_requirements[-1]
    mutated = replace(
        security_plan,
        security_requirements=security_plan.security_requirements[:-1],
        python_commands=tuple(command for command in security_plan.python_commands if omitted not in command),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_pip_audit_scans_every_committed_runtime_and_test_requirements_file",
            REPO_ROOT,
            plan=mutated,
        )


def test_secret_canary_oracle_rejects_assertion_that_accepts_zero_findings(security_plan):
    mutated = replace(security_plan, canary_assertion=lambda document: None)
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_secret_scan_regression_fixture_proves_known_fake_secret_pattern_is_detected",
            REPO_ROOT,
            plan=mutated,
        )


def test_allowlist_metadata_oracle_rejects_missing_owner(security_plan):
    document = copy.deepcopy(dict(security_plan.allowlist_document))
    del document["exceptions"][0]["owner"]
    mutated = replace(security_plan, allowlist_document=document)
    with pytest.raises((AssertionError, ValueError)):
        validate_security_contract(
            "test_security_advisory_allowlist_entry_includes_advisory_id_dependency_justification_and_expiration_owner_metadata",
            REPO_ROOT,
            plan=mutated,
        )


def test_allowlist_expiration_oracle_rejects_already_expired_entry(security_plan):
    document = copy.deepcopy(dict(security_plan.allowlist_document))
    document["exceptions"][0]["expires_on"] = "2020-01-01"
    mutated = replace(security_plan, allowlist_document=document)
    with pytest.raises((AssertionError, ValueError)):
        validate_security_contract(
            "test_security_advisory_allowlist_fails_after_declared_expiration_date",
            REPO_ROOT,
            plan=mutated,
        )


def test_allowlist_wildcard_oracle_rejects_dependency_wildcard(security_plan):
    document = copy.deepcopy(dict(security_plan.allowlist_document))
    document["exceptions"][0]["dependency"] = "*"
    mutated = replace(security_plan, allowlist_document=document)
    with pytest.raises((AssertionError, ValueError)):
        validate_security_contract(
            "test_security_advisory_allowlist_rejects_wildcard_advisory_suppression",
            REPO_ROOT,
            plan=mutated,
        )


def test_configuration_scan_oracle_rejects_missing_production_overlay(security_plan):
    mutated = replace(
        security_plan,
        kustomize_targets=tuple(
            target for target in security_plan.kustomize_targets if not target.endswith("overlay/prod")
        ),
    )
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_trivy_configuration_scan_includes_rendered_kubernetes_and_terraform_sources",
            REPO_ROOT,
            plan=mutated,
        )


def test_secret_scan_oracle_rejects_vulnerability_only_scanner_scope(security_plan):
    arguments = tuple(
        "vuln" if value == "vuln,secret" else value
        for value in security_plan.trivy_source_arguments
    )
    mutated = replace(security_plan, trivy_source_arguments=arguments)
    with pytest.raises(AssertionError):
        validate_security_contract(
            "test_trivy_secret_scan_includes_terraform_kubernetes_workflow_and_source_directories",
            REPO_ROOT,
            plan=mutated,
        )


@pytest.mark.parametrize("requirement", ["pytest", "pytest>=8", "pytest<10", "pytest!=9.0"])
def test_python_constraint_oracle_rejects_unbounded_counterexamples(requirement):
    assert not python_requirement_is_bounded(requirement)


@pytest.mark.parametrize("requirement", ["pytest==9.0.3", "pytest>=8,<10", "pytest~=9.0"])
def test_python_constraint_oracle_accepts_reviewable_bounds(requirement):
    assert python_requirement_is_bounded(requirement)
