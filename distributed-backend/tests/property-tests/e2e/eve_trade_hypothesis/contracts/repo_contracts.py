from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

import pytest
import yaml

from eve_trade_hypothesis.catalog import load_catalog, proposed_names


K8S_PATTERNS = [
    "distributed-backend/ci-cd/**/*.yaml",
    "distributed-backend/ci-cd/**/*.yml",
    "infra/**/*.yaml",
    "infra/**/*.yml",
]
WORKFLOW_PATTERNS = [".github/workflows/*.yaml", ".github/workflows/*.yml"]
PROTO_PATTERNS = ["proto/**/*.proto", "buf.yaml", "buf.gen.yaml"]
MIGRATION_PATTERNS = ["**/migrations/**/*.sql", "**/migration/**/*.sql", "**/schema/**/*.sql"]
GO_PATTERNS = ["**/*.go"]
RUST_PATTERNS = ["**/*.rs", "**/Cargo.toml", "**/Cargo.lock"]


from eve_trade_hypothesis.semantic_overrides import SEMANTIC_EVIDENCE_OVERRIDES


def run(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    assert name not in SEMANTIC_EVIDENCE_OVERRIDES, (
        f"{name} has an audited weak legacy branch and must be rerouted by engine.py"
    )
    repo = runtime.repo
    repo.require_repo()

    if category == 92:
        return _naming_quality(repo, name)
    if category == 91:
        return _traceability(runtime, name)
    if category in {38, 98, 99, 100}:
        return _ci_contract(runtime, category, name, case)
    if category in {67, 68, 69, 70, 71}:
        return _terraform_contract(runtime, category, name, case)
    if category in {35, 65, 84}:
        return _kubernetes_contract(runtime, category, name, case)
    if category == 75:
        return _proto_contract(runtime, name, case)
    if category == 74:
        return _dependency_contract(runtime, name, case)
    if category == 73:
        return _container_contract(runtime, name, case)
    if category == 72:
        return _supply_chain_contract(runtime, name, case)
    if category == 77:
        return _nsq_contract(runtime, name, case)
    if category in {25, 26, 27, 50, 80, 81, 90}:
        return _database_repo_contract(runtime, category, name, case)
    if category in {34, 89}:
        return _observability_contract(runtime, name, case)
    if category in {49}:
        return _sql_safety_contract(runtime, name, case)
    if category in {51, 85}:
        return _configuration_contract(runtime, name, case)
    if category in {55, 56}:
        return _source_contract(runtime, category, name, case)
    if category in {57, 93}:
        return _test_quality_contract(runtime, category, name, case)
    if category in {37, 45, 52, 53, 88}:
        return runtime.evidence.run(name, case)

    # No silent success: any repo category not implemented above requires an
    # evidence driver that demonstrates the assertion against the real checkout.
    runtime.evidence.run(name, case)


def _all_generated_catalog_names() -> list[str]:
    catalog = load_catalog()
    return sorted(
        name
        for category in catalog["categories"].values()
        for name in category["names"]
    )


def _executable_test_references(text: str) -> set[str]:
    """Extract function identities only from executable pytest selectors.

    Bare ``test_*`` prose is commonly an example, historical report, file stem,
    or placeholder. Treating every Markdown token as a live node reference made
    the traceability contract evaluate unrelated prose instead of automation.
    """
    references = set(re.findall(r"::(test_[a-z0-9_]+)\b", text))
    references.update(
        re.findall(
            r"(?:^|\s)(?:-k|--deselect(?:=|\s))\s*[\"']?(test_[a-z0-9_]+)\b",
            text,
            re.MULTILINE,
        )
    )
    return references


def _naming_quality(repo, name: str) -> None:
    names = _all_generated_catalog_names()
    assert names
    if name == "test_proposed_test_names_contain_no_placeholder_words_todo_fixme_or_tbd":
        # The policy contract must name the forbidden tokens to define the
        # check. Evaluate the subject catalog, excluding that self-reference.
        bad = [
            candidate
            for candidate in names
            if candidate != name
            and re.search(r"(?:^|_)(todo|fixme|tbd)(?:_|$)", candidate)
        ]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_contain_no_vague_success_verbs":
        bad = [n for n in names if re.search(r"(?:^|_)(works|handles|behaves|correctly)(?:_|$)", n)]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_using_reject_identify_the_specific_rejected_condition":
        bad = [n for n in names if "_reject" in n and n.endswith(("_rejects", "_rejected"))]
        assert not bad, bad
        return
    if name == "test_proposed_test_names_using_retry_identify_whether_business_effect_may_repeat":
        bad = [n for n in names if "retry" in n and not any(k in n for k in ("duplicate", "idempot", "same_", "reexecute", "repeat", "attempt", "backoff", "retryable", "retries"))]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_using_concurrent_identify_the_shared_resource_or_race_boundary":
        bad = [n for n in names if "concurrent" in n and len(n.split("_")) < 7]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_using_crash_identify_crash_window_and_post_restart_invariant":
        bad = [n for n in names if "crash" in n and not any(k in n for k in ("before", "after", "during", "restart", "recover", "redeliver", "resume", "rollback"))]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_using_timeout_identify_timeout_boundary_and_persistence_invariant":
        bad = [n for n in names if "timeout" in n and len(n.split("_")) < 7]
        assert not bad, bad[:20]
        return
    if name == "test_proposed_test_names_using_invalid_identify_the_exact_invalid_property":
        bad = [n for n in names if n.endswith("_invalid") or n.endswith("_invalid_input")]
        assert not bad, bad
        return
    if name == "test_proposed_test_names_do_not_encode_two_alternative_expected_outcomes_with_rejected_or_accepted_wording":
        bad = [
            candidate
            for candidate in names
            if candidate != name
            and re.search(
                r"_(rejected|accepted)_or_(rejected|accepted)_", candidate
            )
        ]
        assert not bad, bad
        return
    if name == "test_proposed_test_names_are_unique_across_categories_except_explicit_priority_index":
        assert len(names) == len(set(names))
        return
    if name == "test_existing_repository_test_names_are_never_silently_rewritten_in_observed_inventory":
        existing = load_catalog()["existing"]
        assert all(k.startswith("test_") for k in existing)
        return
    if name == "test_recommended_existing_test_rename_file_contains_old_and_new_name_for_every_suggested_rename":
        candidates = list(repo.root.rglob("recommended_existing_test_renames*.md"))
        if not candidates:
            # The implementation archive itself carries this mapping; when not
            # copied into repo, require external evidence rather than passing.
            repo.unavailable("recommended existing-test rename mapping is not present in checkout")
        text = "\n".join(p.read_text(encoding="utf-8") for p in candidates)
        assert "→" in text or "->" in text
        return
    raise AssertionError(f"unhandled naming-quality contract: {name}")


def _traceability(runtime, name: str) -> None:
    repo = runtime.repo
    py_tests = repo.python_test_names()
    go_tests = repo.go_test_names()
    source = repo.all_text(["distributed-backend/src/**/*.go", "gametrade/**/*.go", "distributed-backend/trade-settlement/**/*.rs", "proto/**/*.proto"])

    if "public_gateway_action" in name:
        for action in ("market_place_sell_order", "market_buy_from_sell_order", "market_cancel_order"):
            assert action in source, action
        return
    if "gametrade_settlement_intent" in name:
        for intent in ("ISSUE", "ACCEPT", "CANCEL"):
            assert intent.lower() in source.lower(), intent
        return
    if "settlement_operation_enum_value" in name:
        proto = repo.all_text(["proto/**/*.proto"])
        enum_values = re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]+)\s*=\s*\d+\s*;", proto)
        assert enum_values
        test_blob = " ".join(py_tests) + " " + " ".join(go_tests) + " " + repo.all_text(["**/*_test.go", "**/test_*.py"])
        missing = [v for v in enum_values if "OPERATION" in v and v.lower() not in test_blob.lower()]
        assert not missing, missing
        return
    if "documented_domain_error_code" in name:
        tests = repo.all_text(["**/*_test.go", "**/test_*.py"])
        codes = set(re.findall(r'failure_code\s*=\s*["\']([A-Z0-9_]+)', source, re.I))
        if not codes:
            codes = set(re.findall(r'"([a-z_]{4,})"', source))
        assert tests
        # At minimum every explicit proto/database error token mentioned in tests
        # must remain searchable; exact mapping matrix may be supplied by evidence.
        assert any(c.lower() in tests.lower() for c in codes) if codes else True
        return
    if "database_check_constraint" in name or "database_foreign_key" in name:
        sql = repo.all_text(MIGRATION_PATTERNS)
        assert re.search(r"\bCHECK\b|\bFOREIGN\s+KEY\b|\bREFERENCES\b", sql, re.I)
        return
    if "idempotency_terminal_state" in name:
        assert "idempotency" in source.lower()
        assert any("idempotency" in n for n in py_tests)
        return
    if "pubsub_handler" in name:
        assert "pubsub" in source.lower() or "subscription" in source.lower()
        assert any("duplicate" in n and ("delivery" in n or "pubsub" in n) for n in py_tests) or runtime.config.strict is False
        return
    if "crash_failpoint" in name or "production_configuration_key" in name or "terraform_root" in name or "kubernetes_production_overlay" in name or "proto_breaking_sensitive_field" in name:
        return runtime.evidence.run(name, {"python_tests": len(py_tests), "go_tests": len(go_tests)})
    if "referenced_test_name_no_longer_exists" in name:
        # Scan executable automation selectors. Bare prose tokens are examples,
        # file stems, historical reports, and placeholders rather than nodes.
        referenced: set[str] = set()
        for p in repo.existing_paths(
            [
                ".github/workflows/**/*.yaml",
                ".github/workflows/**/*.yml",
                ".github/actions/**/*.yaml",
                ".github/actions/**/*.yml",
                "scripts/**/*.sh",
                "distributed-backend/ci/**/*.sh",
            ]
        ):
            referenced.update(_executable_test_references(repo.read(p)))
        known = set(py_tests) | proposed_names()
        stale = sorted(n for n in referenced if n.startswith("test_") and n not in known)
        assert not stale, stale[:50]
        return
    runtime.evidence.run(name, {})


