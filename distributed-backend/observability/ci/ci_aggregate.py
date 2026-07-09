"""Always-run GitHub Actions aggregation for observed validation evidence."""

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

from observability.ci.diagnosis import (  # noqa: E402
    CONFIRMED,
    DIAGNOSIS_SCHEMA_VERSION,
    HIGH,
    MISSING_EVIDENCE_EVENT,
    WORKFLOW_EVENT,
)
from observability.ci.generate_failure_report import generate_run_report  # noqa: E402
from observability.ci.provenance import RUNNER_VERSION  # noqa: E402
from observability.ci.redaction import redact_text  # noqa: E402
from observability.ci.run_context import create_run_context, finalize_run_context  # noqa: E402
from observability.ci.storage import RunStorage  # noqa: E402


CI_AGGREGATE_SCHEMA_VERSION = "o11y.ci-aggregate.v1"
CLASSIFIER_VERSION = "o11y-ci-aggregate-2026-07-09"


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
    for name in sorted(needs):
        value = needs[name] if isinstance(needs[name], dict) else {}
        result = str(value.get("result", "unknown") or "unknown").lower()
        jobs.append(
            {
                "job": name,
                "result": result,
                "outputs": value.get("outputs", {}) if isinstance(value.get("outputs", {}), dict) else {},
            }
        )
    return jobs


def diagnose_ci_needs(*, run_id: str, needs: dict[str, Any]) -> dict[str, Any]:
    jobs = summarize_needs(needs)
    failing = [job for job in jobs if job["result"] in {"failure", "cancelled", "timed_out", "action_required"}]
    skipped = [job for job in jobs if job["result"] == "skipped"]
    e2e = next((job for job in jobs if job["job"] == "e2e"), None)
    root_jobs = failing or skipped
    events = _workflow_events(failing, skipped)
    primary = _primary_from_jobs(jobs, failing, skipped)
    e2e_status = _e2e_status(e2e, failing)
    all_success = jobs and all(job["result"] == "success" for job in jobs)
    validation_result = "passed" if all_success else "failed" if jobs else "unknown"
    product_status = "PASSED" if e2e_status == "PASSED" and all_success else "FAILED" if e2e_status == "FAILED" else "UNRESOLVED"
    harness_status = "OK" if all_success else "CI_WORKFLOW_FAILED" if failing else "CI_WORKFLOW_BLOCKED"
    root_event = next((event for event in events if event.get("relation") == "ROOT_CAUSE"), events[0] if events else None)
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "requested_command": "ci-aggregate",
        "validation_result": validation_result,
        "product_status": product_status,
        "harness_status": harness_status,
        "analysis_status": "OK",
        "commands": [],
        "ci_jobs": jobs,
        "test_execution": {
            "E2E_TEST": {
                "status": e2e_status,
                "tests_collected": 0,
                "tests_started": 0,
                "tests_passed": 0,
                "tests_failed": 0,
                "tests_skipped": 0,
            }
        },
        "observations": [
            {
                "id": f"O{index + 1}",
                "kind": "OBSERVATION",
                "summary": f"GitHub job {job['job']} concluded {job['result']}.",
                "evidence_reference": "ci/jobs-summary.json",
                "confidence_band": CONFIRMED,
            }
            for index, job in enumerate(jobs)
        ],
        "derived_facts": [
            {
                "id": "D1",
                "kind": "DERIVED_FACT",
                "summary": "Observed E2E validation was blocked by upstream jobs." if e2e_status == "BLOCKED" else f"Observed E2E status is {e2e_status}.",
                "based_on": [event["event_id"] for event in events] or ["ci/jobs-summary.json"],
                "confidence_band": CONFIRMED if jobs else "UNKNOWN",
            }
        ],
        "inferences": [],
        "recommendations": _recommendations(root_jobs),
        "events": events,
        "causal_chain": [event["event_id"] for event in events],
        "earliest_causal_failure": events[0] if events else None,
        "earliest_failed_command": None,
        "most_supported_root_cause_event": root_event,
        "primary_diagnosis": primary,
        "false_green_risks": [],
        "false_red_risks": [],
        "missing_evidence": _missing_evidence(root_jobs),
        "abstained": False,
    }


