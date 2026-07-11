"""Create and validate provenance-bound CI producer evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_SCHEMA_VERSION = "o11y.ci-evidence.v1"
REQUIRED_CONTEXT_FIELDS = (
    "repository",
    "branch_ref",
    "commit_sha",
    "workflow",
    "run_id",
    "run_attempt",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "artifact_digest"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def github_context(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environment or os.environ
    return {
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "branch_ref": env.get("GITHUB_REF", ""),
        "commit_sha": env.get("GITHUB_SHA", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
    }


def start_evidence(path: Path, *, job_id: str, job_name: str, environment: Mapping[str, str] | None = None) -> None:
    context = github_context(environment)
    _require_context(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        path,
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            **context,
            "job_id": job_id,
            "job_name": job_name,
            "started_at": utc_now(),
        },
    )


def finish_evidence(
    start_path: Path,
    output_path: Path,
    *,
    job_id: str,
    job_name: str,
    step_identity: str,
    command_identity: str,
    status: str,
    dependencies: list[str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    context = github_context(environment)
    _require_context(context)
    started = _read_json(start_path)
    errors = verify_evidence_context(started, context)
    if started.get("job_id") != job_id or started.get("job_name") != job_name:
        errors.append("start marker job identity does not match final evidence")
    if errors:
        raise ValueError("; ".join(errors))
    normalized_status = status.strip().lower()
    if normalized_status not in {"success", "failure", "cancelled", "skipped"}:
        raise ValueError(f"unsupported job status: {status!r}")
    bundle: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        **context,
        "job_id": job_id,
        "job_name": job_name,
        "step_identity": step_identity,
        "started_at": started["started_at"],
        "ended_at": utc_now(),
        "command_identity": command_identity,
        "exit_status": normalized_status,
        "normalized_diagnostic": {
            "class": "NONE" if normalized_status == "success" else "OBSERVED_FAILURE",
            "summary": f"Job {job_name} concluded {normalized_status}.",
            "caused_by": [],
        },
        "dependencies": sorted(set(dependencies)),
        "collector_status": "COMPLETE",
        "provenance": {
            "event_name": (environment or os.environ).get("GITHUB_EVENT_NAME", ""),
            "runner_name": (environment or os.environ).get("RUNNER_NAME", ""),
            "runner_os": (environment or os.environ).get("RUNNER_OS", ""),
        },
    }
    bundle["artifact_digest"] = canonical_digest(bundle)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, bundle)
    return bundle


def verify_evidence_context(bundle: Mapping[str, Any], expected: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    if bundle.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("unsupported evidence schema")
    for field in REQUIRED_CONTEXT_FIELDS:
        actual = str(bundle.get(field, ""))
        wanted = str(expected.get(field, ""))
        if not actual or actual != wanted:
            errors.append(f"{field} mismatch: expected {wanted!r}, got {actual!r}")
    if bundle.get("collector_status") != "COMPLETE" and "artifact_digest" in bundle:
        errors.append("collector did not complete")
    if "artifact_digest" in bundle and bundle.get("artifact_digest") != canonical_digest(bundle):
        errors.append("artifact digest mismatch")
    return errors


def load_evidence_directory(path: Path, expected: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    evidence: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.is_dir():
        return evidence, [f"producer evidence directory is missing: {path}"]
    for candidate in sorted(path.glob("*.json")):
        try:
            bundle = _read_json(candidate)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{candidate.name}: corrupted artifact: {exc}")
            continue
        bundle_errors = verify_evidence_context(bundle, expected)
        if bundle_errors:
            errors.extend(f"{candidate.name}: {message}" for message in bundle_errors)
            continue
        evidence.append(bundle)
    return evidence, errors


def safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def _require_context(context: Mapping[str, str]) -> None:
    missing = [field for field in REQUIRED_CONTEXT_FIELDS if not context.get(field)]
    if missing:
        raise ValueError(f"missing GitHub context: {', '.join(missing)}")


def _read_json(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("evidence JSON must be an object")
    return parsed


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--job-id", required=True)
    start.add_argument("--job-name", required=True)
    start.add_argument("--path", type=Path, required=True)
    finish = subparsers.add_parser("finish")
    finish.add_argument("--job-id", required=True)
    finish.add_argument("--job-name", required=True)
    finish.add_argument("--step-identity", required=True)
    finish.add_argument("--command-identity", required=True)
    finish.add_argument("--status", required=True)
    finish.add_argument("--dependency", action="append", default=[])
    finish.add_argument("--start-path", type=Path, required=True)
    finish.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "start":
        start_evidence(args.path, job_id=args.job_id, job_name=args.job_name)
        return
    finish_evidence(
        args.start_path,
        args.output_path,
        job_id=args.job_id,
        job_name=args.job_name,
        step_identity=args.step_identity,
        command_identity=args.command_identity,
        status=args.status,
        dependencies=args.dependency,
    )


if __name__ == "__main__":
    main()
