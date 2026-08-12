from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from eve_trade_hypothesis.contracts.ci_contracts import (
    CI_CONTRACTS,
    EVIDENCE_ACTION,
    binding_metadata,
    validate_ci_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


REPO_ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verify.yaml"
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "ci-evidence" / "action.yml"


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _workflow() -> dict:
    return _yaml(WORKFLOW_PATH)


def _action() -> dict:
    return _yaml(ACTION_PATH)


def _evidence_step(workflow: dict, job_id: str, mode: str) -> dict:
    matches = [
        step
        for step in workflow["jobs"][job_id]["steps"]
        if step.get("uses") == EVIDENCE_ACTION
        and (step.get("with") or {}).get("mode") == mode
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("name", sorted(CI_CONTRACTS))
def test_each_ci_binding_executes_its_exact_semantic_oracle(name):
    validate_ci_contract(name, REPO_ROOT)


def test_ci_requirement_bindings_are_exactly_the_executable_registry():
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "ci_and_production_gate"
    }
    expected = {name: binding_metadata(spec) for name, spec in CI_CONTRACTS.items()}
    assert bound == expected
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "test_every_required_verify_job_emits_ci_evidence_start_record",
            lambda workflow: workflow["jobs"]["security"]["steps"].remove(
                _evidence_step(workflow, "security", "start")
            ),
        ),
        (
            "test_every_required_verify_job_emits_ci_evidence_finish_record_even_after_failure",
            lambda workflow: _evidence_step(workflow, "go", "finish").update({"if": "${{ success() }}"}),
        ),
        (
            "test_ci_evidence_finish_status_matches_actual_github_job_status",
            lambda workflow: _evidence_step(workflow, "proto", "finish")["with"].update({"status": "success"}),
        ),
        (
            "test_ci_evidence_command_identity_is_unique_for_each_required_verification_job",
            lambda workflow: _evidence_step(workflow, "security", "finish")["with"].update(
                {"command-identity": "dagger/go"}
            ),
        ),
        (
            "test_job_requesting_write_permission_declares_only_minimum_required_permission_scope",
            lambda workflow: workflow["jobs"]["release-verification"]["permissions"].update(
                {"contents": "write"}
            ),
        ),
        (
            "test_production_deploy_job_cannot_run_from_pull_request_event",
            lambda workflow: workflow["jobs"]["release-verification"].update(
                {"if": "${{ github.event_name == 'pull_request' }}"}
            ),
        ),
        (
            "test_production_deploy_job_requires_immutable_commit_sha_rather_than_branch_head_lookup",
            lambda workflow: workflow["jobs"]["release-verification"].update(
                {"if": "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"}
            ),
        ),
        (
            "test_pull_request_workflow_does_not_expose_production_secrets_to_untrusted_fork_code",
            lambda workflow: workflow["jobs"]["release-verification"].update({"if": "${{ always() }}"}),
        ),
        (
            "test_security_scan_job_runs_even_when_earlier_nondependent_verification_job_fails",
            lambda workflow: workflow["jobs"]["security"].update({"needs": "go"}),
        ),
        (
            "test_shell_steps_handling_untrusted_github_context_do_not_interpolate_it_directly_into_executable_shell",
            lambda workflow: workflow["jobs"]["security"]["steps"].append(
                {"name": "unsafe", "run": "echo '${{ github.event.pull_request.title }}'"}
            ),
        ),
        (
            "test_workflow_concurrency_prevents_two_production_deployments_from_racing_same_environment_when_policy_requires_serialization",
            lambda workflow: workflow["concurrency"].update({"cancel-in-progress": False}),
        ),
        (
            "test_workflow_permissions_default_to_read_only_unless_individual_job_requires_write",
            lambda workflow: workflow.update({"permissions": {"contents": "write"}}),
        ),
        (
            "test_production_deployment_consumes_evidence_only_from_same_workflow_run_and_commit",
            lambda workflow: next(
                step
                for step in workflow["jobs"]["o11y-aggregate"]["steps"]
                if str(step.get("uses") or "").startswith("actions/download-artifact@")
            )["with"].update({"run-id": "previous-run"}),
        ),
    ],
)
def test_structured_workflow_oracles_reject_hostile_counterexamples(name, mutate):
    workflow = _workflow()
    mutate(workflow)
    with pytest.raises(AssertionError):
        validate_ci_contract(name, REPO_ROOT, workflow=workflow, action=_action())


def test_evidence_upload_oracle_rejects_non_failing_missing_file_policy():
    action = _action()
    upload = next(
        step
        for step in action["runs"]["steps"]
        if str(step.get("uses") or "").startswith("actions/upload-artifact@")
    )
    upload["with"]["if-no-files-found"] = "warn"
    with pytest.raises(AssertionError):
        validate_ci_contract(
            "test_artifact_upload_rejects_missing_required_evidence_files",
            REPO_ROOT,
            workflow=_workflow(),
            action=action,
        )
