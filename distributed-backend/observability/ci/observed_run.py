"""Observed local/CI runner for checks, tests, Encore E2E, and evidence collection."""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from observability.ci.classify_failure import FailureClassification, classification_from_diagnosis
from observability.ci.collect_db import collect_db
from observability.ci.collect_environment import collect_environment
from observability.ci.collect_kubernetes import collect_kubernetes
from observability.ci.collect_pytest import PytestSummary, collect_pytest
from observability.ci.compare_runs import compare_runs
from observability.ci.diagnosis import diagnose_run
from observability.ci.generate_failure_report import generate_failure_report, generate_run_report
from observability.ci.honeycomb_tracer import HoneycombTracer, ensure_triage_board, initialize_tracing
from observability.ci.links import github_actions_url, honeycomb_investigation, source_url
from observability.ci.run_command import CommandResult, run_command
from observability.ci.redaction import redact_text
from observability.ci.run_context import RunContext, create_run_context, finalize_run_context
from observability.ci.sentry_reporter import SentryReporter
from observability.ci.storage import RunStorage


_TRANSIENT_COMMAND_MARKERS = (
    "tls handshake timeout",
    "gnutls_handshake() failed",
    "tls connection was non-properly terminated",
    "timeout was reached",
    "i/o timeout",
    "context deadline exceeded",
    "temporary failure in name resolution",
    "connection reset by peer",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "unexpected eof",
    "proxyconnect tcp",
    "too many requests",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Eve Trade checks with durable observability artifacts")
    parser.add_argument("command", choices=("check", "test", "integration", "e2e", "collect-only"))
    parser.add_argument("--clean", action="store_true", help="Accepted for compatibility; Encore E2E does not delete local resources")
    parser.add_argument("--maxfail", type=int, default=0)
    parser.add_argument("--test-path", default="")
    parser.add_argument("--no-sentry", action="store_true")
    parser.add_argument("--no-honeycomb", action="store_true")
    parser.add_argument("--strict", action="store_true", default=_truthy(os.getenv("OBSERVABILITY_STRICT", "")))
    parser.add_argument("--compare-to", type=Path)
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[int, RunContext]:
    context = create_run_context()
    os.environ.setdefault("OBSERVABILITY_RUN_ID", context.run_id)
    collector_output = context.run_dir / "telemetry" / "collector-live"
    collector_output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("OTEL_LOCAL_OUTPUT_DIR", collector_output.as_posix())
    storage = RunStorage(context.run_dir, strict=args.strict)
    tracer = initialize_tracing(context, enabled=not args.no_honeycomb, strict=args.strict)
    sentry = SentryReporter(context, enabled=not args.no_sentry, strict=args.strict)
    results: list[CommandResult] = []
    pytest_summary: PytestSummary | None = None
    db_metadata: dict[str, Any] = {}
    kubernetes_metadata: dict[str, Any] = {}
    missing_evidence: list[str] = []
    exit_code = 0
    final_status = "INCOMPLETE"
    diagnosis: dict[str, Any] = {}
    report_path = ""
    try:
        with tracer.span("pipeline.run", {"pipeline.command": args.command, "pipeline.stage": "run"}) as run_span:
            environment = collect_environment(context, storage)
            run_span.set_attribute("git.dirty", bool(environment["git"].get("dirty")))
            run_span.set_attribute("git.branch", environment["git"].get("branch", ""))
            run_span.set_attribute("db.migration_hash", environment["hashes"].get("db.migration_hash", ""))
            run_span.set_attribute("protobuf.generated_hash", environment["hashes"].get("protobuf.generated_hash", ""))
            if args.command == "check":
                results.extend(_run_check(context, storage, tracer, sentry))
            elif args.command == "test":
                results.extend(_run_tests(context, storage, tracer, sentry, args.test_path))
            elif args.command in ("integration", "e2e"):
                integration = _run_integration(context, storage, tracer, sentry, args)
                results.extend(integration["results"])
                pytest_summary = integration["pytest"]
                db_metadata = integration["database"]
                missing_evidence.extend(integration["missing"])
            elif args.command == "collect-only":
                db_metadata = _safe_collect("database", missing_evidence, args.strict, lambda: collect_db(context, storage))
                kubernetes_metadata = _safe_collect("kubernetes", missing_evidence, args.strict, lambda: collect_kubernetes(context, storage))
            failed = next((result for result in results if not result.succeeded), None)
            exit_code = failed.exit_code if failed else 0
            run_span.set_attribute("command.exit_code", exit_code)
            run_span.set_attribute("error", exit_code != 0)
            with tracer.span("pipeline.evidence", _evidence_attributes(db_metadata, kubernetes_metadata)):
                pass
            storage.write_json(
                "run-summary.json",
                {
                    "run_id": context.run_id,
                    "command": args.command,
                    "exit_code": exit_code,
                    "commands": [result.to_dict() for result in results],
                    "command_sequence": [result.name for result in results],
                    "service_readiness_ms": {
                        result.name: result.duration_ms
                        for result in results
                        if "readiness" in result.name or result.stage == "start"
                    },
                    "service_urls": _service_urls(environment["environment"]),
                    "missing_evidence": missing_evidence,
                },
            )
            diagnosis = diagnose_run(
                run_id=context.run_id,
                command=args.command,
                results=results,
                pytest_summary=pytest_summary,
                missing_evidence=missing_evidence,
                database=db_metadata,
                kubernetes=kubernetes_metadata,
            )
            storage.write_json("diagnosis.json", diagnosis)
            run_report = generate_run_report(context, diagnosis, storage=storage)
            report_path = run_report["markdown"].relative_to(context.run_dir).as_posix()
            if failed:
                pytest_summary = pytest_summary or collect_pytest(context, failed.stdout + "\n" + failed.stderr, storage=storage)
                classification = classification_from_diagnosis(diagnosis)
                _capture_enriched_sentry(sentry, context, failed, pytest_summary, classification)
                investigation = honeycomb_investigation(
                    context,
                    trace_id=failed.trace_id,
                    test_nodeid=pytest_summary.first_failing_test_nodeid,
                    service_name=classification.suspected_services[0] if classification.suspected_services else "",
                    failure_family=classification.failure_family,
                )
                with tracer.span(
                    "pipeline.failure",
                    {
                        "pipeline.command": failed.name,
                        "pipeline.stage": failed.stage,
                        "command.exit_code": failed.exit_code,
                        "command.duration_ms": failed.duration_ms,
                        "test.nodeid": pytest_summary.first_failing_test_nodeid,
                        "test.file": pytest_summary.source_file,
                        "test.line": pytest_summary.source_line or 0,
                        "test.failure_family": classification.failure_family,
                        "service.name": classification.suspected_services[0] if classification.suspected_services else "eve-trade-ci",
                        "artifact.path": failed.log_path,
                        "sentry.event_id": sentry.event_id,
                        "honeycomb.trace_url": investigation.get("trace_url", ""),
                        "source.url": pytest_summary.source_url,
                        "error": True,
                    },
                ):
                    pass
                generate_failure_report(
                    context,
                    failed,
                    pytest_summary,
                    classification,
                    database=db_metadata,
                    kubernetes=kubernetes_metadata,
                    sentry_event_id=sentry.event_id,
                    trace_id=failed.trace_id,
                    missing_evidence=missing_evidence,
                    diagnosis=diagnosis,
                    storage=storage,
                )
            if args.compare_to:
                compare_runs(context.run_dir, args.compare_to, context.run_dir)
            if not args.no_honeycomb:
                ensure_triage_board(context, strict=args.strict)
            sentry.configure_release_cli()
            sentry.run_optional_autofix_hook()
            final_status = "COMPLETE"
    except Exception:
        storage.write_text("observability-error.txt", redact_text(traceback.format_exc()))
        final_status = "ANALYSIS_FAILED"
        failed = next((result for result in results if not result.succeeded), None)
        if failed:
            exit_code = failed.exit_code
        elif args.strict or not results:
            exit_code = 2 if args.strict else 1
        else:
            exit_code = 0
        if args.strict:
            raise
    finally:
        try:
            final_provenance = finalize_run_context(
                context,
                status=final_status,
                command=args.command,
                exit_code=exit_code,
                commands_executed=[result.to_dict() for result in results],
                diagnosis_path="diagnosis.json" if diagnosis else "",
                report_path=report_path,
                storage=storage,
            )
            if diagnosis:
                generate_run_report(context, diagnosis, provenance=final_provenance, storage=storage)
        except Exception:
            if args.strict:
                raise
        sentry.flush()
        tracer.shutdown()
        if _truthy(os.getenv("OBS_COMPRESS_RUN", "")):
            try:
                bundle = storage.bundle()
                storage.upload_optional(bundle)
            except Exception:
                if args.strict:
                    raise
        print(context.run_dir)
    return exit_code, context


def _run_check(context: RunContext, storage: RunStorage, tracer: HoneycombTracer, sentry: SentryReporter) -> list[CommandResult]:
    return [
        run_command(context, [sys.executable, "-m", "compileall", "-q", "distributed-backend/observability"], name="compile-observability", stage="check", storage=storage, tracer=tracer, sentry=sentry),
        run_command(context, ["git", "diff", "--check"], name="git-diff-check", stage="check", storage=storage, tracer=tracer, sentry=sentry),
    ]


def _run_tests(context: RunContext, storage: RunStorage, tracer: HoneycombTracer, sentry: SentryReporter, test_path: str) -> list[CommandResult]:
    argv = [sys.executable, "-m", "unittest", "discover", "-s", test_path or "distributed-backend/observability/tests", "-v"]
    return [
        run_command(
            context,
            argv,
            name="observability-unit-tests",
            stage="test",
            storage=storage,
            tracer=tracer,
            sentry=sentry,
            env=_pythonpath_env(context),
            report_failure_to_sentry=False,
        )
    ]


def _run_integration(
    context: RunContext,
    storage: RunStorage,
    tracer: HoneycombTracer,
    sentry: SentryReporter,
    args: argparse.Namespace,
) -> dict[str, Any]:
    missing: list[str] = []
    if args.clean:
        missing.append("--clean was ignored; Encore E2E uses the configured runtime environment")
    for name in ("EVE_TRADE_ENCORE_URL", "EVE_TRADE_SIMULATOR_URL", "EVE_TRADE_DATABASE_URL"):
        if not os.getenv(name):
            missing.append(f"{name} is not set; pytest will skip or fail according to the production gate")
    return _host_e2e(context, storage, tracer, sentry, args, missing)


def _host_e2e(
    context: RunContext, storage: RunStorage, tracer: HoneycombTracer, sentry: SentryReporter,
    args: argparse.Namespace, missing: list[str],
) -> dict[str, Any]:
    junit = storage.path("pytest/pytest-junit.xml")
    test_path = args.test_path or "distributed-backend/tests/e2e"
    argv = [sys.executable, "-m", "pytest", test_path, "-vv", "-s", "--tb=short", f"--junitxml={junit}"]
    if args.maxfail:
        argv.append(f"--maxfail={args.maxfail}")
    result = run_command(context, argv, name="pytest-e2e-host", stage="e2e", storage=storage, tracer=tracer, sentry=sentry, env=_pythonpath_env(context), timeout=1800, report_failure_to_sentry=False)
    summary = collect_pytest(context, result.stdout + "\n" + result.stderr, junit_path=junit, storage=storage)
    return {"results": [result], "pytest": summary, "database": _safe_collect("database", missing, False, lambda: collect_db(context, storage)), "missing": missing}


def _pythonpath_env(context: RunContext) -> dict[str, str]:
    package_root = str((context.repo_root / "distributed-backend").resolve())
    current = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(part for part in (package_root, current) if part)}


