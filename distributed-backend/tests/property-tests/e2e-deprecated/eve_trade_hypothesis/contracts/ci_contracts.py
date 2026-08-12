from __future__ import annotations

"""Exact semantic bindings for CI evidence and production-gate contracts.

Workflow claims are evaluated from parsed job/step structure.  Evidence claims
execute the repository's real producer, envelope validator, and aggregate gate
against valid and deliberately corrupted bundles.  Contract names are registry
keys only; no oracle is selected from name substrings.
"""

import copy
import importlib.util
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class CIContractSpec:
    oracle: str
    capabilities: tuple[str, ...]
    parameters: tuple[tuple[str, str], ...] = ()


def _spec(
    oracle: str,
    *capabilities: str,
    **parameters: str,
) -> CIContractSpec:
    return CIContractSpec(
        oracle=oracle,
        capabilities=tuple(capabilities),
        parameters=tuple(sorted(parameters.items())),
    )


CI_CONTRACTS: dict[str, CIContractSpec] = {
    # CI evidence and artifact integrity.
    "test_ci_evidence_artifact_cannot_be_reused_for_different_commit_sha": _spec(
        "evidence_context_reuse", "ci_runtime", "signed_evidence"
    ),
    "test_ci_evidence_artifact_checksum_mismatch_causes_production_gate_failure": _spec(
        "evidence_checksum_gate", "ci_runtime", "signed_evidence"
    ),
    "test_ci_evidence_artifact_contains_checked_out_commit_sha": _spec(
        "evidence_contains_commit", "ci_runtime", "signed_evidence"
    ),
    "test_ci_evidence_command_identity_is_unique_for_each_required_verification_job": _spec(
        "producer_command_identity", "structured_workflow"
    ),
    "test_ci_evidence_finish_status_matches_actual_github_job_status": _spec(
        "producer_finish_status", "structured_workflow"
    ),
    "test_every_required_verify_job_emits_ci_evidence_finish_record_even_after_failure": _spec(
        "producer_finish_after_failure", "structured_workflow"
    ),
    "test_every_required_verify_job_emits_ci_evidence_start_record": _spec(
        "producer_start_records", "structured_workflow"
    ),
    "test_missing_ci_evidence_artifact_causes_production_gate_failure": _spec(
        "missing_evidence_gate", "ci_runtime", "signed_evidence"
    ),
    "test_production_deployment_consumes_evidence_only_from_same_workflow_run_and_commit": _spec(
        "same_run_release_gate", "structured_workflow", "signed_evidence"
    ),
    "test_truncated_ci_evidence_artifact_causes_production_gate_failure": _spec(
        "truncated_evidence_gate", "ci_runtime", "signed_evidence"
    ),
    # GitHub workflow security.  The cache-key contract is intentionally absent:
    # the current workflow has no dependency cache whose key can be exercised.
    "test_artifact_upload_rejects_missing_required_evidence_files": _spec(
        "artifact_missing_policy", "structured_workflow"
    ),
    "test_job_requesting_write_permission_declares_only_minimum_required_permission_scope": _spec(
        "minimum_write_permissions", "structured_workflow"
    ),
    "test_production_deploy_job_cannot_run_from_pull_request_event": _spec(
        "release_push_only", "structured_workflow"
    ),
    "test_production_deploy_job_requires_immutable_commit_sha_rather_than_branch_head_lookup": _spec(
        "release_immutable_sha", "structured_workflow"
    ),
    "test_pull_request_workflow_does_not_expose_production_secrets_to_untrusted_fork_code": _spec(
        "pull_request_secret_boundary", "structured_workflow"
    ),
    "test_security_scan_job_runs_even_when_earlier_nondependent_verification_job_fails": _spec(
        "security_independent_root", "structured_workflow"
    ),
    "test_shell_steps_handling_untrusted_github_context_do_not_interpolate_it_directly_into_executable_shell": _spec(
        "untrusted_context_shell_boundary", "structured_workflow"
    ),
    "test_workflow_concurrency_prevents_two_production_deployments_from_racing_same_environment_when_policy_requires_serialization": _spec(
        "release_concurrency", "structured_workflow"
    ),
    "test_workflow_permissions_default_to_read_only_unless_individual_job_requires_write": _spec(
        "workflow_read_only_permissions", "structured_workflow"
    ),
    # Production E2E gate behavior.
    "test_production_gate_fails_when_required_test_fixture_skips": _spec(
        "production_skip_gate", "pytest_runtime", risk_group="fixture"
    ),
    "test_production_gate_fails_if_pytest_collects_zero_tests": _spec(
        "production_zero_collection", "pytest_runtime"
    ),
    "test_production_gate_fails_if_all_load_tests_are_skipped": _spec(
        "production_skip_gate", "pytest_runtime", risk_group="load"
    ),
    "test_production_gate_fails_if_all_security_tests_are_skipped": _spec(
        "production_skip_gate", "pytest_runtime", risk_group="security"
    ),
    "test_production_gate_fails_if_all_crash_recovery_tests_are_skipped": _spec(
        "production_skip_gate", "pytest_runtime", risk_group="crash_recovery"
    ),
    "test_production_gate_fails_when_required_environment_variable_contains_placeholder_value": _spec(
        "production_placeholder_gate", "pytest_runtime"
    ),
    # Canonical regression inventory uses the repository's executable AST/source
    # verifier, including its exact-cardinality and digest checks.
    "test_regression_canonical_test_catalog_contains_every_required_contract": _spec(
        "canonical_inventory", "source_ast", "canonical_manifest"
    ),
    "test_regression_canonical_test_catalog_rejects_removed_contract_without_explicit_update": _spec(
        "canonical_removal_canary", "source_ast", "canonical_manifest"
    ),
}


