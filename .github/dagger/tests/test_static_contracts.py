from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re

import yaml

from workflow_model import NEW, NEW_PATH, ORIGINAL_PATH, PRODUCER_JOBS, root_jobs


GITHUB = NEW_PATH.parents[1]
DAGGER = GITHUB / "dagger"
WORKFLOW_TEXT = NEW_PATH.read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
JOBS = WORKFLOW["jobs"]
PIN_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def modes(job_name: str) -> list[str]:
    return [
        str(step.get("with", {}).get("mode", ""))
        for step in JOBS[job_name]["steps"]
        if step.get("uses") == "./.github/actions/ci-evidence"
    ]


def test_original_snapshot_hash_matches_the_audited_experimental_workflow():
    assert hashlib.sha256(ORIGINAL_PATH.read_bytes()).hexdigest() == "d4561155b7a4225d133fb360eb3345fb6bd971dae990aca173115d6f197c1534"


def test_every_checkout_is_immutable_and_does_not_persist_credentials():
    for job_name, job in JOBS.items():
        checkout = job["steps"][0]
        owner, pin = str(checkout["uses"]).split("@", 1)
        assert owner == "actions/checkout", job_name
        assert PIN_RE.fullmatch(pin), (job_name, pin)
        assert checkout.get("with", {}).get("persist-credentials") is False, job_name


def test_every_logical_producer_records_real_commands_and_finishes_on_failure():
    for job_name in PRODUCER_JOBS:
        observed = modes(job_name)
        assert observed[0] == "start", (job_name, observed)
        assert "run" in observed, (job_name, observed)
        assert observed[-1] == "finish", (job_name, observed)
        finish = [
            step
            for step in JOBS[job_name]["steps"]
            if step.get("uses") == "./.github/actions/ci-evidence"
            and step.get("with", {}).get("mode") == "finish"
        ][0]
        assert "always()" in str(finish.get("if", "")), job_name
        assert finish["with"]["status"] == "${{ job.status }}", job_name
        commands = [
            str(step.get("with", {}).get("command", ""))
            for step in JOBS[job_name]["steps"]
            if step.get("uses") == "./.github/actions/ci-evidence"
            and step.get("with", {}).get("mode") == "run"
        ]
        assert all(command.startswith(".dagger-ci-venv/bin/python .github/dagger/") for command in commands)


def test_go_success_produces_the_three_mandatory_observations():
    names = {
        str(step.get("with", {}).get("step-name", ""))
        for step in JOBS["go"]["steps"]
        if step.get("with", {}).get("mode") == "run"
    }
    assert {"Run Go tests", "Run Go race detector", "Run Go vulnerability audit"} <= names


def test_terraform_matrix_preserves_provider_isolation_and_lock_policy():
    strategy = JOBS["terraform"]["strategy"]
    assert strategy["fail-fast"] is False
    rows = strategy["matrix"]["include"]
    assert {row["provider"] for row in rows} == {"eks", "gke", "talos-omni"}
    assert {row["lockfile_args"] for row in rows} == {"-lockfile=readonly"}
    assert all((NEW_PATH.parents[2] / row["root"] / ".terraform.lock.hcl").is_file() for row in rows)