def _is_transient_command_failure(result: CommandResult) -> bool:
    if result.succeeded:
        return False
    if result.timed_out:
        return True
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in output for marker in _TRANSIENT_COMMAND_MARKERS)


def _capture_enriched_sentry(
    sentry: SentryReporter,
    context: RunContext,
    failed: CommandResult,
    pytest: PytestSummary,
    classification: FailureClassification,
) -> None:
    investigation = honeycomb_investigation(context, trace_id=failed.trace_id, test_nodeid=pytest.first_failing_test_nodeid, failure_family=classification.failure_family)
    source_links = [source_url(context, item) for item in classification.likely_solution_files]
    sentry.capture_command_failure(
        command_name=failed.name,
        argv=failed.argv,
        exit_code=failed.exit_code,
        stage=failed.stage,
        artifact_path=failed.log_path,
        failure_family=classification.failure_family,
        failed_test_nodeid=pytest.first_failing_test_nodeid,
        source_links=source_links,
        honeycomb_trace_url=investigation.get("trace_url", ""),
        github_actions_url=github_actions_url(context),
    )


def _safe_collect(name: str, missing: list[str], strict: bool, operation: Any) -> dict[str, Any]:
    try:
        result = operation()
        if isinstance(result, dict) and result.get("error"):
            missing.append(f"{name}: {result['error']}")
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        missing.append(f"{name} collector failed: {type(exc).__name__}: {exc}")
        if strict:
            raise
        return {}


def _read_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _service_urls(environment: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in environment.items()
        if (key.endswith("_URL") or key.endswith("_ENDPOINT")) and isinstance(value, str)
    }


def _evidence_attributes(
    database: dict[str, Any],
    kubernetes: dict[str, Any],
) -> dict[str, Any]:
    pods = kubernetes.get("containers", []) if isinstance(kubernetes, dict) else []
    attributes = {
        "db.schema_hash": database.get("db.schema_hash", "") if isinstance(database, dict) else "",
        "kubernetes.namespace": kubernetes.get("namespace", "") if isinstance(kubernetes, dict) else "",
        "kubernetes.pod.name": [str(item.get("kubernetes.pod.name", "")) for item in pods if item.get("kubernetes.pod.name")],
        "kubernetes.container.name": [str(item.get("kubernetes.container.name", "")) for item in pods if item.get("kubernetes.container.name")],
    }
    return {key: value for key, value in attributes.items() if value not in (None, "", [])}


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    args = parse_args()
    exit_code, _ = execute(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