REQUIRED_PRODUCERS = (
    "proto",
    "go",
    "rust-trade-settlement",
    "terraform",
    "kubernetes",
    "python",
    "architecture",
    "gui-contract",
    "security",
    "canonical-regression",
    "e2e",
)
EVIDENCE_ACTION = "./.github/actions/ci-evidence"
UPLOAD_ARTIFACT_PREFIX = "actions/upload-artifact@"
DOWNLOAD_ARTIFACT_PREFIX = "actions/download-artifact@"


def implemented_ci_contracts() -> frozenset[str]:
    return frozenset(CI_CONTRACTS)


def binding_metadata(spec: CIContractSpec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "family": "ci_and_production_gate",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }
    if spec.parameters:
        result["parameters"] = dict(spec.parameters)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"YAML root must be an object: {path}"
    return value


@lru_cache(maxsize=None)
def _load_module(path_text: str, module_name: str) -> ModuleType:
    path = Path(path_text)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _production_modules(root: Path) -> tuple[ModuleType, ModuleType, ModuleType]:
    backend = str(root / "distributed-backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from observability.ci import ci_aggregate, ci_evidence

    boundary = _load_module(
        str(root / ".github" / "dagger" / "_validate_evidence.py"),
        "eve_trade_property_ci_evidence_boundary",
    )
    return ci_evidence, ci_aggregate, boundary


def _production_gate_module(root: Path) -> ModuleType:
    return _load_module(
        str(root / "distributed-backend" / "tests" / "e2e" / "production_gate.py"),
        "eve_trade_property_production_gate",
    )


def _canonical_verifier(root: Path) -> ModuleType:
    return _load_module(
        str(root / "scripts" / "verify_canonical_regression_tests.py"),
        "eve_trade_property_canonical_verifier",
    )


def _github_context(ci_evidence: ModuleType) -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "astral/eve-trade",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_WORKFLOW": "Verify",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_NAME": "push",
        "RUNNER_NAME": "contract-runner",
        "RUNNER_OS": "Linux",
    }


