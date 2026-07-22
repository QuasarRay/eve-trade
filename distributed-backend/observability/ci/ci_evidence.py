"""Create and validate provenance-bound CI producer evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from observability.ci.redaction import redact_text  # noqa: E402


EVIDENCE_SCHEMA_VERSION = "o11y.ci-evidence.v2"
COMMAND_EVIDENCE_SCHEMA_VERSION = "o11y.ci-command-evidence.v1"
EVIDENCE_SIGNATURE_VERSION = "sha256-workflow-bound-v1"
MAX_EXCERPT_CHARS = 8192
MAX_COMMAND_RECORDS = 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DEFINITION = REPOSITORY_ROOT / ".github" / "workflows" / "verify.yaml"
REQUIRED_CONTEXT_FIELDS = (
    "repository",
    "branch_ref",
    "commit_sha",
    "workflow",
    "run_id",
    "run_attempt",
    "workflow_definition_digest",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key not in {"artifact_digest", "signature"}}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def workflow_definition_digest(path: Path = WORKFLOW_DEFINITION) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def evidence_signature(value: Mapping[str, Any]) -> dict[str, str]:
    workflow_digest = str(value.get("workflow_definition_digest", ""))
    artifact_digest = str(value.get("artifact_digest", ""))
    payload = f"{EVIDENCE_SIGNATURE_VERSION}\n{workflow_digest}\n{artifact_digest}".encode("utf-8")
    return {
        "algorithm": EVIDENCE_SIGNATURE_VERSION,
        "key_id": workflow_digest,
        "value": f"sha256:{hashlib.sha256(payload).hexdigest()}",
    }


def sign_evidence(value: dict[str, Any]) -> dict[str, Any]:
    value["artifact_digest"] = canonical_digest(value)
    value["signature"] = evidence_signature(value)
    return value


def github_context(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environment or os.environ
    return {
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "branch_ref": env.get("GITHUB_REF", ""),
        "commit_sha": env.get("GITHUB_SHA", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "workflow_definition_digest": workflow_definition_digest(),
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
    commands_path: Path | None = None,
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
    commands = load_command_records(commands_path, context, job_id, job_name) if commands_path else []
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
        "commands": commands,
        "collector_status": "COMPLETE",
        "provenance": {
            "event_name": (environment or os.environ).get("GITHUB_EVENT_NAME", ""),
            "runner_name": (environment or os.environ).get("RUNNER_NAME", ""),
            "runner_os": (environment or os.environ).get("RUNNER_OS", ""),
        },
    }
    sign_evidence(bundle)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, bundle)
    return bundle


def run_command_evidence(
    output_path: Path,
    *,
    job_id: str,
    job_name: str,
    step_name: str,
    command: str,
    working_directory: Path,
    environment: Mapping[str, str] | None = None,
    runner: tuple[str, ...] = ("bash", "-euo", "pipefail", "-c"),
) -> int:
    context = github_context(environment)
    _require_context(context)
    started_at = utc_now()
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            [*runner, command],
            cwd=working_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(environment or os.environ),
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except OSError as exc:
        exit_code = 127
        stderr = str(exc)
    redacted_stdout = redact_text(stdout)
    redacted_stderr = redact_text(stderr)
    if redacted_stdout:
        print(redacted_stdout, end="" if redacted_stdout.endswith("\n") else "\n")
    if redacted_stderr:
        print(redacted_stderr, end="" if redacted_stderr.endswith("\n") else "\n", file=sys.stderr)
    sanitized_stdout = bounded_excerpt(redacted_stdout)
    sanitized_stderr = bounded_excerpt(redacted_stderr)
    record: dict[str, Any] = {
        "schema_version": COMMAND_EVIDENCE_SCHEMA_VERSION,
        "repository": context["repository"],
        "sha": context["commit_sha"],
        "workflow_run_id": context["run_id"],
        "workflow_run_attempt": context["run_attempt"],
        "job_id": job_id,
        "job_name": job_name,
        "step_name": step_name,
        "command": redact_text(command),
        "started_at": started_at,
        "finished_at": utc_now(),
        "exit_code": exit_code,
        "stdout_excerpt": sanitized_stdout,
        "stderr_excerpt": sanitized_stderr,
        "diagnostics": structured_diagnostics(step_name, command, redacted_stdout, redacted_stderr, exit_code)[:128],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, record)
    return exit_code


def bounded_excerpt(value: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    if len(value) <= limit:
        return value
    half = max(1, (limit - 80) // 2)
    omitted = len(value) - (half * 2)
    return f"{value[:half]}\n... <{omitted} characters omitted> ...\n{value[-half:]}"


def structured_diagnostics(
    step_name: str, command: str, stdout: str, stderr: str, exit_code: int
) -> list[dict[str, Any]]:
    combined = f"{stdout}\n{stderr}"
    lowered = f"{step_name} {command}".lower()
    diagnostics: list[dict[str, Any]] = []
    if "architecture" in lowered:
        for line in combined.splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("rule_id"):
                diagnostics.append({"type": "architecture", **parsed})
    if "terraform" in lowered:
        for match in re.finditer(r"(?m)^Error:\s*(?P<summary>.+)$", combined):
            detail_start = match.end()
            next_error = combined.find("\nError:", detail_start)
            detail = combined[detail_start : next_error if next_error >= 0 else None].strip()
            address = re.search(
                r"registry\.terraform\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|module\.[A-Za-z0-9_.\[\]\"-]+",
                detail,
            )
            diagnostics.append(
                {
                    "type": "terraform",
                    "severity": "error",
                    "summary": match.group("summary").strip(),
                    "detail": bounded_excerpt(detail, 2048),
                    "address": address.group(0) if address else "",
                }
            )
    if "buf" in lowered:
        paths = set()
        for match in re.finditer(r"(?m)^(?:diff -u\s+\S+\.orig\s+|\+\+\+\s+)(?P<path>\S+)", combined):
            path = match.group("path").removesuffix(".orig")
            if path != "/dev/null":
                paths.add(path)
        diagnostics.extend({"type": "buf_format", "path": path} for path in sorted(paths))
    if "govulncheck" in lowered or "vulnerability audit" in lowered:
        vulnerability_ids = list(dict.fromkeys(re.findall(r"GO-\d{4}-\d+", combined)))
        for index, vulnerability_id in enumerate(vulnerability_ids):
            section_start = combined.find(vulnerability_id)
            next_start = combined.find(vulnerability_ids[index + 1], section_start + len(vulnerability_id)) if index + 1 < len(vulnerability_ids) else -1
            section = combined[section_start : next_start if next_start >= 0 else None]
            fixed = re.search(r"(?m)^\s*Fixed in:\s*(\S+)", section)
            found = re.search(r"(?m)^\s*Found in:\s*(\S+)", section)
            affected = found.group(1) if found else ""
            fixed_value = fixed.group(1) if fixed else ""
            package = affected.rsplit("@", 1)[0] if "@" in affected else affected
            trace_lines: list[str] = []
            trace_marker = re.search(r"(?m)^\s*(?:Example traces found|Call traces):\s*$", section)
            if trace_marker:
                for line in section[trace_marker.end() :].splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith(("More info:", "Symbols:", "Your code is affected")):
                        break
                    trace_lines.append(stripped[:512])
                    if len(trace_lines) == 20:
                        break
            diagnostics.append(
                {
                    "type": "govulncheck",
                    "vulnerability_id": vulnerability_id,
                    "fixed_version": fixed_value.rsplit("@", 1)[-1],
                    "package": package,
                    "module": "standard-library" if re.search(r"@go\d+\.\d+(?:\.\d+)?$", affected) else "",
                    "reachable": True,
                    "call_traces": trace_lines,
                }
            )
    if "gui" in lowered or "pnpm" in lowered:
        missing = re.search(r"Cannot find module ['\"]([^'\"]+)", combined)
        if missing:
            diagnostics.append({"type": "gui", "category": "missing_package", "package": missing.group(1)})
        elif exit_code and combined.strip():
            diagnostics.append({"type": "gui", "category": "test_failure", "message": first_diagnostic_line(combined)})
    for match in re.finditer(r"(?m)^E2E_SUMMARY=(\{.*\})$", combined):
        try:
            summary = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(summary, dict):
            diagnostics.append({"type": "e2e", **summary})
    if exit_code and not diagnostics:
        diagnostics.append({"type": "command_failure", "message": first_diagnostic_line(combined)})
    return diagnostics


def first_diagnostic_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "command exited nonzero")[:512]


def load_command_records(
    path: Path, context: Mapping[str, str], job_id: str, job_name: str
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for candidate in sorted(path.glob("*.json"))[:MAX_COMMAND_RECORDS]:
        record = _read_json(candidate)
        expected = {
            "schema_version": COMMAND_EVIDENCE_SCHEMA_VERSION,
            "repository": context["repository"],
            "sha": context["commit_sha"],
            "workflow_run_id": context["run_id"],
            "workflow_run_attempt": context["run_attempt"],
            "job_id": job_id,
            "job_name": job_name,
        }
        mismatches = [key for key, value in expected.items() if record.get(key) != value]
        if mismatches:
            raise ValueError(f"command evidence context mismatch in {candidate.name}: {', '.join(mismatches)}")
        records.append(record)
    return records


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
    if bool((bundle.get("provenance") or {}).get("historical")):
        errors.append("historical producer evidence is non-current")
    if "artifact_digest" in bundle and bundle.get("artifact_digest") != canonical_digest(bundle):
        errors.append("artifact digest mismatch")
    if "artifact_digest" in bundle:
        signature = bundle.get("signature")
        if not isinstance(signature, dict):
            errors.append("signature is missing")
        elif signature != evidence_signature(bundle):
            errors.append("signature is unverifiable")
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
    finish.add_argument("--commands-path", type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--job-id", required=True)
    run.add_argument("--job-name", required=True)
    run.add_argument("--step-name", required=True)
    run.add_argument("--command-env", default="CI_EVIDENCE_COMMAND")
    run.add_argument("--working-directory", type=Path, default=Path("."))
    run.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "start":
        start_evidence(args.path, job_id=args.job_id, job_name=args.job_name)
        return
    if args.command == "run":
        command = os.environ.get(args.command_env, "")
        if not command:
            raise ValueError(f"command environment variable {args.command_env} is empty")
        raise SystemExit(
            run_command_evidence(
                args.output_path,
                job_id=args.job_id,
                job_name=args.job_name,
                step_name=args.step_name,
                command=command,
                working_directory=args.working_directory,
            )
        )
    finish_evidence(
        args.start_path,
        args.output_path,
        job_id=args.job_id,
        job_name=args.job_name,
        step_identity=args.step_identity,
        command_identity=args.command_identity,
        status=args.status,
        dependencies=args.dependency,
        commands_path=args.commands_path,
    )


if __name__ == "__main__":
    main()