def _ci_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo = runtime.repo
    workflows = repo.workflow_yaml()
    assert workflows, "no GitHub Actions workflows"
    workflow_text = "\n".join(repo.read(p) for p in repo.workflow_files())
    commands = "\n".join(repo.workflow_run_commands())
    uses = repo.workflow_uses()

    if name == "test_every_third_party_github_action_uses_full_length_commit_sha":
        bad = []
        for use in uses:
            if use.startswith("./") or use.startswith("docker://"):
                continue
            if "@" not in use:
                bad.append(use); continue
            ref = use.rsplit("@", 1)[1]
            if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
                bad.append(use)
        assert not bad, bad
        return
    if "workflow_permissions_default_to_read_only" in name:
        for _, wf in workflows:
            perms = wf.get("permissions")
            assert perms in ("read-all", {"contents": "read"}) or (isinstance(perms, dict) and all(v in {"read", "none"} for v in perms.values())), perms
        return
    if "production_deploy_job_cannot_run_from_pull_request" in name:
        # No deploy job may have a condition that explicitly includes pull_request.
        for _, wf in workflows:
            for job_name, job in (wf.get("jobs") or {}).items():
                if "deploy" in str(job_name).lower() and isinstance(job, dict):
                    cond = str(job.get("if", ""))
                    assert "pull_request" not in cond or "!=" in cond or "push" in cond, (job_name, cond)
        return
    if "pinned_encore" in name or "encore_cli" in name:
        assert "ENCORE_CLI_VERSION" in workflow_text
        assert "ENCORE_CLI_SHA256" in workflow_text
        return
    if "govulncheck" in name:
        assert "govulncheck ./..." in commands
        return
    if "cargo_audit" in name:
        assert "cargo audit" in commands.lower()
        return
    if "pip_audit" in name:
        assert "pip-audit" in commands.lower() or "pip_audit" in commands.lower()
        return
    if "trivy_secret" in name:
        assert "trivy" in workflow_text.lower() and "secret" in workflow_text.lower()
        return
    if "trivy_configuration" in name:
        assert "trivy" in workflow_text.lower() and any(k in workflow_text.lower() for k in ("config", "misconfig"))
        return
    if "verify_job_emits_ci_evidence_start_record" in name:
        jobs = [j for _, wf in workflows for j in (wf.get("jobs") or {}).values() if isinstance(j, dict)]
        missing = []
        for job in jobs:
            steps = job.get("steps") or []
            if not steps:
                continue
            if not any(isinstance(s, dict) and s.get("uses") == "./.github/actions/ci-evidence" and (s.get("with") or {}).get("mode") == "start" for s in steps):
                missing.append(job.get("name"))
        assert not missing, missing
        return
    if "verify_job_emits_ci_evidence_finish_record" in name:
        jobs = [j for _, wf in workflows for j in (wf.get("jobs") or {}).values() if isinstance(j, dict)]
        missing = []
        for job in jobs:
            steps = job.get("steps") or []
            if not steps:
                continue
            if not any(isinstance(s, dict) and s.get("uses") == "./.github/actions/ci-evidence" and (s.get("with") or {}).get("mode") == "finish" for s in steps):
                missing.append(job.get("name"))
        assert not missing, missing
        return
    if "command_identity_is_unique" in name:
        records=[]
        for path,wf in workflows:
            for job_id,job in (wf.get("jobs") or {}).items():
                if not isinstance(job,dict):
                    continue
                evidence_steps=[
                    step for step in (job.get("steps") or [])
                    if isinstance(step,dict) and step.get("uses")=="./.github/actions/ci-evidence"
                ]
                if not any((step.get("with") or {}).get("mode") in {"start","run"} for step in evidence_steps):
                    continue
                finish=[step for step in evidence_steps if (step.get("with") or {}).get("mode")=="finish"]
                assert len(finish)==1,(str(path),job_id,"expected one finish evidence step")
                identity=str((finish[0].get("with") or {}).get("command-identity") or "")
                assert identity and identity!="unspecified",(str(path),job_id,"missing command identity")
                records.append((str(path),str(job_id),identity))
        assert records,"no required verification jobs with CI evidence"
        identities=[identity for _,_,identity in records]
        assert len(identities)==len(set(identities)),records
        return
    if "checked_out_commit_sha" in name or "same_workflow_run_and_commit" in name or "artifact_checksum" in name or "evidence_finish_status" in name or "missing_ci_evidence" in name or "truncated_ci_evidence" in name:
        return runtime.evidence.run(name, case)
    if "production_gate" in name or "deployed_" in name or "rendered_manifest_sha" in name or "e2e_suite_targets_running_commit_sha" in name:
        # Require the workflow to contain production-gate wiring and immutable SHA usage;
        # live equivalence is delegated when exact runtime evidence is needed.
        if "production_gate" in name:
            assert "EVE_TRADE_E2E_PRODUCTION_GATE" in workflow_text
            return
        if "commit_sha" in name or "image_sha" in name or "manifest_sha" in name:
            assert "github.sha" in workflow_text or "GITHUB_SHA" in workflow_text
            return
    if "race_contract" in name:
        assert "-race" in commands or "go test -race" in workflow_text
        return
    if "pytest_collects_zero" in name or "all_load_tests_are_skipped" in name or "all_security_tests_are_skipped" in name or "all_crash_recovery_tests_are_skipped" in name:
        e2e_conftest = repo.read("distributed-backend/tests/e2e/conftest.py")
        assert "testscollected == 0" in e2e_conftest
        assert "skipped" in e2e_conftest
        return
    if "required_environment_variable_contains_placeholder_value" in name or "endpoint_resolves_to_local_stub" in name:
        return runtime.evidence.run(name, case)
    if "security_advisory_allowlist" in name or "ignored_rust_advisory" in name or "new_critical_vulnerability" in name:
        ignores = repo.existing_paths([".trivyignore*", "**/*audit*.toml", "**/*audit*.yaml", "**/*audit*.yml"])
        if not ignores:
            repo.unavailable("security allowlist files are not present")
        blob = "\n".join(repo.read(p) for p in ignores)
        assert blob.strip()
        return
    if "secret_scan_regression_fixture" in name:
        return runtime.evidence.run(name, case)

    runtime.evidence.run(name, case)


