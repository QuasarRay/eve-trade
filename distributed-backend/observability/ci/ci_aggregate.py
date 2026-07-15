"""Aggregate provenance-validated producer evidence from GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from observability.ci.ci_evidence import github_context, load_evidence_directory  # noqa: E402
from observability.ci.diagnosis import DIAGNOSIS_SCHEMA_VERSION, WORKFLOW_EVENT  # noqa: E402
from observability.ci.generate_failure_report import generate_run_report  # noqa: E402
from observability.ci.provenance import RUNNER_VERSION  # noqa: E402
from observability.ci.redaction import redact_text  # noqa: E402
from observability.ci.run_context import create_run_context, finalize_run_context  # noqa: E402
from observability.ci.storage import RunStorage  # noqa: E402


CI_AGGREGATE_SCHEMA_VERSION = "o11y.ci-aggregate.v2"
CLASSIFIER_VERSION = "o11y-ci-aggregate-2026-07-10"
FAILURE_RESULTS = {"failure", "cancelled", "timed_out", "action_required"}
DEPENDENCY_GRAPH = {
    "go": {"proto"},
    "e2e": {"go", "rust-trade-settlement", "terraform", "kubernetes", "python", "architecture", "gui-contract", "security"},
}


def load_needs(value: str) -> dict[str, Any]:
    value = value.lstrip("\ufeff")
    if not value.strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("GitHub needs JSON must be an object")
    return parsed


def summarize_needs(needs: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for name, raw_value in needs.items():
        value = raw_value if isinstance(raw_value, dict) else {}
        result = str(value.get("result", "unknown") or "unknown").lower()
        jobs.append(
            {
                "job": name,
                "result": result,
                "outputs": value.get("outputs", {}) if isinstance(value.get("outputs", {}), dict) else {},
            }
        )
    return jobs


def assess_evidence(
    needs: dict[str, Any],
    evidence: list[dict[str, Any]],
    evidence_errors: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_job: dict[str, list[dict[str, Any]]] = {}
    for bundle in evidence:
        by_job.setdefault(str(bundle.get("job_id", "")), []).append(bundle)
    missing = list(evidence_errors)
    assessed: list[dict[str, Any]] = []
    for job in summarize_needs(needs):
        bundles = by_job.get(job["job"], [])
        if not bundles:
            missing.append(f"mandatory producer evidence is missing for job {job['job']}")
        elif job["result"] != "skipped" and not any(bundle.get("commands") for bundle in bundles):
            missing.append(f"mandatory command evidence is missing for job {job['job']}")
        if job["job"] == "e2e" and job["result"] == "success" and not _successful_e2e_command(bundles):
            missing.append("successful e2e job lacks nonzero-duration passing test evidence")
        if job["job"] == "go" and job["result"] == "success":
            required = {"Run Go tests", "Run Go race detector", "Run Go vulnerability audit"}
            observed = {
                command.get("step_name")
                for bundle in bundles
                for command in bundle.get("commands", [])
                if command.get("exit_code") == 0
            }
            for step in sorted(required - observed):
                missing.append(f"successful go job lacks passing command evidence for {step}")
        assessed.append({**job, "evidence": bundles})
    return assessed, missing


def diagnose_ci_needs(
    *,
    run_id: str,
    needs: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    evidence_errors: list[str] | None = None,
) -> dict[str, Any]:
    jobs, missing = assess_evidence(needs, evidence or [], evidence_errors or [])
    failed_names = {job["job"] for job in jobs if job["result"] in FAILURE_RESULTS}
    events = [_job_event(job, failed_names) for job in jobs]
    events.sort(key=lambda event: event["timestamp"] or "9999")
    e2e = next((job for job in jobs if job["job"] == "e2e"), None)
    e2e_status = _e2e_status(e2e, failed_names)
    evidence_complete = bool(jobs) and not missing
    all_success = evidence_complete and all(job["result"] == "success" for job in jobs)
    validation_result = "passed" if all_success else "failed"
    product_status = "PASSED" if all_success and e2e_status == "PASSED" else "FAILED" if e2e_status == "FAILED" else "UNRESOLVED"
    observed_failures = [event for event in events if event["classification"] == "OBSERVED_FAILURE"]
    primary = _primary_diagnosis(jobs, observed_failures, missing)
    commands = [
        command
        for job in jobs
        for bundle in job["evidence"]
        for command in bundle.get("commands", [])
    ]
    e2e_summary = _e2e_summary(commands)
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "requested_command": "ci-aggregate",
        "validation_result": validation_result,
        "product_status": product_status,
        "harness_status": "OK" if all_success else "INSUFFICIENT_EVIDENCE" if missing else "CI_WORKFLOW_FAILED",
        "analysis_status": "OK" if evidence_complete else "INSUFFICIENT_EVIDENCE",
        "commands": commands,
        "ci_jobs": [{key: value for key, value in job.items() if key != "evidence"} for job in jobs],
        "test_execution": {
            "E2E_TEST": {
                "status": e2e_status,
                "tests_collected": int(e2e_summary.get("collected_count", 0)),
                "tests_started": int(e2e_summary.get("collected_count", 0)),
                "tests_passed": int(e2e_summary.get("passed_count", 0)),
                "tests_failed": int(e2e_summary.get("failed_count", 0)) + int(e2e_summary.get("error_count", 0)),
                "tests_skipped": int(e2e_summary.get("skipped_count", 0)),
                "duration_seconds": float(e2e_summary.get("duration_seconds", 0.0)),
            }
        },
        "observations": [
            {
                "id": f"O{index + 1}",
                "kind": "OBSERVATION",
                "summary": event["message"],
                "evidence_reference": event["evidence_reference"],
                "confidence_band": _confidence_band(event["confidence"]),
            }
            for index, event in enumerate(events)
        ],
        "derived_facts": [],
        "inferences": [],
        "recommendations": _recommendations(observed_failures, missing),
        "events": events,
        "causal_chain": [],
        "earliest_causal_failure": None,
        "earliest_failed_command": None,
        "most_supported_root_cause_event": None,
        "primary_diagnosis": primary,
        "false_green_risks": ["Mandatory producer evidence is absent or invalid."] if missing else [],
        "false_red_risks": [],
        "missing_evidence": missing,
        "abstained": bool(observed_failures or missing),
        "confidence_model": {
            "validated_provenance": 0.25,
            "direct_job_result": 0.30,
            "normalized_diagnostic": 0.15,
            "dependency_link": 0.15,
            "temporal_consistency": 0.10,
            "independent_reproduction": 0.05,
            "rule": "Causal labels require an explicit evidence link; job failure alone is observational.",
        },
    }


def aggregate_exit_code(
    needs: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    evidence_errors: list[str] | None = None,
) -> int:
    jobs, missing = assess_evidence(needs, evidence or [], evidence_errors or [])
    if not jobs or missing:
        return 1
    return 0 if all(job["result"] == "success" for job in jobs) else 1


def _e2e_summary(commands: list[dict[str, Any]]) -> dict[str, Any]:
    for command in commands:
        for diagnostic in command.get("diagnostics", []):
            if diagnostic.get("type") == "e2e":
                return diagnostic
    return {}


def _successful_e2e_command(bundles: list[dict[str, Any]]) -> bool:
    commands = [command for bundle in bundles for command in bundle.get("commands", [])]
    summary = _e2e_summary(commands)
    return bool(
        any(command.get("step_name") == "Run observed integration tests" and command.get("exit_code") == 0 for command in commands)
        and int(summary.get("collected_count", 0)) > 0
        and int(summary.get("passed_count", 0)) > 0
        and int(summary.get("failed_count", 0)) == 0
        and int(summary.get("error_count", 0)) == 0
        and float(summary.get("duration_seconds", 0.0)) > 0
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--needs-json", default=os.getenv("OBS_CI_NEEDS_JSON", ""))
    parser.add_argument("--needs-file", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path(".o11y/producer-artifacts"))
    args = parser.parse_args(argv)
    raw_needs = args.needs_file.read_text(encoding="utf-8-sig") if args.needs_file else args.needs_json
    needs = load_needs(raw_needs)
    expected_context = github_context()
    evidence, evidence_errors = load_evidence_directory(args.evidence_dir, expected_context)
    context = create_run_context()
    storage = RunStorage(context.run_dir)
    exit_code = aggregate_exit_code(needs, evidence, evidence_errors)
    diagnosis: dict[str, Any] = {}
    report_path = ""
    try:
        jobs, missing = assess_evidence(needs, evidence, evidence_errors)
        storage.write_json(
            "ci/jobs-summary.json",
            {"schema_version": CI_AGGREGATE_SCHEMA_VERSION, "needs": needs, "jobs": jobs, "evidence_errors": missing},
        )
        for index, bundle in enumerate(evidence):
            storage.write_json(f"ci/producer-evidence/{index:03d}-{bundle['job_id']}.json", bundle)
        diagnosis = diagnose_ci_needs(run_id=context.run_id, needs=needs, evidence=evidence, evidence_errors=evidence_errors)
        storage.write_json("diagnosis.json", diagnosis)
        report = generate_run_report(context, diagnosis, storage=storage)
        report_path = report["markdown"].relative_to(context.run_dir).as_posix()
        final_provenance = finalize_run_context(
            context,
            status="COMPLETE" if not missing else "INSUFFICIENT_EVIDENCE",
            command="ci-aggregate",
            exit_code=exit_code,
            commands_executed=[],
            diagnosis_path="diagnosis.json",
            report_path=report_path,
            storage=storage,
        )
        generate_run_report(context, diagnosis, provenance=final_provenance, storage=storage)
    except Exception:
        storage.write_text("observability-error.txt", redact_text(traceback.format_exc()))
        exit_code = 2
        try:
            finalize_run_context(
                context,
                status="ANALYSIS_FAILED",
                command="ci-aggregate",
                exit_code=exit_code,
                commands_executed=[],
                diagnosis_path="diagnosis.json" if diagnosis else "",
                report_path=report_path,
                storage=storage,
            )
        except Exception:
            storage.write_text("observability-finalization-error.txt", redact_text(traceback.format_exc()))
    print(context.run_dir)
    raise SystemExit(exit_code)


def _job_event(job: dict[str, Any], failed_names: set[str]) -> dict[str, Any]:
    bundles = job["evidence"]
    result = job["result"]
    dependencies = DEPENDENCY_GRAPH.get(job["job"], set())
    if result == "skipped" and dependencies.intersection(failed_names):
        classification = "SKIPPED_DUE_TO_DEPENDENCY"
    elif result == "skipped":
        classification = "BLOCKED"
    elif result in FAILURE_RESULTS:
        classification = "OBSERVED_FAILURE"
    elif result == "success" and bundles:
        classification = "OBSERVED_SUCCESS"
    else:
        classification = "INSUFFICIENT_EVIDENCE"
    started_at = min((str(bundle.get("started_at", "")) for bundle in bundles), default="")
    confidence = 0.30 + (0.25 if bundles else 0.0)
    if bundles and all(bundle.get("normalized_diagnostic") for bundle in bundles):
        confidence += 0.15
    if classification == "SKIPPED_DUE_TO_DEPENDENCY":
        confidence += 0.15
    return {
        "event_id": f"JOB:{job['job']}",
        "timestamp": started_at,
        "stage": "CI_WORKFLOW",
        "command_id": ",".join(str(bundle.get("command_identity", "")) for bundle in bundles),
        "process_id_if_available": "",
        "component": job["job"],
        "event_type": f"JOB_{result.upper()}",
        "severity": "error" if result in FAILURE_RESULTS else "warning" if result == "skipped" else "info",
        "message": f"GitHub Actions job {job['job']} concluded {result}; classified as {classification}.",
        "source_file_or_log": "ci/producer-evidence" if bundles else "ci/jobs-summary.json",
        "evidence_reference": "ci/producer-evidence" if bundles else "ci/jobs-summary.json",
        "caused_by": [f"JOB:{name}" for name in sorted(dependencies.intersection(failed_names))]
        if classification == "SKIPPED_DUE_TO_DEPENDENCY"
        else [],
        "relation": classification,
        "classification": classification,
        "confidence": min(confidence, 0.95),
        "event_source": WORKFLOW_EVENT,
    }


def _primary_diagnosis(
    jobs: list[dict[str, Any]], observed_failures: list[dict[str, Any]], missing: list[str]
) -> dict[str, Any]:
    if missing:
        summary = "CI causality is unresolved because mandatory current-run producer evidence is missing or invalid."
        mechanism = "INSUFFICIENT_EVIDENCE"
        confidence = 0.95
    elif observed_failures:
        summary = "CI contains observed failures, but no validated evidence establishes a root cause."
        mechanism = "OBSERVED_FAILURE"
        confidence = 0.70
    else:
        summary = "All required jobs and their current-run producer evidence concluded successfully."
        mechanism = "NONE_OBSERVED"
        confidence = 0.70 if jobs else 0.0
    return {
        "summary": summary,
        "category_dimensions": {
            "stage": "CI_WORKFLOW",
            "mechanism": mechanism,
            "component": "",
            "external_system": "github-actions",
        },
        "confidence_score": confidence,
        "confidence_band": _confidence_band(confidence),
        "supporting_evidence": [event["message"] for event in observed_failures],
        "contradicting_evidence": [],
        "missing_evidence": missing,
        "unsupported_diagnoses": ["A failed job is not a confirmed root cause without direct causal evidence."],
    }


def _e2e_status(e2e: dict[str, Any] | None, failed_names: set[str]) -> str:
    if not e2e:
        return "NOT_SCHEDULED"
    result = e2e["result"]
    if result == "success":
        return "PASSED" if _successful_e2e_command(e2e["evidence"]) else "INSUFFICIENT_EVIDENCE"
    if result == "failure":
        return "FAILED"
    if result == "skipped":
        return "BLOCKED" if DEPENDENCY_GRAPH["e2e"].intersection(failed_names) else "SKIPPED"
    if result in {"cancelled", "timed_out"}:
        return "BLOCKED"
    return "INSUFFICIENT_EVIDENCE"


def _recommendations(observed_failures: list[dict[str, Any]], missing: list[str]) -> list[dict[str, str]]:
    recommendations = [
        {
            "kind": "RECOMMENDATION",
            "action": "Restore and validate the missing current-run producer evidence.",
            "rationale": message,
            "would_confirm_or_reject": "Matching provenance and digest would permit evidence-based classification.",
        }
        for message in missing
    ]
    recommendations.extend(
        {
            "kind": "RECOMMENDATION",
            "action": f"Inspect direct diagnostics for job {event['component']}.",
            "rationale": "The current evidence proves failure but not causality.",
            "would_confirm_or_reject": "A validated diagnostic with an explicit dependency or reproduction link may support LIKELY_CAUSE.",
        }
        for event in observed_failures
    )
    return recommendations


def _confidence_band(score: float) -> str:
    if score >= 0.9:
        return "CONFIRMED"
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


if __name__ == "__main__":
    main()
