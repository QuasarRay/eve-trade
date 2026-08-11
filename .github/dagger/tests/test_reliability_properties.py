from __future__ import annotations

import hashlib

from hypothesis import given, settings, strategies as st

from workflow_model import (
    NEW,
    NEW_PATH,
    ORIGINAL,
    ORIGINAL_PATH,
    PRODUCER_JOBS,
    executable_runs_in_workflow,
    new_release_allowed,
    release_allowed,
    root_jobs,
    would_run,
)


RESULT = st.sampled_from(["success", "failure", "cancelled", "skipped"])
ORIGINAL_ROOTS = sorted(root_jobs(ORIGINAL))


@given(st.dictionaries(st.sampled_from(PRODUCER_JOBS), RESULT))
@settings(max_examples=500)
def test_observability_aggregate_runs_for_every_upstream_result_pattern(outcomes):
    assert would_run(ORIGINAL, "o11y-aggregate", outcomes)
    assert would_run(NEW, "o11y-aggregate", outcomes)


@given(RESULT, RESULT, RESULT)
def test_terraform_provider_failure_cannot_cancel_sibling_matrix_entries(eks, gke, talos):
    assert {eks, gke, talos} <= {"success", "failure", "cancelled", "skipped"}
    assert NEW["terraform"].matrix_fail_fast is False
    assert set(NEW["terraform"].matrix_rows) == {"eks", "gke", "talos-omni"}


@given(RESULT)
def test_go_dependency_behavior_is_identical_to_the_original_pipeline(proto_result):
    outcomes = {"proto": proto_result}
    assert would_run(NEW, "go", outcomes) == would_run(ORIGINAL, "go", outcomes)


@given(st.sampled_from(sorted(set(ORIGINAL) & set(NEW))))
def test_every_shared_original_job_timeout_is_preserved_or_tightened(job):
    assert NEW[job].timeout <= ORIGINAL[job].timeout


@given(
    event=st.sampled_from(["push", "pull_request", "workflow_dispatch"]),
    ref=st.sampled_from(["refs/heads/main", "refs/heads/experimental", "refs/pull/1/merge"]),
    sha=st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    reliability=RESULT,
    e2e=RESULT,
    o11y=RESULT,
    same=st.booleans(),
)
def test_release_is_stricter_than_original_and_requires_exact_current_sha(
    event, ref, sha, reliability, e2e, o11y, same
):
    verified = sha if same else ("0" * 40 if sha != "0" * 40 else "1" * 40)
    original_allowed = release_allowed(event, ref, sha, e2e, o11y, verified)
    new_allowed = new_release_allowed(event, ref, sha, reliability, e2e, o11y, verified)
    assert new_allowed == (reliability == "success" and original_allowed)
    assert not new_allowed or original_allowed


@given(st.sampled_from(ORIGINAL_ROOTS), st.sampled_from(["failure", "cancelled", "skipped"]))
def test_failure_of_an_original_root_never_suppresses_other_independent_roots(job, result):
    outcomes = {job: result}
    for other in ORIGINAL_ROOTS:
        if other != job:
            assert would_run(ORIGINAL, other, outcomes)
            assert would_run(NEW, other, outcomes)


@given(st.sampled_from(["failure", "cancelled", "skipped"]))
def test_reliability_contract_failure_never_suppresses_e2e_when_product_prerequisites_succeed(result):
    outcomes = {dependency: "success" for dependency in NEW["e2e"].needs}
    outcomes["reliability-contract"] = result
    assert "reliability-contract" not in NEW["e2e"].needs
    assert would_run(NEW, "e2e", outcomes)


def test_new_pipeline_adds_an_independent_reliability_failure_domain():
    assert root_jobs(ORIGINAL) < root_jobs(NEW)
    assert root_jobs(NEW) - root_jobs(ORIGINAL) == {"reliability-contract"}


def test_e2e_product_dependency_graph_is_unchanged():
    assert NEW["e2e"].needs == ORIGINAL["e2e"].needs


def test_github_run_steps_are_hash_locked_launchers_plus_one_isolated_publisher():
    runs = executable_runs_in_workflow(NEW_PATH.read_text(encoding="utf-8"))
    assert runs
    assert runs.count("|") == 1
    assert all(
        run == "|"
        or
        run == "python3 .github/dagger/_bootstrap.py"
        or (
            run.startswith(".dagger-ci-venv/bin/python .github/dagger/")
            and (run.endswith(".py") or run.endswith(".py build") or run.endswith(".py verify"))
        )
        for run in runs
    )


def test_observability_uses_real_artifacts_and_exact_repository_producer_set():
    workflow = NEW_PATH.read_text(encoding="utf-8")
    assert "OBS_CI_NEEDS_JSON: ${{ toJson(needs) }}" in workflow
    assert "actions/download-artifact@" in workflow
    assert "pattern: o11y-producer-*" in workflow
    assert not (NEW_PATH.parents[1] / "dagger" / "_generate_evidence.py").exists()
    assert set(NEW["o11y-aggregate"].needs) == set(PRODUCER_JOBS)


def test_release_job_explicitly_depends_on_reliability_e2e_and_observability():
    assert set(NEW["release-verification"].needs) == {
        "reliability-contract",
        "e2e",
        "o11y-aggregate",
    }


def test_original_workflow_fixture_is_the_exact_audited_experimental_snapshot():
    assert hashlib.sha256(ORIGINAL_PATH.read_bytes()).hexdigest() == "d4561155b7a4225d133fb360eb3345fb6bd971dae990aca173115d6f197c1534"