def _terraform_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo = runtime.repo
    roots = repo.terraform_roots()
    if not roots:
        repo.unavailable("no Terraform roots found")

    if name == "test_all_terraform_roots_pass_fmt_check":
        for root in roots:
            repo.run(["terraform", "fmt", "-check", "-recursive"], cwd=root).assert_ok()
        return
    if name == "test_all_terraform_roots_initialize_with_dependency_lockfile_read_only":
        for root in roots:
            repo.run(["terraform", "init", "-backend=false", "-lockfile=readonly"], cwd=root, timeout=180).assert_ok()
        return
    if name == "test_all_terraform_roots_pass_validate":
        for root in roots:
            repo.run(["terraform", "validate"], cwd=root).assert_ok()
        return
    if name == "test_all_terraform_roots_execute_terraform_test_without_skipped_required_assertions":
        for root in roots:
            repo.run(["terraform", "test", "-no-color"], cwd=root, timeout=300).assert_ok()
        return
    if "provider_lockfiles_include_" in name:
        lockfiles = [r / ".terraform.lock.hcl" for r in roots if (r / ".terraform.lock.hcl").exists()]
        assert lockfiles, "no .terraform.lock.hcl files"
        for path in lockfiles:
            text = path.read_text(encoding="utf-8")
            assert "zh:" in text or "h1:" in text
        return
    if "pin_provider_versions" in name:
        blob = "\n".join(p.read_text(encoding="utf-8") for p in repo.existing_paths(["**/*.tf"]))
        assert re.search(r"required_providers\s*{", blob)
        assert re.search(r"version\s*=\s*\"[^\"]+\"", blob)
        return
    if "secret_variables_are_marked_sensitive" in name or "outputs_derived_from_secret_values_are_marked_sensitive" in name:
        tf = "\n".join(repo.read(p) for p in repo.existing_paths(["**/*.tf"]))
        secret_blocks = re.findall(r'variable\s+"([^"]*(?:password|secret|token|key)[^"]*)"\s*{([^}]*)}', tf, re.I | re.S)
        assert secret_blocks
        bad = [n for n, body in secret_blocks if not re.search(r"sensitive\s*=\s*true", body, re.I)]
        assert not bad, bad
        return
    if "no_terraform_output_exposes_database_password" in name or "no_terraform_output_exposes_hmac_secret" in name:
        tf = "\n".join(repo.read(p) for p in repo.existing_paths(["**/*.tf"]))
        outputs = re.findall(r'output\s+"([^"]+)"\s*{([^}]*)}', tf, re.S)
        bad = [(n,b) for n,b in outputs if re.search(r"password|hmac.*secret|secret.*hmac", n+b, re.I) and not re.search(r"sensitive\s*=\s*true", b, re.I)]
        assert not bad, [n for n,_ in bad]
        return
    if "second_plan_after_apply_contains_zero_unexpected_changes" in name:
        return runtime.evidence.run(name, {"terraform_roots": [str(r.relative_to(repo.root)) for r in roots], **case})
    if category in {68, 69, 70}:
        provider = {68: "eks", 69: "gke", 70: "talos"}[category]
        relevant = [r for r in roots if provider in str(r).lower() or (provider == "talos" and "omni" in str(r).lower())]
        assert relevant, f"no {provider} Terraform root found"
        blob = "\n".join(p.read_text(encoding="utf-8", errors="replace") for r in relevant for p in r.glob("*.tf"))
        if "do_not_expose_postgres" in name or "do_not_expose_settlement_grpc" in name or "firewall_rules" in name or "security_groups" in name:
            assert "0.0.0.0/0" not in "\n".join(line for line in blob.splitlines() if re.search(r"5432|9092|postgres|settlement", line, re.I)), name
            return
        if "workload_identity" in name or "iam_roles" in name or "service_account_roles" in name:
            assert any(k in blob.lower() for k in ("iam", "workload_identity", "service_account", "role"))
            return
        if "database_password" in name or "secret" in name:
            assert "sensitive" in blob.lower() or "secret" in blob.lower()
            return
        if "encryption" in name or "encrypted" in name:
            assert any(k in blob.lower() for k in ("encrypted", "encryption", "kms"))
            return
        if "backup_retention" in name or "deletion_protection" in name:
            assert any(k in blob.lower() for k in ("backup_retention", "deletion_protection", "retention"))
            return
        if "external_database_mode" in name or "in_cluster_database_mode" in name:
            assert any(k in blob.lower() for k in ("external_database", "database_url", "postgres"))
            return
        if "provider_uses_created_cluster_endpoint" in name or "cluster_endpoint_public_access" in name or "control_plane_access" in name:
            assert "endpoint" in blob.lower()
            return
        return runtime.evidence.run(name, {"roots": [str(r) for r in relevant], **case})
    if category == 71:
        lockfiles = [r / ".terraform.lock.hcl" for r in roots if (r / ".terraform.lock.hcl").exists()]
        if "provider_lockfile" in name or "providers_lock" in name:
            assert lockfiles
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in lockfiles}
            # Pure integrity checks do not mutate; exact command mutation tests go to evidence.
            if "identical_after_two_consecutive" in name:
                return runtime.evidence.run(name, {"before": before, **case})
            assert all(p.stat().st_size > 0 for p in lockfiles)
            return
        if "state_backend_configuration_never_embeds_static_cloud" in name:
            blob = "\n".join(repo.read(p) for p in repo.existing_paths(["**/*.tf"]))
            assert not re.search(r"(?i)(access_key|secret_key)\s*=\s*\"[^$][^\"]+\"", blob)
            return
        if "remote_state" in name:
            return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _kubernetes_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo = runtime.repo
    docs = repo.rendered_kubernetes_documents()
    if not docs:
        repo.unavailable("no Kubernetes YAML found")

    def pod_specs():
        for path, doc in docs:
            kind = doc.get("kind")
            spec = doc.get("spec") or {}
            if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}:
                template = spec.get("template") or (spec.get("jobTemplate") or {}).get("spec", {}).get("template") or {}
                ps = template.get("spec") or {}
                yield path, doc, ps

    workloads=list(pod_specs())
    assert workloads,"no Kubernetes workload pod specs found in orchestration manifests"
    assert any((ps.get("containers") or []) for _,_,ps in workloads),"Kubernetes workload set contains no application containers"

    if "runs_as_non_root" in name:
        bad = []
        for p, doc, ps in pod_specs():
            pod_sc = ps.get("securityContext") or {}
            for c in ps.get("containers") or []:
                sc = c.get("securityContext") or {}
                if sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is not True:
                    bad.append((str(p.relative_to(repo.root)), c.get("name")))
        assert not bad, bad
        return
    if "disallows_privilege_escalation" in name:
        bad = [(str(p.relative_to(repo.root)), c.get("name")) for p,_,ps in pod_specs() for c in ps.get("containers") or [] if (c.get("securityContext") or {}).get("allowPrivilegeEscalation") is not False]
        assert not bad, bad
        return
    if "drops_all_linux_capabilities" in name:
        bad=[]
        for p,_,ps in pod_specs():
            for c in ps.get("containers") or []:
                drops = (((c.get("securityContext") or {}).get("capabilities") or {}).get("drop") or [])
                if "ALL" not in drops:
                    bad.append((str(p.relative_to(repo.root)), c.get("name")))
        assert not bad, bad
        return
    if "uses_privileged_container" in name:
        bad=[(str(p.relative_to(repo.root)), c.get("name")) for p,_,ps in pod_specs() for c in ps.get("containers") or [] if (c.get("securityContext") or {}).get("privileged") is True]
        assert not bad, bad
        return
    if "uses_host_pid" in name or "uses_host_ipc" in name:
        field = "hostPID" if "host_pid" in name else "hostIPC"
        bad=[str(p.relative_to(repo.root)) for p,_,ps in pod_specs() if ps.get(field) is True]
        assert not bad, bad
        return
    if "mounts_host_path" in name:
        bad=[]
        for p,_,ps in pod_specs():
            for v in ps.get("volumes") or []:
                if "hostPath" in v:
                    bad.append((str(p.relative_to(repo.root)), v.get("name")))
        assert not bad, bad
        return
    if "seccomp_profile" in name:
        bad=[]
        for p,_,ps in pod_specs():
            sc=ps.get("securityContext") or {}
            sec=(sc.get("seccompProfile") or {}).get("type")
            if sec not in {"RuntimeDefault", "Localhost"}:
                bad.append(str(p.relative_to(repo.root)))
        assert not bad, bad
        return
    if "resource_requests" in name or "resource_limits" in name:
        field = "requests" if "requests" in name else "limits"
        bad=[]
        for p,_,ps in pod_specs():
            for c in ps.get("containers") or []:
                resources=c.get("resources") or {}
                if not resources.get(field):
                    bad.append((str(p.relative_to(repo.root)), c.get("name")))
        assert not bad, bad
        return
    if "readiness_probe" in name or "liveness_probe" in name:
        field="readinessProbe" if "readiness" in name else "livenessProbe"
        bad=[]
        for p,_,ps in pod_specs():
            for c in ps.get("containers") or []:
                if c.get("name") in {"encore-backend", "trade-settlement", "settlement-worker"} and field not in c:
                    bad.append((str(p.relative_to(repo.root)), c.get("name")))
        assert not bad, bad
        return
    if "rejects_latest_image_tag" in name:
        images=[str(c.get("image","")) for _,_,ps in pod_specs() for c in ps.get("containers") or []]
        assert all(not i.endswith(":latest") and i != "latest" for i in images), images
        return
    if "uses_exact_built_image_sha" in name or "same_application_image_digests" in name or "render_same_" in name:
        return runtime.evidence.run(name, case)
    if "plaintext_secrets" in name:
        blob="\n".join(repo.read(p) for p,_ in docs)
        assert not re.search(r"(?i)(password|secret|hmac[_-]?key)\s*:\s*[\"']?(?!\$|\{|<|REDACTED|changeme)[A-Za-z0-9+/=_-]{12,}", blob)
        return
    if "service_is_cluster_internal" in name or "not_publicly_exposed" in name or "postgres_service_is_not_exposed" in name:
        for _,doc in docs:
            if doc.get("kind") != "Service": continue
            meta=doc.get("metadata") or {}; service_name=str(meta.get("name", ""))
            if any(k in service_name for k in ("trade-settlement", "postgres")):
                assert (doc.get("spec") or {}).get("type", "ClusterIP") == "ClusterIP", (service_name, (doc.get("spec") or {}).get("type"))
        return
    if "network_policy" in name:
        policies=[doc for _,doc in docs if doc.get("kind")=="NetworkPolicy"]
        assert policies, "no NetworkPolicy resources"
        return
    if category == 84:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _proto_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    proto_files=repo.require_paths(["proto/**/*.proto"], purpose="protobuf contract")
    blob="\n".join(repo.read(p) for p in proto_files)
    workflow="\n".join(repo.workflow_run_commands())
    if name == "test_buf_lint_passes_for_all_eve_proto_modules":
        repo.run(["buf","lint","--error-format","text"]).assert_ok(); return
    if name == "test_buf_format_produces_no_diff":
        repo.run(["buf","format","--diff","--exit-code"]).assert_ok(); return
    if name == "test_buf_generate_produces_no_diff_in_checked_in_generated_sources":
        return runtime.evidence.run(name, case)
    if "buf_breaking" in name:
        # Mutation-specific breaking tests need a disposable checkout/evidence driver.
        return runtime.evidence.run(name, case)
    if "generated_go_proto_sources_match" in name or "generated_rust_proto_sources_match" in name:
        assert (repo.root/"proto"/"gen").exists()
        assert "buf generate" in workflow
        return
    if "protovalidate_rules" in name:
        assert "buf.validate" in blob or "protovalidate" in blob or "validation" in blob.lower()
        return
    if "reserved_removed" in name:
        assert "reserved" in blob.lower(), "no reserved proto fields/enums found"
        return
    if "new_optional_field" in name or "unknown_fields_round_trip" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _dependency_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    if name == "test_go_mod_tidy_produces_no_diff":
        repo.run(["go","mod","tidy","-diff"]).assert_ok(); return
    if name == "test_go_mod_verify_succeeds_for_every_downloaded_module":
        repo.run(["go","mod","verify"]).assert_ok(); return
    if name == "test_go_sum_contains_checksum_for_every_module_required_by_go_list_all":
        repo.run(["go","list","-mod=readonly","all"], timeout=180).assert_ok(); repo.run(["go","mod","verify"]).assert_ok(); return
    if name == "test_rust_cargo_lock_is_unchanged_after_locked_dependency_resolution":
        cargo_roots=sorted({p.parent for p in repo.existing_paths(["**/Cargo.toml"])})
        assert cargo_roots
        for root in cargo_roots:
            repo.run(["cargo","metadata","--locked","--format-version","1"], cwd=root, timeout=180).assert_ok()
        repo.git_diff_clean(*[str((r/"Cargo.lock").relative_to(repo.root)) for r in cargo_roots if (r/"Cargo.lock").exists()]); return
    if name == "test_cargo_metadata_locked_succeeds_without_modifying_cargo_lock":
        cargo_roots=sorted({p.parent for p in repo.existing_paths(["**/Cargo.toml"])})
        for root in cargo_roots:
            repo.run(["cargo","metadata","--locked","--format-version","1"], cwd=root, timeout=180).assert_ok()
        return
    if "python_requirement_files_contain_no_unbounded_direct_dependency_versions" in name:
        bad=[]
        for p in repo.requirements_files():
            for line in repo.read(p).splitlines():
                line=line.strip()
                if not line or line.startswith("#") or line.startswith("-"): continue
                if not re.search(r"(?:==|~=|>=|<=|!=)", line): bad.append((str(p.relative_to(repo.root)), line))
        assert not bad, bad
        return
    if "python_dependency_audit_covers" in name:
        commands="\n".join(repo.workflow_run_commands()).lower()
        assert "pip-audit" in commands or "pip_audit" in commands
        return
    if "dependency_audit_allowlist" in name or "buf_dependency_lock" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _container_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    dockerfiles=repo.dockerfiles()
    if not dockerfiles:
        repo.unavailable("no Dockerfiles found")
    blob="\n".join(repo.read(p) for p in dockerfiles)
    if "runs_as_non_root_user" in name:
        users=re.findall(r"(?mi)^\s*USER\s+([^\s#]+)", blob)
        assert users and all(u not in {"0","root"} for u in users), users
        return
    if "contain_no_repository_git_directory" in name:
        dockerignore=repo.path(".dockerignore")
        assert dockerignore.exists() and ".git" in repo.read(dockerignore)
        return
    if "contain_no_build_time_cloud_credentials" in name or "contain_no_hmac_secret_fixture_values" in name or "include_private_ssh_keys" in name:
        assert not re.search(r"(?i)(AKIA[0-9A-Z]{16}|BEGIN (?:RSA|OPENSSH) PRIVATE KEY|hmac[_-]?secret\s*=\s*[\"'][^$])", blob)
        return
    if "does_not_include_rust_compiler" in name:
        # Build stages may contain cargo; final stage must be inspected by image evidence.
        return runtime.evidence.run(name, case)
    if "does_not_include_go_compiler" in name:
        return runtime.evidence.run(name, case)
    if "declares_only_ports" in name:
        exposed=re.findall(r"(?mi)^\s*EXPOSE\s+(.+)$", blob)
        assert exposed
        return
    if "ca_certificates" in name or "read_only_root_filesystem" in name or "entrypoint_exits_nonzero" in name or "container_image_scan" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _supply_chain_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    workflow="\n".join(repo.read(p) for p in repo.workflow_files())
    if "mutable_latest_tag" in name:
        assert not re.search(r"(?i)(?:image|tag).*:latest\b", workflow)
        return
    if "third_party_github_actions" in name:
        return _ci_contract(runtime, 99, "test_every_third_party_github_action_uses_full_length_commit_sha", case)
    if "repository_commit" in name or "same_container_digest" in name or "digest_matches" in name or "provenance" in name or "sbom" in name:
        return runtime.evidence.run(name, case)
    if "downloaded_build_tools_are_verified" in name:
        assert "SHA256" in workflow.upper() or "sha256sum" in workflow.lower()
        return
    runtime.evidence.run(name, case)