def _signed_bundle(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    ci_evidence, _, _ = _production_modules(root)
    environment = _github_context(ci_evidence)
    with tempfile.TemporaryDirectory(prefix="eve-ci-evidence-") as directory:
        base = Path(directory)
        start = base / "start.json"
        output = base / "producer.json"
        ci_evidence.start_evidence(
            start,
            job_id="security",
            job_name="security / dependencies and source",
            environment=environment,
        )
        bundle = ci_evidence.finish_evidence(
            start,
            output,
            job_id="security",
            job_name="security / dependencies and source",
            step_identity="ci-evidence/finalize",
            command_identity="dagger/security",
            status="success",
            dependencies=[],
            environment=environment,
        )
    return bundle, ci_evidence.github_context(environment)


def _jobs(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow has no jobs"
    return jobs


def _steps(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps") or []
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _needs(job: Mapping[str, Any]) -> tuple[str, ...]:
    value = job.get("needs")
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    assert isinstance(value, list)
    return tuple(str(item) for item in value)


def _evidence_steps(job: Mapping[str, Any], mode: str) -> list[dict[str, Any]]:
    return [
        step
        for step in _steps(job)
        if step.get("uses") == EVIDENCE_ACTION
        and (step.get("with") or {}).get("mode") == mode
    ]


def _producer_jobs(workflow: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    jobs = _jobs(workflow)
    aggregate = jobs.get("o11y-aggregate")
    assert isinstance(aggregate, dict), "o11y-aggregate job is missing"
    assert _needs(aggregate) == REQUIRED_PRODUCERS, (
        "aggregate producer set changed",
        _needs(aggregate),
    )
    return [(job_id, jobs[job_id]) for job_id in REQUIRED_PRODUCERS]


def _release_job(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates: list[Mapping[str, Any]] = []
    for job in _jobs(workflow).values():
        if not isinstance(job, dict):
            continue
        permissions = job.get("permissions") or {}
        runs = "\n".join(str(step.get("run") or "") for step in _steps(job))
        if permissions.get("packages") == "write" or "docker push" in runs:
            candidates.append(job)
    assert len(candidates) == 1, f"expected one package publisher, found {len(candidates)}"
    return candidates[0]


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _oracle_producer_start_records(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    for job_id, job in _producer_jobs(workflow):
        records = _evidence_steps(job, "start")
        assert len(records) == 1, (job_id, "expected exactly one start record")
        fields = records[0].get("with") or {}
        assert fields.get("job-id") == job_id, (job_id, fields.get("job-id"))
        assert fields.get("artifact-key"), (job_id, "empty artifact key")


def _oracle_producer_finish_after_failure(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    for job_id, job in _producer_jobs(workflow):
        records = _evidence_steps(job, "finish")
        assert len(records) == 1, (job_id, "expected exactly one finish record")
        record = records[0]
        assert "always()" in str(record.get("if") or ""), (job_id, "finish is not always-run")
        fields = record.get("with") or {}
        assert fields.get("job-id") == job_id
        assert fields.get("status") == "${{ job.status }}"


def _oracle_producer_finish_status(workflow: Mapping[str, Any], action: Mapping[str, Any]) -> None:
    _oracle_producer_finish_after_failure(workflow, action)
    steps = ((action.get("runs") or {}).get("steps") or [])
    finish = [step for step in steps if isinstance(step, dict) and step.get("name") == "Finalize producer evidence"]
    assert len(finish) == 1
    assert '--status "${{ inputs.status }}"' in str(finish[0].get("run") or "")


def _oracle_producer_command_identity(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    identities: list[str] = []
    for job_id, job in _producer_jobs(workflow):
        finish = _evidence_steps(job, "finish")
        assert len(finish) == 1, job_id
        identity = str((finish[0].get("with") or {}).get("command-identity") or "")
        assert identity and identity != "unspecified", job_id
        identities.append(identity)
    assert len(identities) == len(set(identities)), identities


def _oracle_artifact_missing_policy(workflow: Mapping[str, Any], action: Mapping[str, Any]) -> None:
    action_steps = ((action.get("runs") or {}).get("steps") or [])
    uploads = [
        step
        for step in action_steps
        if isinstance(step, dict) and str(step.get("uses") or "").startswith(UPLOAD_ARTIFACT_PREFIX)
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    fields = upload.get("with") or {}
    assert fields.get("if-no-files-found") == "error"
    assert fields.get("path") == ".o11y/producer-artifacts/${{ inputs.artifact-key }}.json"
    assert fields.get("include-hidden-files") is True
    assert upload.get("if") == "inputs.mode == 'finish'"


def _oracle_workflow_read_only_permissions(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    assert workflow.get("permissions") == {"contents": "read"}
    publisher = _release_job(workflow)
    for job_id, job in _jobs(workflow).items():
        if not isinstance(job, dict) or "permissions" not in job:
            continue
        permissions = job["permissions"]
        assert isinstance(permissions, dict), job_id
        if any(value == "write" for value in permissions.values()):
            assert job == publisher, job_id


def _oracle_minimum_write_permissions(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    _oracle_workflow_read_only_permissions(workflow, {})
    publisher = _release_job(workflow)
    assert publisher.get("permissions") == {"contents": "read", "packages": "write"}
    all_write = []
    for job_id, job in _jobs(workflow).items():
        permissions = job.get("permissions") if isinstance(job, dict) else None
        if isinstance(permissions, dict):
            all_write.extend((job_id, scope) for scope, value in permissions.items() if value == "write")
    assert all_write == [("release-verification", "packages")], all_write


def _release_condition(workflow: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(_release_job(workflow).get("if") or "")).strip()


def _oracle_release_push_only(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    condition = _release_condition(workflow)
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "pull_request" not in condition
    triggers = workflow.get(True) or workflow.get("on") or {}
    assert "pull_request" in triggers, "negative event is not part of workflow trigger set"


def _oracle_release_immutable_sha(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    publisher = _release_job(workflow)
    condition = _release_condition(workflow)
    assert "needs.o11y-aggregate.outputs.verified_sha == github.sha" in condition
    runs = "\n".join(str(step.get("run") or "") for step in _steps(publisher))
    assert 'test "$VERIFIED_SHA" = "$GITHUB_SHA"' in runs
    for step in _steps(publisher):
        run = str(step.get("run") or "")
        if "release.py build" in run or "docker push" in run or "release.py verify" in run:
            assert (step.get("env") or {}).get("VERIFIED_SHA") == "${{ needs.o11y-aggregate.outputs.verified_sha }}"
            assert "refs/heads/main" not in run or 'test "$GITHUB_REF" = "refs/heads/main"' in run


def _oracle_pull_request_secret_boundary(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    secret_jobs = []
    for job_id, job in _jobs(workflow).items():
        if not isinstance(job, dict):
            continue
        if any("secrets." in value for value in _all_strings(job)):
            secret_jobs.append((job_id, job))
    assert secret_jobs, "workflow has no credentialed production boundary to verify"
    assert [job_id for job_id, _ in secret_jobs] == ["release-verification"]
    for _, job in secret_jobs:
        condition = re.sub(r"\s+", " ", str(job.get("if") or ""))
        assert "github.event_name == 'push'" in condition
        assert "github.ref == 'refs/heads/main'" in condition


def _oracle_security_independent_root(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    security = _jobs(workflow).get("security")
    assert isinstance(security, dict)
    assert _needs(security) == ()
    condition = str(security.get("if") or "")
    assert "success()" not in condition and "needs." not in condition
    assert len(_evidence_steps(security, "run")) == 1


def _oracle_untrusted_context_shell_boundary(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    offenders: list[tuple[str, str, str]] = []
    expressions = re.compile(r"\$\{\{\s*(github\.[^}]+?)\s*\}\}")
    for job_id, job in _jobs(workflow).items():
        if not isinstance(job, dict):
            continue
        for step in _steps(job):
            run = str(step.get("run") or "")
            for expression in expressions.findall(run):
                offenders.append((job_id, str(step.get("name") or ""), expression))
    assert not offenders, (
        "GitHub context must cross an env/input boundary before shell execution",
        offenders,
    )


def _oracle_release_concurrency(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict)
    group = str(concurrency.get("group") or "")
    assert "${{ github.workflow }}" in group
    assert "${{ github.ref }}" in group
    assert concurrency.get("cancel-in-progress") is True
    _release_job(workflow)


def _oracle_same_run_release_gate(workflow: Mapping[str, Any], _: Mapping[str, Any]) -> None:
    jobs = _jobs(workflow)
    aggregate = jobs.get("o11y-aggregate")
    assert isinstance(aggregate, dict)
    downloads = [
        step
        for step in _steps(aggregate)
        if str(step.get("uses") or "").startswith(DOWNLOAD_ARTIFACT_PREFIX)
    ]
    assert len(downloads) == 1
    fields = downloads[0].get("with") or {}
    assert fields.get("pattern") == "o11y-producer-*"
    assert fields.get("merge-multiple") is True
    assert "run-id" not in fields, "download must remain scoped to the current workflow run"
    _oracle_release_immutable_sha(workflow, {})


WORKFLOW_ORACLES = {
    "producer_start_records": _oracle_producer_start_records,
    "producer_finish_after_failure": _oracle_producer_finish_after_failure,
    "producer_finish_status": _oracle_producer_finish_status,
    "producer_command_identity": _oracle_producer_command_identity,
    "artifact_missing_policy": _oracle_artifact_missing_policy,
    "workflow_read_only_permissions": _oracle_workflow_read_only_permissions,
    "minimum_write_permissions": _oracle_minimum_write_permissions,
    "release_push_only": _oracle_release_push_only,
    "release_immutable_sha": _oracle_release_immutable_sha,
    "pull_request_secret_boundary": _oracle_pull_request_secret_boundary,
    "security_independent_root": _oracle_security_independent_root,
    "untrusted_context_shell_boundary": _oracle_untrusted_context_shell_boundary,
    "release_concurrency": _oracle_release_concurrency,
    "same_run_release_gate": _oracle_same_run_release_gate,
}


def _validate_evidence_oracle(root: Path, oracle: str) -> None:
    ci_evidence, ci_aggregate, boundary = _production_modules(root)
    if oracle == "evidence_contains_commit":
        bundle, expected = _signed_bundle(root)
        assert bundle["commit_sha"] == "a" * 40
        assert bundle["commit_sha"] == expected["commit_sha"]
        assert ci_evidence.verify_evidence_context(bundle, expected) == []
        assert boundary.validate_bundle(bundle) == []
        return
    if oracle == "evidence_context_reuse":
        bundle, expected = _signed_bundle(root)
        wrong = {**expected, "commit_sha": "b" * 40}
        errors = ci_evidence.verify_evidence_context(bundle, wrong)
        assert any("commit_sha mismatch" in error for error in errors), errors
        return
    if oracle == "evidence_checksum_gate":
        bundle, expected = _signed_bundle(root)
        tampered = copy.deepcopy(bundle)
        tampered["job_name"] = "tampered producer"
        producer_errors = ci_evidence.verify_evidence_context(tampered, expected)
        boundary_errors = boundary.validate_bundle(tampered)
        assert "artifact digest mismatch" in producer_errors
        assert "artifact digest mismatch" in boundary_errors
        needs = {"security": {"result": "success", "outputs": {}}}
        assert ci_aggregate.aggregate_exit_code(needs, [], boundary_errors) == 1
        return
    if oracle == "missing_evidence_gate":
        with tempfile.TemporaryDirectory(prefix="eve-ci-missing-") as directory:
            missing = Path(directory) / "does-not-exist"
            errors = boundary.validate_directory(missing)
        assert errors and "directory is missing" in errors[0]
        assert ci_aggregate.aggregate_exit_code(
            {"security": {"result": "success", "outputs": {}}}, [], errors
        ) == 1
        return
    if oracle == "truncated_evidence_gate":
        with tempfile.TemporaryDirectory(prefix="eve-ci-truncated-") as directory:
            path = Path(directory)
            (path / "security.json").write_text('{"schema_version":', encoding="utf-8")
            errors = boundary.validate_directory(path)
        assert errors and "corrupted artifact" in errors[0]
        assert ci_aggregate.aggregate_exit_code(
            {"security": {"result": "success", "outputs": {}}}, [], errors
        ) == 1
        return
    raise AssertionError(f"unknown evidence oracle: {oracle}")


def _validate_production_gate_oracle(root: Path, spec: CIContractSpec) -> None:
    gate = _production_gate_module(root)
    if spec.oracle == "production_skip_gate":
        assert gate.production_session_failed(
            tests_collected=7,
            skipped=7 if dict(spec.parameters)["risk_group"] != "fixture" else 1,
            pytest_exit_ok=True,
        )
        return
    if spec.oracle == "production_zero_collection":
        assert gate.production_session_failed(
            tests_collected=0,
            skipped=0,
            pytest_exit_ok=False,
        )
        return
    if spec.oracle == "production_placeholder_gate":
        values = {name: "configured-value" for name in gate.REQUIRED_PRODUCTION_SETTINGS}
        values["EVE_TRADE_EDGE_RESPONSE_SECRET"] = "${PRODUCTION_EDGE_SECRET}"
        try:
            gate.validate_required_settings(values)
        except ValueError as exc:
            assert "placeholder" in str(exc)
            assert "EVE_TRADE_EDGE_RESPONSE_SECRET" in str(exc)
        else:
            raise AssertionError("production gate accepted placeholder credential")
        return
    raise AssertionError(f"unknown production-gate oracle: {spec.oracle}")


def _validate_canonical_oracle(root: Path, oracle: str) -> None:
    verifier = _canonical_verifier(root)
    if oracle == "canonical_inventory":
        assert verifier.verify([], True) == []
        return
    if oracle == "canonical_removal_canary":
        original_path = verifier.MANIFEST
        manifest = json.loads(original_path.read_text(encoding="utf-8"))
        removed = manifest["groups"][0]["names"].pop()
        with tempfile.TemporaryDirectory(prefix="eve-canonical-") as directory:
            mutated = Path(directory) / "canonical_tests.json"
            mutated.write_text(json.dumps(manifest), encoding="utf-8")
            verifier.MANIFEST = mutated
            try:
                errors = verifier.verify([], True)
            finally:
                verifier.MANIFEST = original_path
        assert errors, "canonical verifier accepted a removed contract"
        assert any("199" in error or "digest mismatch" in error or removed in error for error in errors)
        return
    raise AssertionError(f"unknown canonical oracle: {oracle}")


def validate_ci_contract(
    name: str,
    root: Path,
    *,
    workflow: Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
) -> None:
    spec = CI_CONTRACTS[name]
    if spec.oracle in WORKFLOW_ORACLES:
        parsed_workflow = dict(workflow) if workflow is not None else _load_yaml(root / ".github" / "workflows" / "verify.yaml")
        parsed_action = dict(action) if action is not None else _load_yaml(root / ".github" / "actions" / "ci-evidence" / "action.yml")
        WORKFLOW_ORACLES[spec.oracle](parsed_workflow, parsed_action)
        if spec.oracle == "same_run_release_gate":
            ci_evidence, _, _ = _production_modules(root)
            bundle, expected = _signed_bundle(root)
            wrong_run = {**expected, "run_id": "654321"}
            wrong_sha = {**expected, "commit_sha": "b" * 40}
            assert any("run_id mismatch" in error for error in ci_evidence.verify_evidence_context(bundle, wrong_run))
            assert any("commit_sha mismatch" in error for error in ci_evidence.verify_evidence_context(bundle, wrong_sha))
        return
    if spec.oracle.startswith("evidence_") or spec.oracle in {"missing_evidence_gate", "truncated_evidence_gate"}:
        _validate_evidence_oracle(root, spec.oracle)
        return
    if spec.oracle.startswith("production_"):
        _validate_production_gate_oracle(root, spec)
        return
    if spec.oracle.startswith("canonical_"):
        _validate_canonical_oracle(root, spec.oracle)
        return
    raise AssertionError(f"unhandled CI semantic oracle {spec.oracle!r} for {name}")