def test_o11y_consumes_only_repository_producers_and_real_artifacts():
    aggregate = JOBS["o11y-aggregate"]
    assert set(aggregate["needs"]) == set(PRODUCER_JOBS)
    assert "reliability-contract" not in aggregate["needs"]
    assert "always()" in str(aggregate["if"])
    download = next(step for step in aggregate["steps"] if str(step.get("uses", "")).startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "o11y-producer-*"
    assert download["with"]["merge-multiple"] is True
    assert not (DAGGER / "_generate_evidence.py").exists()
    source = (DAGGER / "o11y.py").read_text(encoding="utf-8")
    assert "_validate_evidence.py" in source
    assert "envelope_exit" in source


def test_release_uses_a_separate_inline_publisher_between_uncredentialed_dagger_stages():
    steps = JOBS["release-verification"]["steps"]
    bootstrap_index = next(i for i, step in enumerate(steps) if step.get("run") == "python3 .github/dagger/_bootstrap.py")
    build_index = next(i for i, step in enumerate(steps) if str(step.get("run", "")).endswith(".github/dagger/release.py build"))
    publisher_index = next(i for i, step in enumerate(steps) if "GHCR_TOKEN" in step.get("env", {}))
    verify_index = next(i for i, step in enumerate(steps) if str(step.get("run", "")).endswith(".github/dagger/release.py verify"))
    assert bootstrap_index < build_index < publisher_index < verify_index
    assert "GHCR_TOKEN" not in steps[bootstrap_index].get("env", {})
    assert steps[publisher_index]["env"]["GHCR_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert all("GHCR_TOKEN" not in step.get("env", {}) for index, step in enumerate(steps) if index != publisher_index)
    source = (DAGGER / "release.py").read_text(encoding="utf-8")
    assert 'verified_sha = os.environ.get("VERIFIED_SHA")' in source
    assert "verified_sha != sha" in source
    assert "GHCR_TOKEN" not in source


def test_registry_token_step_uses_a_pinned_source_free_publisher_and_unsets_before_copy():
    publisher = next(
        step for step in JOBS["release-verification"]["steps"] if "GHCR_TOKEN" in step.get("env", {})
    )
    script = publisher["run"]
    assert ".github/" not in script
    assert "scripts/" not in script
    assert "pip install" not in script
    assert "docker buildx build" not in script
    assert script.count("docker push") == 3
    assert "docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c" in script
    assert script.rindex("unset GHCR_TOKEN") < script.index("install -m 0600")
    lock = (DAGGER / "requirements-release.txt").read_text(encoding="utf-8")
    assert "PyYAML==6.0.3" in lock and "--hash=sha256:" in lock


def test_encore_build_environments_provide_python_before_hardening():
    go = (DAGGER / "go.py").read_text(encoding="utf-8")
    release = (DAGGER / "release.py").read_text(encoding="utf-8")
    assert "PYTHON_BOOKWORM_IMAGE" in go and ".with_directory(" in go and '"/usr/local/go"' in go
    assert go.index('with_env_variable(\n            "PATH"') < go.index("harden_encore_install.sh")
    assert "PYTHON_BOOKWORM_IMAGE" in release and 'with_directory("/usr/local/go"' in release
    assert release.index('with_env_variable(\n        "PATH"') < release.index("harden_encore_install.sh")


def test_every_external_action_pin_has_reviewed_runtime_policy():
    policy = json.loads((GITHUB / "action-runtime-policy.json").read_text(encoding="utf-8"))
    for action, pin in re.findall(r"uses:\s*(actions/(?:checkout|upload-artifact|download-artifact))@([0-9a-f]{40})", WORKFLOW_TEXT + (GITHUB / "actions/ci-evidence/action.yml").read_text(encoding="utf-8")):
        assert pin in policy, (action, pin)
        assert policy[pin]["action"] == action
        assert policy[pin]["runtime"] in {"node20", "node24"}
        assert policy[pin]["supported_on_ubuntu_24_04"] is True


def test_bootstrap_uses_hash_locked_sdk_cli_binary_and_engine():
    source = (DAGGER / "_bootstrap.py").read_text(encoding="utf-8")
    assert "install.sh" not in source
    assert "--require-hashes" in source
    assert re.search(r'DAGGER_ARCHIVE_SHA256\s*=\s*"[0-9a-f]{64}"', source)
    assert re.search(r'DAGGER_BINARY_SHA256\s*=\s*"[0-9a-f]{64}"', source)
    assert re.search(r'DAGGER_ENGINE\s*=\s*"[^\"]+@sha256:[0-9a-f]{64}"', source)
    lock = (DAGGER / "requirements-bootstrap.txt").read_text(encoding="utf-8")
    assert "dagger-io==0.21.8" in lock
    assert "--hash=sha256:" in lock


def test_all_dagger_runtime_images_are_digest_pinned():
    tree = ast.parse((DAGGER / "_images.py").read_text(encoding="utf-8"))
    values = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance((target := node.targets[0]), ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and target.id.endswith("_IMAGE")
    }
    assert values
    assert all(DIGEST_RE.fullmatch(value) for value in values.values()), values
    for path in DAGGER.glob("*.py"):
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(parsed):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "from_":
                continue
            assert node.args, (path, node.lineno)
            arg = node.args[0]
            assert isinstance(arg, ast.Name) and arg.id in values, (path.name, node.lineno, ast.dump(arg))


def test_downloaded_kubernetes_tools_are_checksum_verified():
    kubernetes = (DAGGER / "kubernetes.py").read_text(encoding="utf-8")
    e2e = (DAGGER / "e2e.py").read_text(encoding="utf-8")
    release = (DAGGER / "release.py").read_text(encoding="utf-8")
    assert "KUBECTL_SHA256" in kubernetes and "sha256sum -c" in kubernetes
    assert "KUBECTL_SHA256" in release and "digest.hexdigest()" in release
    assert "KUBECTL_SHA256" in e2e and "KIND_SHA256" in e2e
    assert e2e.count("sha256sum -c") >= 2


def test_e2e_output_is_redacted_before_host_persistence_and_emits_observed_summary():
    source = (DAGGER / "e2e.py").read_text(encoding="utf-8")
    common = (DAGGER / "_common.py").read_text(encoding="utf-8")
    assert "redact_text(log)" in source
    assert 'print("E2E_SUMMARY="' in source
    assert "_sanitize_payload" in common
    e2e_job = json.dumps(JOBS["e2e"], sort_keys=True)
    for secret_name in ("HONEYCOMB_API_KEY", "HONEYCOMB_CONFIGURATION_KEY", "SENTRY_DSN"):
        assert secret_name not in e2e_job
        assert secret_name not in source


def test_evidence_envelope_rejects_missing_digest_or_signature(tmp_path: Path):
    import importlib.util

    validator_path = DAGGER / "_validate_evidence.py"
    spec = importlib.util.spec_from_file_location("eve_trade_validate_evidence", validator_path)
    assert spec and spec.loader
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    bundle = {
        "schema_version": "o11y.ci-evidence.v2",
        "repository": "QuasarRay/eve-trade",
        "branch_ref": "refs/heads/main",
        "commit_sha": "a" * 40,
        "workflow": "verify",
        "run_id": "123",
        "run_attempt": "1",
        "workflow_definition_digest": "sha256:" + "b" * 64,
        "job_id": "go",
        "job_name": "go / encore",
        "step_identity": "ci-evidence/finalize",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:01:00+00:00",
        "command_identity": "dagger/go",
        "exit_status": "success",
        "normalized_diagnostic": {"class": "NONE", "summary": "passed", "caused_by": []},
        "dependencies": ["proto"],
        "commands": [{"step_name": "Run Go tests", "exit_code": 0}],
        "collector_status": "COMPLETE",
        "provenance": {"historical": False},
    }
    bundle["artifact_digest"] = validator.canonical_digest(bundle)
    bundle["signature"] = validator.expected_signature(bundle)
    artifact = tmp_path / "go.json"
    artifact.write_text(json.dumps(bundle), encoding="utf-8")
    assert validator.validate_directory(tmp_path) == []
    for missing in ("artifact_digest", "signature"):
        altered = dict(bundle)
        altered.pop(missing)
        artifact.write_text(json.dumps(altered), encoding="utf-8")
        errors = validator.validate_directory(tmp_path)
        assert errors and any(missing.replace("artifact_", "artifact ") in error for error in errors)


def test_durable_diagnostics_are_uploaded_on_failure_or_completion():
    assert "canonical-regression-reports-" in WORKFLOW_TEXT
    assert "e2e-diagnostics-" in WORKFLOW_TEXT
    assert "o11y-aggregate-" in WORKFLOW_TEXT
    assert "release-verification-" in WORKFLOW_TEXT
    assert "include-hidden-files: true" in WORKFLOW_TEXT
    diagnostic_uploads = [
        step
        for job in JOBS.values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert diagnostic_uploads
    assert all(step.get("with", {}).get("if-no-files-found") == "error" for step in diagnostic_uploads)


def test_new_pipeline_keeps_an_additional_independent_reliability_gate():
    assert "reliability-contract" in root_jobs(NEW)
    assert "reliability-contract" not in NEW["e2e"].needs
    assert "reliability-contract" in NEW["release-verification"].needs