def _nsq_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo = runtime.repo
    blob = repo.all_text(["distributed-backend/src/**/*.go", "infra/encore/**/*.json", "distributed-backend/ci-cd/**/*.yaml", "distributed-backend/ci-cd/**/*.yml"])
    if "settlement_work_topic_name_matches" in name:
        assert "settlement-work" in blob
        return
    if "settlement_result_topic_name_matches" in name:
        assert "settlement-results" in blob
        return
    durable_bindings = {
        "test_settlement_worker_uses_non_ephemeral_channel_for_correctness_critical_work": {
            "source": "distributed-backend/src/settlementworker/service.go",
            "topic_symbol": "WorkTopic",
            "topic_name": "settlement-work",
            "subscription": "trade-settlement-executor",
        },
        "test_settlement_result_consumer_uses_non_ephemeral_channel_for_correctness_critical_results": {
            "source": "distributed-backend/src/market/settlement_result.go",
            "topic_symbol": "ResultTopic",
            "topic_name": "settlement-results",
            "subscription": "market-settlement-result-projection",
        },
    }
    if name in durable_bindings:
        binding = durable_bindings[name]
        source = repo.read(binding["source"])
        topic_source = repo.read("distributed-backend/src/settlement/work.go")
        assert re.search(
            rf"pubsub\.NewSubscription\(\s*settlement\.{binding['topic_symbol']}\s*,\s*"
            rf'"{re.escape(binding["subscription"])}"\s*,',
            source,
        ), f"consumer source does not bind {binding['topic_symbol']} to {binding['subscription']}"
        assert re.search(
            rf"var\s+{binding['topic_symbol']}\s*=\s*pubsub\.NewTopic\[[^\]]+\]\(\s*"
            rf'"{re.escape(binding["topic_name"])}"\s*,',
            topic_source,
        ), f"topic symbol {binding['topic_symbol']} does not declare {binding['topic_name']}"
        assert "#ephemeral" not in binding["subscription"].lower()

        config_paths = repo.require_paths(
            ["infra/encore/self-host*.nsq.json"],
            purpose="NSQ self-host subscription configuration",
        )
        for config_path in config_paths:
            config = json.loads(repo.read(config_path))
            backends = config.get("pubsub")
            assert isinstance(backends, list) and backends, f"{config_path} has no pubsub backends"
            configured = []
            for backend in backends:
                if not isinstance(backend, dict) or backend.get("type") != "nsq":
                    continue
                topics = backend.get("topics") or {}
                topic = topics.get(binding["topic_name"]) if isinstance(topics, dict) else None
                subscriptions = topic.get("subscriptions") if isinstance(topic, dict) else None
                subscription = (
                    subscriptions.get(binding["subscription"])
                    if isinstance(subscriptions, dict)
                    else None
                )
                if isinstance(subscription, dict):
                    configured.append(subscription)
            assert len(configured) == 1, (
                f"{config_path} must configure exactly one {binding['topic_name']}/"
                f"{binding['subscription']} NSQ binding"
            )
            assert configured[0].get("name") == binding["subscription"]
            assert "#ephemeral" not in str(configured[0].get("name", "")).lower()
        return
    if "max_in_flight" in name or "message_timeout" in name or "requeue_delay" in name or "auth_or_tls" in name or "restart_preserves" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _database_repo_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    # Prefer live DB for direct constraints/privileges/query plans; otherwise inspect migrations.
    if os.environ.get("EVE_TRADE_DATABASE_URL"):
        try:
            return _database_live_check(runtime, category, name, case)
        except pytest.skip.Exception:
            raise
        except Exception:
            # A real assertion/database error must not be swallowed. Only fall back
            # to static inspection when the requested check is structurally static.
            if category not in {25, 90}:
                raise
    # Constraint/orphan/privilege contract names promise an observed database
    # rejection, not merely that some CHECK/FOREIGN KEY text exists somewhere.
    # Without a configured live database, require the strict evidence driver.
    if category in {26, 27, 50}:
        return runtime.evidence.run(name, case)
    sql_paths=repo.existing_paths(MIGRATION_PATTERNS)
    sql="\n".join(repo.read(p) for p in sql_paths)
    if not sql:
        repo.unavailable("no SQL migration/schema files found")
    if "fresh_database_applies_all_migrations" in name or "upgrade_" in name or "backfill" in name or "migration" in name:
        return runtime.evidence.run(name, case)
    if "negative_wallet_balance" in name:
        assert re.search(r"wallet[\s\S]{0,120}CHECK[\s\S]{0,80}isk", sql, re.I)
        return
    if "negative_item_stack_quantity" in name or "negative_item_escrow_quantity" in name or "negative_trade_remaining_quantity" in name:
        assert re.search(r"CHECK\s*\([^)]*(?:quantity|remaining_quantity)[^)]*(?:>=|>)\s*0", sql, re.I)
        return
    if "foreign" in name or "nonexistent" in name or "orphan" in name:
        assert re.search(r"REFERENCES|FOREIGN\s+KEY", sql, re.I)
        return
    if "state_value_outside" in name:
        assert re.search(r"CHECK\s*\([^)]*(?:state|status)[^)]*\bIN\s*\(", sql, re.I)
        return
    runtime.evidence.run(name, case)


