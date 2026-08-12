from __future__ import annotations

"""Exact source-to-test traceability matrices for repository-owned surfaces."""

import ast
import importlib.util
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class TraceabilityContractSpec:
    oracle: str
    capabilities: tuple[str, ...]


TRACEABILITY_CONTRACTS = {
    "test_every_pubsub_handler_has_duplicate_delivery_test_reference": TraceabilityContractSpec(
        "pubsub_duplicate_matrix", ("structured_go", "exact_handler_inventory", "native_test_body")
    ),
    "test_every_terraform_root_has_terraform_test_fixture_reference": TraceabilityContractSpec(
        "terraform_root_matrix", ("filesystem_inventory", "structured_hcl", "workflow_matrix")
    ),
    "test_every_kubernetes_production_overlay_has_render_and_security_test_reference": TraceabilityContractSpec(
        "kubernetes_overlay_matrix", ("kustomization_inventory", "dagger_ast", "security_scan_plan")
    ),
    "test_test_catalog_fails_when_referenced_test_name_no_longer_exists_in_collected_suite": TraceabilityContractSpec(
        "physical_pytest_collection", ("pytest_collect_only", "canonical_contract_ids", "exact_cardinality")
    ),
}


def binding_metadata(spec: TraceabilityContractSpec) -> dict[str, Any]:
    return {
        "family": "repository_traceability",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }


@lru_cache(maxsize=4)
def _nsq_validator(path_text: str) -> ModuleType:
    path = Path(path_text)
    name = "eve_trade_traceability_nsq_parser"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contains(values: Sequence[str], expected: Sequence[str]) -> bool:
    width = len(expected)
    return any(values[index : index + width] == list(expected) for index in range(len(values) - width + 1))


def _count(values: Sequence[str], expected: Sequence[str]) -> int:
    width = len(expected)
    return sum(values[index : index + width] == list(expected) for index in range(len(values) - width + 1))


def _go_function_values(module: ModuleType, source: str, function_name: str) -> list[str]:
    tokens = module._go_tokens(source)
    values = [token.value for token in tokens]
    signature = ["func", function_name, "("]
    starts = [index for index in range(len(values)) if values[index : index + 3] == signature]
    assert len(starts) == 1, f"expected exactly one Go function {function_name}"
    body_start = next(
        index for index in range(starts[0] + 3, len(tokens)) if tokens[index].value == "{"
    )
    body_end = module._matching_index(tokens, body_start)
    return values[body_start + 1 : body_end]


def _go_subtest_values(module: ModuleType, source: str, subtest_name: str) -> list[str]:
    tokens = module._go_tokens(source)
    for index in range(len(tokens)):
        call = module._call_arguments(tokens, index, ("t", ".", "Run"))
        if call is None:
            continue
        arguments, _ = call
        if (
            len(arguments) == 2
            and len(arguments[0]) == 1
            and arguments[0][0].kind == "string"
            and arguments[0][0].value == subtest_name
        ):
            return [token.value for token in arguments[1]]
    raise AssertionError(f"missing Go subtest {subtest_name}")


def _validate_pubsub_duplicate_matrix(root: Path) -> None:
    module = _nsq_validator(str(root / "scripts/validate_nsq_configuration.py"))
    state = module.load_repository_state(root)
    assert set(state.subscriptions) == {
        "trade-settlement-executor",
        "market-settlement-result-projection",
    }, "every discovered production subscription needs an explicit duplicate-delivery test binding"

    worker_source = (root / "distributed-backend/src/settlementworker/regression_lifecycle_test.go").read_text(
        encoding="utf-8"
    )
    worker_test = _go_subtest_values(
        module,
        worker_source,
        "test_duplicate_delivery_does_not_publish_duplicate_terminal_result",
    )
    assert _count(worker_test, ("service", ".", "HandleSettlementWork", "(")) == 2
    assert _count(worker_test, ("HaveLen", "(", "1", ")")) == 2

    result_source = (root / "distributed-backend/src/market/settlement_lifecycle_test.go").read_text(
        encoding="utf-8"
    )
    result_test = _go_function_values(
        module, result_source, "TestDuplicateSettlementResultProjectionIsHarmless"
    )
    assert _contains(
        result_test,
        ("for", "attempt", ":", "=", "1", ";", "attempt", "<", "=", "2", ";", "attempt", "+", "+"),
    )
    assert _count(result_test, ("validateSettlementResult", "(", "result", ",", "operation", ")")) == 1


def _terraform_roots(root: Path) -> frozenset[str]:
    terraform = root / "distributed-backend/terraform"
    return frozenset(
        str(path.parent.relative_to(root)).replace("\\", "/")
        for path in terraform.rglob(".terraform.lock.hcl")
        if any(path.parent.glob("*.tf"))
    )


def _workflow_terraform_roots(root: Path) -> frozenset[str]:
    import yaml

    workflow = yaml.safe_load((root / ".github/workflows/verify.yaml").read_text(encoding="utf-8"))
    terraform_job = (workflow.get("jobs") or {}).get("terraform")
    assert isinstance(terraform_job, dict)
    matrix = (((terraform_job.get("strategy") or {}).get("matrix") or {}).get("include"))
    assert isinstance(matrix, list) and matrix
    roots: set[str] = set()
    for entry in matrix:
        assert isinstance(entry, dict)
        assert set(entry) >= {"provider", "root", "launcher", "lockfile_args"}
        assert entry["launcher"] == f"terraform_{entry['provider'].replace('-', '_')}.py"
        assert entry["lockfile_args"] == "-lockfile=readonly"
        roots.add(str(entry["root"]).replace("\\", "/"))
    return frozenset(roots)