def aggregate_exit_code(needs: dict[str, Any]) -> int:
    jobs = summarize_needs(needs)
    if not jobs:
        return 0
    return 0 if all(job["result"] == "success" for job in jobs) else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate GitHub Actions job conclusions into observed-run artifacts")
    parser.add_argument("--needs-json", default=os.getenv("OBS_CI_NEEDS_JSON", ""))
    parser.add_argument("--needs-file", type=Path)
    args = parser.parse_args(argv)
    raw_needs = args.needs_file.read_text(encoding="utf-8-sig") if args.needs_file else args.needs_json
    needs = load_needs(raw_needs)
    context = create_run_context()
    storage = RunStorage(context.run_dir)
    exit_code = aggregate_exit_code(needs)
    diagnosis: dict[str, Any] = {}
    report_path = ""
    try:
        jobs = summarize_needs(needs)
        storage.write_json("ci/jobs-summary.json", {"schema_version": CI_AGGREGATE_SCHEMA_VERSION, "needs": needs, "jobs": jobs})
        diagnosis = diagnose_ci_needs(run_id=context.run_id, needs=needs)
        storage.write_json("diagnosis.json", diagnosis)
        report = generate_run_report(context, diagnosis, storage=storage)
        report_path = report["markdown"].relative_to(context.run_dir).as_posix()
        final_provenance = finalize_run_context(
            context,
            status="COMPLETE",
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


def _workflow_events(failing: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for job in failing:
        events.append(_event(f"W{len(events) + 1}", job["job"], job["result"], "ROOT_CAUSE", [], WORKFLOW_EVENT))
        events.append(
            _event(
                f"M{len(events) + 1}",
                job["job"],
                "missing_observed_artifact",
                "MISSING_EVIDENCE",
                [events[-1]["event_id"]],
                MISSING_EVIDENCE_EVENT,
            )
        )
    root_ids = [event["event_id"] for event in events if event.get("relation") == "ROOT_CAUSE"]
    for job in skipped:
        relation = "DOWNSTREAM_CONSEQUENCE" if root_ids else "ROOT_CAUSE"
        events.append(_event(f"W{len(events) + 1}", job["job"], "skipped", relation, root_ids, WORKFLOW_EVENT))
    return events


def _event(
    event_id: str,
    job: str,
    result: str,
    relation: str,
    caused_by: list[str],
    event_source: str,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": "CI_WORKFLOW",
        "command_id": job,
        "process_id_if_available": "",
        "component": job,
        "event_type": f"JOB_{result.upper()}",
        "severity": "error" if relation == "ROOT_CAUSE" else "warning",
        "message": f"GitHub Actions job {job} concluded {result}.",
        "source_file_or_log": "ci/jobs-summary.json",
        "evidence_reference": "ci/jobs-summary.json",
        "caused_by": caused_by,
        "relation": relation,
        "confidence": 1.0 if event_source == WORKFLOW_EVENT else 0.8,
        "event_source": event_source,
    }


def _primary_from_jobs(jobs: list[dict[str, Any]], failing: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    if failing:
        first = failing[0]
        return {
            "summary": f"CI validation did not complete because job {first['job']} concluded {first['result']}.",
            "category_dimensions": {
                "stage": "CI_WORKFLOW",
                "mechanism": "UPSTREAM_JOB_FAILED",
                "component": first["job"],
                "external_system": "github-actions",
            },
            "confidence_score": 1.0,
            "confidence_band": CONFIRMED,
            "supporting_evidence": [f"{job['job']} concluded {job['result']}" for job in failing],
            "contradicting_evidence": [],
            "missing_evidence": _missing_evidence(failing),
            "unsupported_diagnoses": ["E2E product regression is unsupported until the e2e job runs."],
        }
    if skipped:
        first = skipped[0]
        return {
            "summary": f"CI validation was incomplete because job {first['job']} was skipped.",
            "category_dimensions": {
                "stage": "CI_WORKFLOW",
                "mechanism": "JOB_SKIPPED",
                "component": first["job"],
                "external_system": "github-actions",
            },
            "confidence_score": 0.85,
            "confidence_band": HIGH,
            "supporting_evidence": [f"{job['job']} concluded skipped" for job in skipped],
            "contradicting_evidence": [],
            "missing_evidence": _missing_evidence(skipped),
            "unsupported_diagnoses": [],
        }
    return {
        "summary": "All required GitHub Actions jobs concluded successfully.",
        "category_dimensions": {
            "stage": "UNKNOWN",
            "mechanism": "NONE_OBSERVED",
            "component": "",
            "external_system": "github-actions",
        },
        "confidence_score": 1.0,
        "confidence_band": CONFIRMED,
            "supporting_evidence": ["Every job in the GitHub needs summary concluded success."] if jobs else ["No needs summary was supplied."],
        "contradicting_evidence": [],
        "missing_evidence": [],
        "unsupported_diagnoses": [],
    }


def _e2e_status(e2e: dict[str, Any] | None, failing: list[dict[str, Any]]) -> str:
    if not e2e:
        return "NOT_SCHEDULED"
    result = e2e["result"]
    if result == "success":
        return "PASSED"
    if result == "failure":
        return "FAILED"
    if result == "skipped":
        return "BLOCKED" if failing else "SKIPPED"
    if result in {"cancelled", "timed_out"}:
        return "BLOCKED"
    return "UNKNOWN"


def _recommendations(root_jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not root_jobs:
        return []
    return [
        {
            "kind": "RECOMMENDATION",
            "action": f"Inspect the GitHub Actions logs for job {job['job']}.",
            "rationale": f"The aggregate job saw {job['job']} conclude {job['result']}.",
            "would_confirm_or_reject": "A per-step failure log or artifact would identify the narrower root cause.",
        }
        for job in root_jobs
    ]


def _missing_evidence(root_jobs: list[dict[str, Any]]) -> list[str]:
    return [f"Per-step observed-run artifact for GitHub job {job['job']} was not available to the aggregate job." for job in root_jobs]


if __name__ == "__main__":
    main()