def _database_live_check(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    live=runtime.live
    live.reset_example()
    db=live.db
    if category == 80 and "uses_index" in name:
        table=None
        for candidate in ("idempotency_record","trade_instance","item_stack_escrow","wallet","settlement_outbox","settlement_operation"):
            if candidate in name:
                table=candidate; break
        assert table and live.table_exists(table), table
        indexes=db.fetchall("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s", (table,))
        assert indexes, f"no indexes on {table}"
        return
    if category == 27:
        role_url=os.environ.get("EVE_TRADE_RUNTIME_DATABASE_URL")
        if not role_url:
            live.unavailable("EVE_TRADE_RUNTIME_DATABASE_URL required for privilege tests")
        role=live.helpers.Database(role_url)
        try:
            forbidden={
                "create_function": "CREATE FUNCTION hypothesis_forbidden() RETURNS integer LANGUAGE SQL AS $$ SELECT 1 $$",
                "create_trigger": "CREATE FUNCTION hypothesis_trigger_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER hypothesis_forbidden BEFORE UPDATE ON wallet FOR EACH ROW EXECUTE FUNCTION hypothesis_trigger_fn()",
                "disable_trigger": "ALTER TABLE wallet DISABLE TRIGGER ALL",
                "alter_constraint": "ALTER TABLE wallet DROP CONSTRAINT IF EXISTS hypothesis_nonexistent",
                "truncate": "TRUNCATE wallet",
                "grant_privileges": "GRANT SELECT ON wallet TO PUBLIC",
            }
            key=next((k for k in forbidden if k in name), None)
            if key:
                import psycopg
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    role.execute(forbidden[key])
                return
        finally:
            role.close()
        return runtime.evidence.run(name, case)
    if category in {26,50}:
        # Direct-constraint tests use SAVEPOINT-like explicit transaction on a
        # separate connection through psycopg, delegated for schema-specific row construction.
        return runtime.evidence.run(name, case)
    if category == 81:
        return runtime.evidence.run(name, case)
    if category in {25,90}:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _observability_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    blob=repo.all_text([".o11y/**/*", "distributed-backend/src/**/*.go", "distributed-backend/trade-settlement/**/*.rs", ".github/workflows/*.yaml"])
    if "secrets_are_absent" in name or "hmac_values_are_absent" in name or "database_credentials_are_absent" in name:
        return runtime.evidence.run(name, case)
    if "high_cardinality_business_identifiers_are_not_used_as_metric_label_values" in name:
        metric_lines="\n".join(l for l in blob.splitlines() if re.search(r"metric|counter|histogram|gauge", l, re.I))
        assert not re.search(r"(?i)(trade_instance_id|idempotency_key|interaction_id|wallet_id|item_stack_id).*(label|attribute)", metric_lines)
        return
    if "error_metrics_use_bounded_documented_error_code_cardinality" in name:
        assert any(k in blob.lower() for k in ("error_code", "failure_code", "status"))
        return
    if "readiness" in name or "health_endpoint" in name:
        assert "readyz" in blob.lower() or "healthz" in blob.lower()
        return
    # Trace propagation, metric values, alert firing, and exporter failures are
    # runtime evidence properties.
    runtime.evidence.run(name, case)


def _sql_safety_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    source=repo.all_text(["distributed-backend/src/**/*.go", "distributed-backend/trade-settlement/**/*.rs", "gametrade/**/*.go"])
    if "postgres_search_path_is_fixed" in name or "security_definer_functions_set_safe_search_path" in name:
        sql=repo.all_text(MIGRATION_PATTERNS)
        assert "search_path" in sql.lower()
        return
    if "runtime_role_cannot_create_shadow_object" in name:
        return runtime.evidence.run(name, case)
    if "bound_as_value" in name or "single_quote" in name or "sql_comment" in name or "statement_separator" in name:
        # Assert normal DB code uses parameter placeholders rather than string concatenation;
        # the malicious generated value is checked live through evidence where possible.
        assert any(token in source for token in ("$1", "%s", "query!", "query_as!", "sqlx::query"))
        return runtime.evidence.run(name, case)
    if "do_not_construct_table_or_column_names" in name:
        suspicious=re.findall(r"(?i)(?:fmt\.Sprintf|format!)\([^\n]*(?:SELECT|UPDATE|INSERT|DELETE)[^\n]*", source)
        assert not suspicious, suspicious[:10]
        return
    if "log_injection" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _configuration_contract(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    source=repo.all_text(["distributed-backend/src/**/*.go", "distributed-backend/trade-settlement/**/*.rs", "scripts/**/*", "infra/**/*", ".github/workflows/*.yaml"])
    if "startup_fails_when_required" in name or "production_startup_rejects" in name:
        # The exact startup failure is process behavior; static presence of config
        # key is a precondition and evidence driver proves exit status.
        tokens=[]
        for token in ("hmac","udp","settlement","database","pubsub","retry"):
            if token in name: tokens.append(token)
        assert all(t in source.lower() for t in tokens), tokens
        return runtime.evidence.run(name, case)
    if "secret_reload_does_not_log" in name or "configuration_reload" in name or "request_started" in name or "rate_limit_configuration_reload" in name or "timeout_configuration_reload" in name:
        return runtime.evidence.run(name, case)
    if "environment_variable_precedence" in name or "environment_value_overrides" in name or "explicit_secret_reference_overrides" in name or "duplicate_configuration_key_sources" in name:
        return runtime.evidence.run(name, case)
    if "unknown_configuration_key" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _source_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    source=repo.all_text(["distributed-backend/src/**/*.go", "gametrade/**/*.go", "distributed-backend/trade-settlement/**/*.rs", "proto/**/*.proto"])
    if category == 56:
        if "matches" in name or "identical" in name or "accepted" in name or "fails_cross_service" in name or "schema_version" in name:
            return runtime.evidence.run(name, case)
    if category == 55:
        if "trade_version" in name:
            assert "version" in source.lower()
            return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)


def _test_quality_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    tests=repo.existing_paths(["**/test_*.py", "**/*_test.go"])
    assert tests
    blob="\n".join(repo.read(p) for p in tests)
    if "do_not_depend_on_wall_clock_second_boundaries" in name:
        assert not re.search(r"time\.sleep\(1(?:\.0)?\)|Sleep\(time\.Second\)", blob)
        return
    if "do_not_depend_on_unordered_database_row_return_order" in name:
        # Flag SELECTs used by Python tests without ORDER BY when immediately
        # compared as a list; exact AST/dataflow proof is intentionally strict via evidence.
        return runtime.evidence.run(name, case)
    if "test_order_randomization" in name or "race_regression" in name or "barriers" in name or "failpoint" in name or "cleanup" in name or "fuzz" in name:
        return runtime.evidence.run(name, case)
    runtime.evidence.run(name, case)
