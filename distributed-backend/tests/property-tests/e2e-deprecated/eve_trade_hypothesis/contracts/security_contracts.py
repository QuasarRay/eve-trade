from __future__ import annotations

"""Executable, fail-closed bindings for dependency and source security gates."""

import ast
import copy
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class SecurityContractSpec:
    oracle: str
    capabilities: tuple[str, ...]


def _spec(oracle: str, *capabilities: str) -> SecurityContractSpec:
    return SecurityContractSpec(oracle=oracle, capabilities=tuple(capabilities))


SECURITY_CONTRACTS: dict[str, SecurityContractSpec] = {
    "test_govulncheck_scans_every_go_package_in_module": _spec(
        "go_package_audit", "dagger_command_plan", "go_module_scope"
    ),
    "test_cargo_audit_scans_locked_dependencies_used_by_trade_settlement_build": _spec(
        "cargo_locked_audit", "dagger_command_plan", "cargo_lockfile"
    ),
    "test_ignored_rust_advisory_dependency_is_absent_from_cargo_tree_for_runtime_target_and_all_enabled_features": _spec(
        "ignored_dependency_absence", "cargo_tree", "active_exception"
    ),
    "test_new_critical_vulnerability_fails_ci_unless_exact_advisory_has_explicit_active_exception": _spec(
        "exact_active_exception", "advisory_policy", "dagger_command_plan"
    ),
    "test_pip_audit_scans_every_committed_runtime_and_test_requirements_file": _spec(
        "python_requirement_coverage", "git_inventory", "dagger_command_plan"
    ),
    "test_python_dependency_audit_covers_e2e_requirements": _spec(
        "python_requirement_coverage", "git_inventory", "dagger_command_plan"
    ),
    "test_python_dependency_audit_covers_observability_runtime_and_test_requirements": _spec(
        "python_requirement_coverage", "git_inventory", "dagger_command_plan"
    ),
    "test_python_dependency_audit_covers_simulator_runtime_requirements": _spec(
        "python_requirement_coverage", "git_inventory", "dagger_command_plan"
    ),
    "test_python_dependency_audit_covers_simulator_test_requirements": _spec(
        "python_requirement_coverage", "git_inventory", "dagger_command_plan"
    ),
    "test_dependency_audit_allowlist_entry_is_scoped_to_dependency_absent_from_runtime_build_graph": _spec(
        "ignored_dependency_absence", "cargo_tree", "active_exception"
    ),
    "test_python_requirement_files_contain_no_unbounded_direct_dependency_versions_for_ci_critical_tools": _spec(
        "bounded_python_requirements", "git_inventory", "pep508_constraints"
    ),
    "test_secret_scan_regression_fixture_proves_known_fake_secret_pattern_is_detected": _spec(
        "trivy_secret_canary", "trivy_json", "dagger_execution"
    ),
    "test_security_advisory_allowlist_entry_includes_advisory_id_dependency_justification_and_expiration_owner_metadata": _spec(
        "allowlist_metadata", "structured_json", "advisory_policy"
    ),
    "test_security_advisory_allowlist_fails_after_declared_expiration_date": _spec(
        "allowlist_expiration", "structured_json", "time_boundary"
    ),
    "test_security_advisory_allowlist_rejects_wildcard_advisory_suppression": _spec(
        "allowlist_no_wildcards", "structured_json", "hostile_mutation"
    ),
    "test_trivy_configuration_scan_includes_rendered_kubernetes_and_terraform_sources": _spec(
        "trivy_rendered_configuration_scope",
        "kubectl_kustomize",
        "terraform_source",
        "dagger_command_plan",
    ),
    "test_trivy_secret_scan_includes_terraform_kubernetes_workflow_and_source_directories": _spec(
        "trivy_secret_repository_scope", "repository_snapshot", "dagger_command_plan"
    ),
}


