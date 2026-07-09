"""Evidence-first diagnosis and causal event modeling for observed runs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .collect_pytest import PytestSummary
from .provenance import RUNNER_VERSION
from .run_command import CommandResult


DIAGNOSIS_SCHEMA_VERSION = "o11y.structured-diagnosis.v1"
CLASSIFIER_VERSION = "o11y-causal-classifier-2026-07-09"

CONFIRMED = "CONFIRMED"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
UNKNOWN = "UNKNOWN"


def diagnose_run(
    *,
    run_id: str,
    command: str,
    results: list[CommandResult],
    pytest_summary: PytestSummary | None = None,
    missing_evidence: list[str] | None = None,
    database: dict[str, Any] | None = None,
    kubernetes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_evidence = list(missing_evidence or [])
    pytest_summary = pytest_summary or PytestSummary()
    failed = next((result for result in results if not result.succeeded), None)
    diagnosis = _base_diagnosis(run_id, command, results, pytest_summary, missing_evidence)
    if failed:
        failure = diagnose_command_failure(
            failed,
            pytest_summary=pytest_summary,
            additional_logs=_metadata_hints(database, kubernetes, missing_evidence),
            downstream_results=results[results.index(failed) + 1 :],
        )
        failure["test_execution"] = _test_truth(command, results, pytest_summary, missing_evidence)
        diagnosis.update(failure)
        diagnosis["validation_result"] = "failed"
        return diagnosis

    test_truth = _test_truth(command, results, pytest_summary, missing_evidence)
    diagnosis["test_execution"] = test_truth
    false_green_risks = _false_green_risks(command, results, pytest_summary, missing_evidence)
    diagnosis["false_green_risks"] = false_green_risks
    if false_green_risks:
        diagnosis["validation_result"] = "passed_with_false_green_risk"
        diagnosis["product_status"] = "UNRESOLVED"
        diagnosis["harness_status"] = "BROKEN"
        diagnosis["primary_diagnosis"] = _primary(
            summary="Validation command exited successfully, but required validation evidence is absent or skipped.",
            stage="CI_HARNESS",
            mechanism="FALSE_GREEN_RISK",
            component="observability-runner",
            external_system="",
            confidence_score=0.9,
            confidence_band=HIGH,
            supporting=[risk["evidence"] for risk in false_green_risks],
            contradicting=[],
            missing=["Fresh product correctness evidence from the expected suite."],
            unsupported=[],
        )
        diagnosis["recommendations"] = [
            _recommendation(
                "Run the required suite with its service URLs and credentials configured.",
                "The command returned success without evidence that product assertions executed.",
                "Collected tests with started assertions and nonzero pass/fail counts would reject the false-green hypothesis.",
            )
        ]
    else:
        diagnosis["validation_result"] = "passed"
        diagnosis["product_status"] = _product_status_from_tests(test_truth)
        diagnosis["harness_status"] = "OK"
        diagnosis["primary_diagnosis"] = _primary(
            summary="No failing command was observed.",
            stage="UNKNOWN",
            mechanism="NONE_OBSERVED",
            component="",
            external_system="",
            confidence_score=1.0,
            confidence_band=CONFIRMED,
            supporting=["All executed commands exited with code 0."],
            contradicting=[],
            missing=[],
            unsupported=[],
        )
    return diagnosis


def diagnose_command_failure(
    command: CommandResult,
    *,
    pytest_summary: PytestSummary | None = None,
    additional_logs: str = "",
    downstream_results: list[CommandResult] | None = None,
) -> dict[str, Any]:
    pytest_summary = pytest_summary or PytestSummary()
    downstream_results = downstream_results or []
    output = "\n".join((command.stdout, command.stderr, additional_logs))
    lower = output.lower()
    observations = [
        _observation(
            "O1",
            f"Command {command.name} exited with code {command.exit_code}.",
            command.log_path,
            CONFIRMED,
        ),
        _observation("O2", f"Declared command stage was {command.stage}.", command.metadata_path, CONFIRMED),
    ]
    derived: list[dict[str, Any]] = []
    inferences: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    false_red_risks: list[dict[str, Any]] = []
    unsupported: list[str] = []
    events: list[dict[str, Any]] = []
    test_execution = _test_truth(command.stage, [command], pytest_summary, [])

    if _is_stale_path_failure(output):
        path = _extract_stale_path(output)
        observations.append(_observation("O3", f"Output referenced missing path {path or 'unknown path'}.", command.log_path, CONFIRMED))
        derived.append(_derived("D1", "The failure happened before product validation could execute.", ["O1", "O3"]))
        events = _events(command, "CI_HARNESS", "python", "STALE_PATH", f"stale path {path or ''}".strip())
        primary = _primary(
            summary="Automation referenced a stale or missing repository path.",
            stage="CI_HARNESS",
            mechanism="STALE_PATH",
            component="ci-harness",
            external_system="",
            confidence_score=0.95,
            confidence_band=HIGH,
            supporting=[observations[-1]["summary"], "The command failed before test execution evidence was present."],
            contradicting=[],
            missing=["A successful run of the intended product validation command."],
            unsupported=["Python application bug", "E2E product regression", "Docker networking"],
        )
        recommendations.append(
            _recommendation(
                "Update the automation to call the current distributed-backend/observability path.",
                "The evidence is a missing script path in the harness, not a product assertion failure.",
                "The same command should start the intended validation stage instead of exiting with ENOENT.",
            )
        )
        return _failure_payload(
            observations,
            derived,
            inferences,
            recommendations,
            events,
            primary,
            "BROKEN",
            "NOT_TESTED",
            test_execution,
            false_red_risks,
        )

    if _has_docker_daemon_failure(lower):
        observations.append(_observation("O3", "Docker emitted a daemon connectivity error.", command.log_path, CONFIRMED))
        events = _events(command, "CONTAINER_RUNTIME", "docker", "DOCKER_DAEMON_UNAVAILABLE", "Docker daemon unavailable")
        primary = _primary(
            summary="Docker daemon was unavailable to the validation runner.",
            stage="CONTAINER_RUNTIME",
            mechanism="CONTAINER_RUNTIME",
            component="docker",
            external_system="docker-daemon",
            confidence_score=0.96,
            confidence_band=HIGH,
            supporting=["Direct Docker daemon error was present in command output."],
            contradicting=[],
            missing=["Docker daemon status and service logs from the runner host."],
            unsupported=["Application E2E bug", "Database schema drift"],
        )
        recommendations.append(
            _recommendation(
                "Start or repair the Docker daemon on the runner and retry the same command.",
                "The failed component was Docker daemon access.",
                "A successful docker version or docker info call would reject this blocker.",
            )
        )
        return _failure_payload(observations, derived, inferences, recommendations, events, primary, "BROKEN", "UNRESOLVED", test_execution, false_red_risks)

    if _has_go_mod_unexpected_eof(lower):
        endpoint = _first_match(r"https://([^/\"'\s]+)/", output) or "proxy.golang.org"
        observations.extend(
            [
                _observation("O3", "Output contained a go mod download step.", command.log_path, CONFIRMED),
                _observation("O4", "Output contained unexpected EOF during module fetch.", command.log_path, CONFIRMED),
                _observation("O5", f"The remote endpoint was {endpoint}.", command.log_path, CONFIRMED),
            ]
        )
        derived.append(_derived("D1", "The earliest supported failing stage is dependency resolution.", ["O3", "O4", "O5"]))
        inferences.append(
            _inference(
                "I1",
                "The transfer or registry fetch path terminated early; the exact network origin is unresolved.",
                ["O3", "O4", "O5"],
                HIGH,
                0.82,
            )
        )
        unsupported = ["docker-networking", "database", "application E2E bug", "Kubernetes"]
        false_red_risks.append(
            {
                "risk": "FALSE_RED_DEPENDENCY_FETCH",
                "evidence": "The failing low-level operation was a dependency download from an external module proxy.",
            }
        )
        events = _events(command, "DEPENDENCY_RESOLUTION", "go", "NETWORK_TRANSPORT", f"go module fetch unexpected EOF from {endpoint}")
        if downstream_results or "canceled" in lower or "skipped" in lower:
            events.append(
                _event(
                    "E3",
                    command.ended_at,
                    command.stage,
                    command.name,
                    "pipeline",
                    "DOWNSTREAM_BLOCKED",
                    "warning",
                    "Later validation work was canceled, skipped, or blocked after dependency resolution failed.",
                    command.log_path,
                    ["E1"],
                    "DOWNSTREAM_CONSEQUENCE",
                    0.8,
                )
            )
        primary = _primary(
            summary="Dependency transfer failed while fetching Go modules; exact transport origin is unresolved.",
            stage="DEPENDENCY_RESOLUTION",
            mechanism="NETWORK_TRANSPORT",
            component="go",
            external_system=endpoint,
            confidence_score=0.82,
            confidence_band=HIGH,
            supporting=[item["summary"] for item in observations[-3:]],
            contradicting=[
                "Docker daemon failure was not the emitted low-level error.",
                "No database, Kubernetes, or application assertion evidence preceded the module fetch failure.",
            ],
            missing=[
                "Repeat result for the same go mod download outside Docker.",
                "Runner connectivity evidence for GOPROXY/direct module fetch.",
            ],
            unsupported=unsupported,
        )
        recommendations.extend(
            [
                _recommendation(
                    "Retry the exact module fetch and compare repeatability across runners.",
                    "Unexpected EOF is a transfer interruption observation, not a Docker-networking observation.",
                    "A repeatable EOF on multiple runners points toward registry/proxy behavior; a local-only repeat points toward runner network path.",
                ),
                _recommendation(
                    "Inspect GOPROXY behavior and reachability for proxy.golang.org before investigating Docker networking.",
                    "The contacted resource in evidence was the Go module proxy.",
                    "Successful proxy and direct fetches would reject dependency-transfer as the active blocker.",
                ),
            ]
        )
        return _failure_payload(observations, derived, inferences, recommendations, events, primary, "OK", "UNRESOLVED", test_execution, false_red_risks)

    if _has_database_refusal(lower):
        endpoint = _first_match(r"([a-zA-Z0-9_.-]+:5432)", output) or "postgres:5432"
        observations.append(_observation("O3", f"Output contained PostgreSQL TCP connection refusal for {endpoint}.", command.log_path, CONFIRMED))
        events = _events(command, "DATABASE", "postgres", "TCP_REFUSED", f"PostgreSQL refused connection at {endpoint}")
        primary = _primary(
            summary="Database connectivity was refused by the PostgreSQL endpoint.",
            stage="DATABASE",
            mechanism="NETWORK_TRANSPORT",
            component="postgres",
            external_system=endpoint,
            confidence_score=0.88,
            confidence_band=HIGH,
            supporting=[observations[-1]["summary"], "The command stage was a database readiness or integration step."],
            contradicting=["No Docker daemon-specific error was observed before the database refusal."],
            missing=["PostgreSQL process/listener status at the target host and port."],
            unsupported=["docker-networking"],
        )
        recommendations.append(
            _recommendation(
                "Check PostgreSQL readiness and the configured database URL target.",
                "The direct resource in evidence was a PostgreSQL endpoint refusing TCP connections.",
                "A listening PostgreSQL socket and successful readiness query would reject this blocker.",
            )
        )
        return _failure_payload(observations, derived, inferences, recommendations, events, primary, "OK", "UNRESOLVED", test_execution, false_red_risks)

    network = _network_mechanism(lower)
    if network:
        mechanism, message = network
        observations.append(_observation("O3", f"Output contained {message}.", command.log_path, CONFIRMED))
        events = _events(command, "NETWORK_TRANSPORT" if mechanism != "DNS" else "DNS", "network", mechanism, message)
        primary = _primary(
            summary=f"Network transport failure observed: {message}.",
            stage="NETWORK_TRANSPORT" if mechanism != "DNS" else "DNS",
            mechanism=mechanism,
            component="network",
            external_system=_first_match(r"https?://([^/\"'\s]+)", output) or "",
            confidence_score=0.74,
            confidence_band=MEDIUM,
            supporting=[observations[-1]["summary"]],
            contradicting=["No product assertion evidence preceded the transport failure."],
            missing=["Endpoint-specific connectivity checks."],
            unsupported=["application E2E bug"],
        )
        recommendations.append(
            _recommendation(
                "Collect endpoint-specific connectivity evidence for the failing resource.",
                "The current evidence identifies the mechanism but not the unique origin.",
                "DNS lookup, TCP connect, and retry evidence would separate local, proxy, and remote causes.",
            )
        )
        return _failure_payload(observations, derived, inferences, recommendations, events, primary, "OK", "UNRESOLVED", test_execution, false_red_risks)

    if _has_assertion_failure(pytest_summary, output):
        nodeid = pytest_summary.first_failing_test_nodeid or "unknown test"
        observations.append(_observation("O3", f"Test assertion failure was extracted for {nodeid}.", command.log_path, CONFIRMED))
        events = _events(command, _stage_taxonomy(command.stage), "test", "ASSERTION_FAILURE", nodeid)
        primary = _primary(
            summary="A product or test assertion failed after the test command executed.",
            stage=_stage_taxonomy(command.stage),
            mechanism="APPLICATION_TEST_FAILURE",
            component="test",
            external_system="",
            confidence_score=0.87,
            confidence_band=HIGH,
            supporting=[observations[-1]["summary"], pytest_summary.failure_message or pytest_summary.assertion_text],
            contradicting=["No earlier infrastructure blocker was observed in the command evidence."],
            missing=["Service logs and state snapshots around the assertion time."],
            unsupported=["infrastructure failure"],
        )
        recommendations.append(
            _recommendation(
                "Debug the named assertion with the captured command log and service state.",
                "The earliest extracted failure is a test assertion, not an environment setup blocker.",
                "Reproducing the assertion with healthy dependencies confirms a product/test behavior failure.",
            )
        )
        return _failure_payload(observations, derived, inferences, recommendations, events, primary, "OK", "FAILED", test_execution, false_red_risks)

    inferences.append(
        _inference(
            "I1",
            "The root cause is not uniquely identifiable from the available command evidence.",
            ["O1", "O2"],
            UNKNOWN,
            0.2,
        )
    )
    events = _events(command, _stage_taxonomy(command.stage), "unknown", "UNKNOWN", "unclassified command failure")
    primary = _primary(
        summary="Root cause unknown from available evidence.",
        stage=_stage_taxonomy(command.stage),
        mechanism="UNKNOWN",
        component="unknown",
        external_system="",
        confidence_score=0.2,
        confidence_band=UNKNOWN,
        supporting=["A command failed and raw logs were preserved."],
        contradicting=[],
        missing=["A tool-specific structured error or narrower failure signature."],
        unsupported=[],
    )
    recommendations.append(
        _recommendation(
            "Inspect the preserved command log and add a fixture if this failure pattern is recurring.",
            "The classifier intentionally abstained instead of guessing from weak evidence.",
            "A structured fixture with command, stage, and expected category would make the diagnosis reproducible.",
        )
    )
    payload = _failure_payload(observations, derived, inferences, recommendations, events, primary, "UNKNOWN", "UNRESOLVED", test_execution, false_red_risks)
    payload["abstained"] = True
    return payload


def _base_diagnosis(
    run_id: str,
    command: str,
    results: list[CommandResult],
    pytest_summary: PytestSummary,
    missing_evidence: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "requested_command": command,
        "validation_result": "UNKNOWN",
        "product_status": "UNRESOLVED",
        "harness_status": "UNKNOWN",
        "commands": [result.to_dict() for result in results],
        "test_execution": _test_truth(command, results, pytest_summary, missing_evidence),
        "observations": [],
        "derived_facts": [],
        "inferences": [],
        "recommendations": [],
        "events": [],
        "causal_chain": [],
        "earliest_causal_failure": None,
        "primary_diagnosis": {},
        "false_green_risks": [],
        "false_red_risks": [],
        "missing_evidence": missing_evidence,
        "abstained": False,
    }


def _failure_payload(
    observations: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    inferences: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    events: list[dict[str, Any]],
    primary: dict[str, Any],
    harness_status: str,
    product_status: str,
    test_execution: dict[str, Any],
    false_red_risks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "product_status": product_status,
        "harness_status": harness_status,
        "test_execution": test_execution,
        "observations": observations,
        "derived_facts": derived,
        "inferences": inferences,
        "recommendations": recommendations,
        "events": events,
        "causal_chain": [event["event_id"] for event in events],
        "earliest_causal_failure": events[0] if events else None,
        "primary_diagnosis": primary,
        "false_red_risks": false_red_risks,
        "abstained": False,
    }


def _test_truth(command: str, results: list[CommandResult], pytest_summary: PytestSummary, missing_evidence: list[str]) -> dict[str, Any]:
    category = "E2E_TEST" if command in {"integration", "e2e"} or any(result.stage == "e2e" for result in results) else "UNIT_TEST" if command == "test" else "UNKNOWN"
    status = "NOT_SCHEDULED"
    unittest_count = _unittest_count(results)
    collected = pytest_summary.collected_count or unittest_count
    started = pytest_summary.passed_count + pytest_summary.failed_count + pytest_summary.error_count
    if unittest_count and not started:
        started = unittest_count
    passed = pytest_summary.passed_count
    if unittest_count and not pytest_summary.failed_count and not pytest_summary.error_count and all(result.succeeded for result in results):
        passed = unittest_count
    failed = pytest_summary.failed_count + pytest_summary.error_count
    if category != "UNKNOWN":
        status = "STARTED" if any(result.stage in {"e2e", "test"} for result in results) else "NOT_SCHEDULED"
        if status == "NOT_SCHEDULED" and command in {"integration", "e2e", "test"} and any(not result.succeeded for result in results):
            status = "BLOCKED"
        if any(result.stage in {"e2e", "test"} and not result.succeeded for result in results):
            status = "FAILED"
        elif pytest_summary.collected_count and pytest_summary.skipped_count == pytest_summary.collected_count:
            status = "SKIPPED"
        elif any("not set" in item.lower() for item in missing_evidence):
            status = "BLOCKED" if status == "NOT_SCHEDULED" else "SKIPPED"
        elif failed:
            status = "FAILED"
        elif passed:
            status = "PASSED"
        elif status == "STARTED" and collected == 0:
            status = "UNKNOWN"
    return {
        category: {
            "status": status,
            "tests_collected": collected,
            "tests_started": started,
            "tests_passed": passed,
            "tests_failed": failed,
            "tests_skipped": pytest_summary.skipped_count,
        }
    }


def _false_green_risks(command: str, results: list[CommandResult], pytest_summary: PytestSummary, missing_evidence: list[str]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    test_started = any(result.stage in {"e2e", "test"} for result in results)
    explicit_zero_pytest = any("collected 0" in f"{result.stdout}\n{result.stderr}".lower() for result in results)
    explicit_zero_unittest = any(re.search(r"(?m)^Ran\s+0\s+tests\b", f"{result.stdout}\n{result.stderr}") for result in results)
    if command in {"integration", "e2e"} and test_started and pytest_summary.collected_count == 0:
        risks.append({"risk": "ZERO_TESTS_COLLECTED", "evidence": "Expected validation suite started but pytest collected 0 tests."})
    if command == "test" and test_started and (explicit_zero_pytest or explicit_zero_unittest):
        risks.append({"risk": "ZERO_TESTS_COLLECTED", "evidence": "Expected validation suite started but reported 0 tests."})
    if command in {"integration", "e2e"} and pytest_summary.collected_count and pytest_summary.skipped_count == pytest_summary.collected_count:
        risks.append({"risk": "ALL_E2E_TESTS_SKIPPED", "evidence": "E2E pytest collected tests but all collected tests were skipped."})
    if command in {"integration", "e2e"} and any("not set" in item.lower() for item in missing_evidence):
        risks.append({"risk": "MISSING_E2E_ENVIRONMENT", "evidence": "Required E2E environment variables were not set."})
    return risks


def _unittest_count(results: list[CommandResult]) -> int:
    for result in results:
        match = re.search(r"(?m)^Ran\s+(\d+)\s+tests?\b", f"{result.stdout}\n{result.stderr}")
        if match:
            return int(match.group(1))
    return 0


def _product_status_from_tests(test_truth: dict[str, Any]) -> str:
    for item in test_truth.values():
        if item["status"] == "FAILED":
            return "FAILED"
        if item["status"] in {"SKIPPED", "BLOCKED", "UNKNOWN", "NOT_SCHEDULED"}:
            return "UNRESOLVED"
        if item["status"] == "PASSED" and item["tests_passed"]:
            return "PASSED"
    return "UNRESOLVED"


def _primary(
    *,
    summary: str,
    stage: str,
    mechanism: str,
    component: str,
    external_system: str,
    confidence_score: float,
    confidence_band: str,
    supporting: list[str],
    contradicting: list[str],
    missing: list[str],
    unsupported: list[str],
) -> dict[str, Any]:
    return {
        "summary": summary,
        "category_dimensions": {
            "stage": stage,
            "mechanism": mechanism,
            "component": component,
            "external_system": external_system,
        },
        "confidence_score": confidence_score,
        "confidence_band": confidence_band,
        "supporting_evidence": [item for item in supporting if item],
        "contradicting_evidence": [item for item in contradicting if item],
        "missing_evidence": [item for item in missing if item],
        "unsupported_diagnoses": unsupported,
    }


def _events(command: CommandResult, stage: str, component: str, event_type: str, message: str) -> list[dict[str, Any]]:
    return [
        _event(
            "E1",
            command.started_at,
            stage,
            command.name,
            component,
            event_type,
            "error",
            message,
            command.log_path,
            [],
            "ROOT_CAUSE",
            0.86,
        ),
        _event(
            "E2",
            command.ended_at,
            command.stage,
            command.name,
            "process",
            "COMMAND_EXIT_NONZERO",
            "error",
            f"{command.name} exited {command.exit_code}",
            command.metadata_path,
            ["E1"],
            "SYMPTOM",
            1.0,
        ),
    ]


def _event(
    event_id: str,
    timestamp: str,
    stage: str,
    command_id: str,
    component: str,
    event_type: str,
    severity: str,
    message: str,
    evidence_reference: str,
    caused_by: list[str],
    relation: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "stage": stage,
        "command_id": command_id,
        "process_id_if_available": "",
        "component": component,
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "source_file_or_log": evidence_reference,
        "evidence_reference": evidence_reference,
        "caused_by": caused_by,
        "relation": relation,
        "confidence": confidence,
    }


def _observation(event_id: str, summary: str, evidence_reference: str, confidence_band: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "kind": "OBSERVATION",
        "summary": summary,
        "evidence_reference": evidence_reference,
        "confidence_band": confidence_band,
    }


def _derived(event_id: str, summary: str, based_on: list[str]) -> dict[str, Any]:
    return {"id": event_id, "kind": "DERIVED_FACT", "summary": summary, "based_on": based_on, "confidence_band": CONFIRMED}


def _inference(event_id: str, summary: str, based_on: list[str], band: str, score: float) -> dict[str, Any]:
    return {"id": event_id, "kind": "INFERENCE", "summary": summary, "based_on": based_on, "confidence_band": band, "confidence_score": score}


def _recommendation(action: str, rationale: str, would_confirm_or_reject: str) -> dict[str, str]:
    return {
        "kind": "RECOMMENDATION",
        "action": action,
        "rationale": rationale,
        "would_confirm_or_reject": would_confirm_or_reject,
    }


def _metadata_hints(database: dict[str, Any] | None, kubernetes: dict[str, Any] | None, missing_evidence: Iterable[str]) -> str:
    return "\n".join([str(database or {}), str(kubernetes or {}), *[str(item) for item in missing_evidence]])


def _stage_taxonomy(stage: str) -> str:
    return {
        "check": "STATIC_ANALYSIS",
        "test": "UNIT_TEST",
        "e2e": "E2E_TEST",
        "integration": "INTEGRATION_TEST",
        "build": "CONTAINER_BUILD",
        "start": "CONTAINER_RUNTIME",
        "readiness": "CONTAINER_RUNTIME",
        "migrate": "DATABASE",
    }.get(stage, "UNKNOWN")


def _has_go_mod_unexpected_eof(lower: str) -> bool:
    return "go mod download" in lower and "unexpected eof" in lower


def _has_docker_daemon_failure(lower: str) -> bool:
    return "cannot connect to the docker daemon" in lower or "is the docker daemon running" in lower


def _has_database_refusal(lower: str) -> bool:
    return "connection refused" in lower and ("postgres" in lower or ":5432" in lower or "database_url" in lower)


def _network_mechanism(lower: str) -> tuple[str, str] | None:
    if "no such host" in lower or "temporary failure in name resolution" in lower or "could not resolve host" in lower:
        return "DNS", "DNS resolution failure"
    if "connection refused" in lower:
        return "TCP_REFUSED", "TCP connection refused"
    if "i/o timeout" in lower or "context deadline exceeded" in lower or "timed out" in lower or "timeout was reached" in lower:
        return "TIMEOUT", "network timeout"
    if "connection reset by peer" in lower:
        return "NETWORK_TRANSPORT", "connection reset by peer"
    return None


def _has_assertion_failure(pytest_summary: PytestSummary, output: str) -> bool:
    return bool(
        pytest_summary.first_failing_test_nodeid
        or pytest_summary.failed_count
        or pytest_summary.error_count
        or "assertionerror" in output.lower()
        or re.search(r"(?m)^\s*E\s+assert\s+", output)
    )


def _is_stale_path_failure(output: str) -> bool:
    lower = output.lower()
    if not any(marker in lower for marker in ("no such file or directory", "can't open file", "cannot find the path", "enoent")):
        return False
    return bool(re.search(r"(?i)(?:^|[\\/\s])observability[\\/][^\s:'\"]+", output))


def _extract_stale_path(output: str) -> str:
    match = re.search(r"(?i)([A-Za-z]:)?[^\s:'\"]*observability[\\/][^\s:'\"]+", output)
    return match.group(0) if match else ""


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else ""


def command_result_from_dict(value: dict[str, Any], *, stdout: str = "", stderr: str = "") -> CommandResult:
    fields = {field.name for field in CommandResult.__dataclass_fields__.values()}
    kwargs = {key: value[key] for key in fields if key in value}
    kwargs.setdefault("name", str(value.get("name", "fixture-command")))
    kwargs.setdefault("stage", str(value.get("stage", "test")))
    kwargs.setdefault("argv", [str(item) for item in value.get("argv", [])])
    kwargs.setdefault("exit_code", int(value.get("exit_code", 1)))
    kwargs.setdefault("started_at", str(value.get("started_at", "2026-01-01T00:00:00+00:00")))
    kwargs.setdefault("ended_at", str(value.get("ended_at", "2026-01-01T00:00:01+00:00")))
    kwargs.setdefault("duration_ms", float(value.get("duration_ms", 1000.0)))
    kwargs.setdefault("stdout", stdout)
    kwargs.setdefault("stderr", stderr)
    kwargs.setdefault("metadata_path", str(value.get("metadata_path", "commands/test/fixture-command/command.json")))
    kwargs.setdefault("log_path", str(value.get("log_path", "commands/test/fixture-command/command.log")))
    kwargs.setdefault("trace_id", str(value.get("trace_id", "")))
    kwargs.setdefault("timed_out", bool(value.get("timed_out", False)))
    return CommandResult(**kwargs)


def pytest_summary_from_dict(value: dict[str, Any] | None) -> PytestSummary:
    value = value or {}
    fields = {field.name for field in PytestSummary.__dataclass_fields__.values()}
    return PytestSummary(**{key: value[key] for key in fields if key in value})


def diagnosis_brief(diagnosis: dict[str, Any]) -> str:
    primary = diagnosis.get("primary_diagnosis", {})
    dims = primary.get("category_dimensions", {})
    return " / ".join(
        item
        for item in (
            str(dims.get("stage", "")),
            str(dims.get("mechanism", "")),
            str(dims.get("external_system", "")),
        )
        if item
    )