def _validate_terraform_root_matrix(root: Path) -> None:
    roots = _terraform_roots(root)
    assert roots, "no locked Terraform roots discovered"
    fixtures: set[str] = set()
    for relative in roots:
        test_files = tuple(sorted((root / relative / "tests").glob("*.tftest.hcl")))
        assert test_files, f"Terraform root has no native terraform-test fixture: {relative}"
        assert all(path.read_text(encoding="utf-8").strip() for path in test_files)
        fixtures.add(relative)
    assert fixtures == set(roots)
    assert _workflow_terraform_roots(root) == roots


def _production_kustomizations(root: Path) -> frozenset[str]:
    kubernetes = root / "distributed-backend/orchestration/kubernetes"
    return frozenset(
        str(path.parent.relative_to(root)).replace("\\", "/")
        for path in kubernetes.rglob("kustomization.yaml")
        if path.parent.name == "prod"
    )


def _python_string_assignment(tree: ast.AST, name: str) -> frozenset[str]:
    matches: list[frozenset[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: Iterable[ast.expr] = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        assert isinstance(value, (ast.List, ast.Tuple))
        strings = frozenset(
            element.value
            for element in value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
        assert len(strings) == len(value.elts)
        matches.append(strings)
    assert len(matches) == 1, f"expected one structured assignment for {name}"
    return matches[0]


def _validate_kubernetes_overlay_matrix(root: Path) -> None:
    overlays = _production_kustomizations(root)
    assert overlays, "no production Kustomize overlays discovered"
    kubernetes_path = root / ".github/dagger/kubernetes.py"
    kubernetes_tree = ast.parse(kubernetes_path.read_text(encoding="utf-8"), filename=str(kubernetes_path))
    rendered = frozenset(
        value.split(" ", 2)[2].split(" >", 1)[0]
        for value in (
            node.value
            for node in ast.walk(kubernetes_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        if value.startswith("kubectl kustomize ") and " >" in value
    )
    security_path = root / ".github/dagger/security.py"
    security_tree = ast.parse(security_path.read_text(encoding="utf-8"), filename=str(security_path))
    scanned = _python_string_assignment(security_tree, "KUSTOMIZE_TARGETS")
    assert overlays <= rendered, f"production overlays missing from Kubernetes render gate: {sorted(overlays - rendered)}"
    assert overlays <= scanned, f"production overlays missing from Trivy rendered scan: {sorted(overlays - scanned)}"


def assert_exact_collected_catalog(authoritative: Sequence[str], collected: Sequence[str]) -> None:
    duplicate_authoritative = sorted(
        name for name, count in Counter(authoritative).items() if count != 1
    )
    duplicate_collected = sorted(name for name, count in Counter(collected).items() if count != 1)
    missing = sorted(set(authoritative) - set(collected))
    extra = sorted(set(collected) - set(authoritative))
    assert not duplicate_authoritative, f"duplicate authoritative IDs: {duplicate_authoritative}"
    assert not duplicate_collected, f"duplicate collected IDs: {duplicate_collected}"
    assert not missing, f"catalog IDs missing from collected suite: {missing}"
    assert not extra, f"collected IDs absent from catalog: {extra}"
    assert len(authoritative) == len(collected)


def _validate_physical_pytest_collection(root: Path) -> None:
    property_root = root / "distributed-backend/tests/property-tests"
    e2e_root = property_root / "e2e"
    generated = e2e_root / "eve_trade_hypothesis/generated"
    authoritative = re.findall(
        r"`(test_[a-z0-9_]+)`",
        (property_root / "tests-to-implement.md").read_text(encoding="utf-8"),
    )
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(generated)],
        cwd=e2e_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    assert process.returncode == 0, (
        f"pytest collection failed ({process.returncode})\n"
        f"stdout={process.stdout[-12000:]}\nstderr={process.stderr[-12000:]}"
    )
    collected = []
    for line in process.stdout.splitlines():
        if "::test_" not in line:
            continue
        name = line.rsplit("::", 1)[-1].split("[", 1)[0]
        if re.fullmatch(r"test_[a-z0-9_]+", name):
            collected.append(name)
    assert_exact_collected_catalog(authoritative, collected)


def validate_traceability_contract(name: str, root: Path) -> None:
    assert name in TRACEABILITY_CONTRACTS, f"unknown traceability contract: {name}"
    oracle = TRACEABILITY_CONTRACTS[name].oracle
    if oracle == "pubsub_duplicate_matrix":
        _validate_pubsub_duplicate_matrix(root)
        return
    if oracle == "terraform_root_matrix":
        _validate_terraform_root_matrix(root)
        return
    if oracle == "kubernetes_overlay_matrix":
        _validate_kubernetes_overlay_matrix(root)
        return
    if oracle == "physical_pytest_collection":
        _validate_physical_pytest_collection(root)
        return
    raise AssertionError(f"unhandled traceability oracle: {oracle}")