@dataclass(frozen=True)
class SecurityPlan:
    security_requirements: tuple[str, ...]
    go_commands: tuple[str, ...]
    rust_commands: tuple[str, ...]
    python_commands: tuple[str, ...]
    trivy_source_arguments: tuple[str, ...]
    trivy_configuration_arguments: tuple[str, ...]
    trivy_canary_arguments: tuple[str, ...]
    kustomize_targets: tuple[str, ...]
    rust_allowlist_path: str
    rust_ignored_advisory: str
    rust_ignored_dependency: str
    rust_target: str
    allowlist_document: Mapping[str, Any]
    function_references: Mapping[str, frozenset[str]]
    function_literals: Mapping[str, frozenset[str]]
    canary_assertion: Callable[[object], None]


def implemented_security_contracts() -> frozenset[str]:
    return frozenset(SECURITY_CONTRACTS)


def binding_metadata(spec: SecurityContractSpec) -> dict[str, Any]:
    return {
        "family": "security_ci",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }


def _load_module(
    path: Path,
    name: str,
    *,
    import_directories: tuple[Path, ...] = (),
) -> ModuleType:
    before = list(sys.path)
    try:
        for directory in reversed(import_directories):
            sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"cannot import {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = before


def _function_symbols(tree: ast.AST) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    references: dict[str, frozenset[str]] = {}
    literals: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            references[node.name] = frozenset(
                [child.id for child in ast.walk(node) if isinstance(child, ast.Name)]
                + [child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)]
            )
            literals[node.name] = frozenset(
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    return references, literals


def load_security_plan(root: Path) -> SecurityPlan:
    dagger_directory = root / ".github" / "dagger"
    source_path = dagger_directory / "security.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    references, literals = _function_symbols(tree)
    module = _load_module(
        source_path,
        "eve_trade_security_policy",
        import_directories=(dagger_directory, root / "distributed-backend"),
    )
    allowlist_path = root / str(module.RUST_ADVISORY_ALLOWLIST)
    document = json.loads(allowlist_path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "security advisory allowlist root must be an object"
    return SecurityPlan(
        security_requirements=tuple(module.SECURITY_REQUIREMENTS),
        go_commands=tuple(module.GO_AUDIT_COMMANDS),
        rust_commands=tuple(module.RUST_AUDIT_COMMANDS),
        python_commands=tuple(module.PYTHON_AUDIT_COMMANDS),
        trivy_source_arguments=tuple(module.TRIVY_SOURCE_ARGUMENTS),
        trivy_configuration_arguments=tuple(module.TRIVY_CONFIGURATION_ARGUMENTS),
        trivy_canary_arguments=tuple(module.TRIVY_CANARY_ARGUMENTS),
        kustomize_targets=tuple(module.KUSTOMIZE_TARGETS),
        rust_allowlist_path=str(module.RUST_ADVISORY_ALLOWLIST),
        rust_ignored_advisory=str(module.RUST_IGNORED_ADVISORY),
        rust_ignored_dependency=str(module.RUST_IGNORED_DEPENDENCY),
        rust_target=str(module.RUST_TARGET),
        allowlist_document=document,
        function_references=references,
        function_literals=literals,
        canary_assertion=module.assert_trivy_canary_detected,
    )


def _allowlist_module(root: Path) -> ModuleType:
    path = root / ".github" / "scripts" / "validate_security_advisory_allowlist.py"
    return _load_module(path, "eve_trade_security_allowlist")


def _assert_wired(plan: SecurityPlan, function: str, constant: str) -> None:
    assert function in plan.function_references, f"missing security subcheck {function}"
    assert constant in plan.function_references[function], (
        f"{function} does not execute the reviewed {constant} plan"
    )


def _git_files(root: Path, pattern: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", pattern],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return tuple(sorted(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()))


def _product_requirement_files(root: Path) -> tuple[str, ...]:
    return tuple(
        path
        for path in _git_files(root, "*requirements*.txt")
        if not path.startswith((".agents/", "vendor/"))
    )


def _direct_python_requirements(root: Path) -> tuple[tuple[str, int, str], ...]:
    records: list[tuple[str, int, str]] = []
    for relative in _product_requirement_files(root):
        for number, raw in enumerate((root / relative).read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "--", "\\")):
                continue
            if line.startswith("sha256:"):
                continue
            records.append((relative, number, line.rstrip(" \\")))
    return tuple(records)


def python_requirement_is_bounded(requirement: str) -> bool:
    specifier = requirement.split(";", 1)[0]
    operators = re.findall(r"===|==|~=|>=|<=|!=|>|<", specifier)
    exact = any(operator in {"==", "==="} for operator in operators)
    lower = any(operator in {">=", ">", "~="} for operator in operators)
    upper = any(operator in {"<", "<="} for operator in operators) or "~=" in operators
    return exact or (lower and upper)


def _active_exceptions(root: Path, plan: SecurityPlan, *, today: dt.date | None = None):
    module = _allowlist_module(root)
    return module.validate_document(copy.deepcopy(dict(plan.allowlist_document)), today=today)


def _ignored_advisories(commands: tuple[str, ...]) -> frozenset[str]:
    values: set[str] = set()
    for command in commands:
        if not command.startswith("cargo audit "):
            continue
        values.update(re.findall(r"(?:^|\s)--ignore\s+(RUSTSEC-[0-9-]+)(?=\s|$)", command))
    return frozenset(values)


@lru_cache(maxsize=8)
def _cargo_tree_inverse(root_text: str, target: str, dependency: str) -> str:
    root = Path(root_text)
    result = subprocess.run(
        [
            "cargo",
            "tree",
            "--locked",
            "--target",
            target,
            "--all-features",
            "-i",
            dependency,
        ],
        cwd=root / "distributed-backend" / "src" / "trade-settlement",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _argument_value(arguments: tuple[str, ...], option: str) -> str:
    indexes = [index for index, value in enumerate(arguments) if value == option]
    assert len(indexes) == 1, f"expected exactly one {option}: {arguments!r}"
    index = indexes[0]
    assert index + 1 < len(arguments), f"missing value for {option}"
    return arguments[index + 1]


def validate_security_contract(
    name: str,
    root: Path,
    *,
    plan: SecurityPlan | None = None,
    execute_cargo_tree: bool = True,
) -> None:
    assert name in SECURITY_CONTRACTS, f"unknown security semantic contract: {name}"
    plan = plan or load_security_plan(root)
    oracle = SECURITY_CONTRACTS[name].oracle

    if oracle == "go_package_audit":
        _assert_wired(plan, "go_audit", "GO_AUDIT_COMMANDS")
        modules = tuple(path for path in _git_files(root, "*go.mod") if not path.startswith((".agents/", "vendor/")))
        assert modules == ("go.mod",), f"review Go audit workdir for modules: {modules!r}"
        assert plan.go_commands.count("govulncheck ./...") == 1
        assert not any("-tags=" in command or "-tags " in command for command in plan.go_commands)
        return

    if oracle == "cargo_locked_audit":
        _assert_wired(plan, "rust_audit", "RUST_AUDIT_COMMANDS")
        assert _git_files(root, "*Cargo.lock") == (
            "distributed-backend/src/trade-settlement/Cargo.lock",
        )
        assert "cargo install cargo-audit --locked --version 0.22.2" in plan.rust_commands
        commands = [command for command in plan.rust_commands if command.startswith("cargo audit ")]
        assert len(commands) == 1
        assert "--deny warnings" in commands[0]
        assert "/src/distributed-backend/src/trade-settlement" in plan.function_literals["rust_audit"]
        return

    if oracle == "ignored_dependency_absence":
        _assert_wired(plan, "rust_audit", "RUST_AUDIT_COMMANDS")
        expected = (
            f'test -z "$(cargo tree --locked --target {plan.rust_target} '
            f'--all-features -i {plan.rust_ignored_dependency})"'
        )
        assert plan.rust_commands.count(expected) == 1
        exceptions = _active_exceptions(root, plan)
        exact = [
            item
            for item in exceptions
            if item["advisory_id"] == plan.rust_ignored_advisory
            and item["dependency"] == plan.rust_ignored_dependency
            and item["target"] == plan.rust_target
            and item["features"] == "all"
        ]
        assert len(exact) == 1, "ignored advisory lacks one exact active exception"
        if execute_cargo_tree:
            assert _cargo_tree_inverse(str(root), plan.rust_target, plan.rust_ignored_dependency) == ""
        return

    if oracle == "exact_active_exception":
        _assert_wired(plan, "rust_audit", "RUST_AUDIT_COMMANDS")
        module = _allowlist_module(root)
        exceptions = _active_exceptions(root, plan)
        configured_ids = frozenset(str(item["advisory_id"]) for item in exceptions)
        assert _ignored_advisories(plan.rust_commands) == configured_ids
        assert not module.exception_is_active(
            exceptions,
            advisory_id="RUSTSEC-2099-9999",
            dependency="hostile-new-crate",
            severity="CRITICAL",
        )
        simulated = copy.deepcopy(dict(plan.allowlist_document))
        simulated["exceptions"].append(
            {
                "advisory_id": "RUSTSEC-2099-9999",
                "dependency": "hostile-new-crate",
                "justification": "Hostile control proving only an exact reviewed exception is accepted.",
                "expires_on": "2099-12-31",
                "owner": "test-security-owner",
                "status": "active",
                "target": plan.rust_target,
                "features": "all",
                "severities": ["CRITICAL"],
            }
        )
        simulated_exceptions = module.validate_document(simulated, today=dt.date(2026, 8, 12))
        assert module.exception_is_active(
            simulated_exceptions,
            advisory_id="RUSTSEC-2099-9999",
            dependency="hostile-new-crate",
            severity="CRITICAL",
        )
        assert not module.exception_is_active(
            simulated_exceptions,
            advisory_id="RUSTSEC-2099-9999",
            dependency="different-crate",
            severity="CRITICAL",
        )
        return

    if oracle == "python_requirement_coverage":
        _assert_wired(plan, "python_audit", "PYTHON_AUDIT_COMMANDS")
        expected = _product_requirement_files(root)
        assert tuple(sorted(plan.security_requirements)) == expected
        audited = tuple(
            sorted(
                command.removeprefix("pip-audit --requirement ")
                for command in plan.python_commands
                if command.startswith("pip-audit --requirement ")
            )
        )
        assert audited == expected
        assert len(audited) == len(set(audited))
        return

    if oracle == "bounded_python_requirements":
        records = _direct_python_requirements(root)
        assert records, "no product Python requirements were discovered"
        unbounded: list[tuple[str, int, str]] = []
        for relative, number, requirement in records:
            if not python_requirement_is_bounded(requirement):
                unbounded.append((relative, number, requirement))
        assert not unbounded, f"unbounded direct Python requirements: {unbounded!r}"
        return

    if oracle == "trivy_secret_canary":
        _assert_wired(plan, "trivy_secret_canary", "TRIVY_CANARY_ARGUMENTS")
        assert _argument_value(plan.trivy_canary_arguments, "--scanners") == "secret"
        assert _argument_value(plan.trivy_canary_arguments, "--format") == "json"
        assert _argument_value(plan.trivy_canary_arguments, "--output") == "/tmp/trivy-canary.json"
        assert plan.trivy_canary_arguments[-1] == "/canary"
        plan.canary_assertion(
            {"Results": [{"Target": "credentials", "Secrets": [{"RuleID": "fake-secret"}]}]}
        )
        try:
            plan.canary_assertion({"Results": [{"Target": "credentials", "Secrets": []}]})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Trivy canary assertion accepts an empty finding set")
        return

    if oracle == "allowlist_metadata":
        exceptions = _active_exceptions(root, plan)
        assert exceptions, "security advisory allowlist must contain a reviewed exception"
        required = {
            "advisory_id",
            "dependency",
            "justification",
            "expires_on",
            "owner",
            "status",
            "target",
            "features",
            "severities",
        }
        assert all(required <= item.keys() for item in exceptions)
        return

    if oracle == "allowlist_expiration":
        module = _allowlist_module(root)
        exceptions = _active_exceptions(root, plan)
        assert exceptions
        for item in exceptions:
            isolated = {"schema_version": 1, "exceptions": [copy.deepcopy(dict(item))]}
            expires = dt.date.fromisoformat(str(item["expires_on"]))
            try:
                module.validate_document(isolated, today=expires + dt.timedelta(days=1))
            except module.AllowlistError:
                continue
            raise AssertionError(f"allowlist accepted expired advisory {item['advisory_id']}")
        return

    if oracle == "allowlist_no_wildcards":
        module = _allowlist_module(root)
        _active_exceptions(root, plan)
        hostile = copy.deepcopy(dict(plan.allowlist_document))
        assert hostile.get("exceptions")
        hostile["exceptions"][0]["advisory_id"] = "RUSTSEC-*"
        try:
            module.validate_document(hostile, today=dt.date(2026, 1, 1))
        except module.AllowlistError:
            return
        raise AssertionError("allowlist accepted wildcard advisory suppression")

    if oracle == "trivy_rendered_configuration_scope":
        _assert_wired(plan, "trivy_rendered_configuration", "KUSTOMIZE_TARGETS")
        _assert_wired(plan, "trivy_rendered_configuration", "TRIVY_CONFIGURATION_ARGUMENTS")
        refs = plan.function_references["trivy_rendered_configuration"]
        assert {"with_source", "with_directory"} <= refs
        assert "/src/.rendered-kubernetes" in plan.function_literals["trivy_rendered_configuration"]
        assert _argument_value(plan.trivy_configuration_arguments, "--scanners") == "misconfig"
        assert _argument_value(plan.trivy_configuration_arguments, "--exit-code") == "1"
        assert plan.trivy_configuration_arguments[-1] == "."
        assert not any(value.startswith("--skip-") for value in plan.trivy_configuration_arguments)
        assert (root / "distributed-backend" / "terraform").is_dir()
        assert plan.kustomize_targets
        for target in plan.kustomize_targets:
            assert (root / target / "kustomization.yaml").is_file(), target
        required_production_targets = {
            "distributed-backend/orchestration/kubernetes/platform/istio/prod",
            "distributed-backend/orchestration/kubernetes/platform/gateway/prod",
            "distributed-backend/orchestration/kubernetes/overlay/prod",
            "distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod",
        }
        assert required_production_targets <= set(plan.kustomize_targets)
        return

    if oracle == "trivy_secret_repository_scope":
        _assert_wired(plan, "trivy_source", "TRIVY_SOURCE_ARGUMENTS")
        assert "with_source" in plan.function_references["trivy_source"]
        scanners = set(_argument_value(plan.trivy_source_arguments, "--scanners").split(","))
        assert "secret" in scanners
        assert _argument_value(plan.trivy_source_arguments, "--exit-code") == "1"
        assert plan.trivy_source_arguments[-1] == "."
        assert not any(value.startswith("--skip-") for value in plan.trivy_source_arguments)
        for relative in (
            ".github/workflows",
            "distributed-backend/terraform",
            "distributed-backend/orchestration/kubernetes",
            "distributed-backend/src",
        ):
            assert (root / relative).exists(), relative
        return

    raise AssertionError(f"unhandled security oracle: {oracle}")
