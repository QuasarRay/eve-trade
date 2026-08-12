#!/usr/bin/env python3
"""Execute a registered scenario and its explicit business tests in a real deployment.

This module is invoked by Dagger after the deployment exists.  It prepares a
run/scenario-bound safety marker, installs only the required Litmus primitive,
runs the AnySystem-controlled action protocol, then lets pytest own business
pass/fail decisions over the facts-only evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from install_litmus import HELM_RELEASE, install


INFRA_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = INFRA_ROOT.parent
SCENARIO_ROOT = INFRA_ROOT / "emulated-scenarios"
RUNTIME_ROOT = SCENARIO_ROOT / "runtime"
E2E_ROOT = PROPERTY_ROOT / "e2e"
REPO_ROOT = PROPERTY_ROOT.parents[2]
RUN_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class OrchestrationError(RuntimeError):
    pass


def command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 600,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and process.returncode != 0:
        raise OrchestrationError(
            f"bounded command failed ({process.returncode}): {argv[0]}\n"
            f"stdout={process.stdout[-12000:]}\nstderr={process.stderr[-12000:]}"
        )
    return process


def kubectl_json(*args: str) -> dict[str, Any]:
    value = json.loads(command(["kubectl", *args, "-o", "json"]).stdout)
    if not isinstance(value, dict):
        raise OrchestrationError(f"kubectl returned non-object JSON for {args!r}")
    return value


def load_invocation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OrchestrationError(f"invalid invocation {path}: {error}") from error
    if not isinstance(value, dict):
        raise OrchestrationError("scenario invocation must be a JSON object")
    return value


def prepare_safety(invocation: dict[str, Any], mode: str, confirmation: str | None) -> None:
    namespace = str(invocation["namespace"])
    run_id = str(invocation["run_id"])
    scenario_id = str(invocation["scenario_id"])
    context = command(["kubectl", "config", "current-context"]).stdout.strip()
    if not context or any(token in context.lower() for token in ("prod", "production", "staging", "stage", "live")):
        raise OrchestrationError(f"refusing protected or empty Kubernetes context {context!r}")
    if mode == "kind":
        if not context.startswith("kind-"):
            raise OrchestrationError(f"kind mode requires a kind-* context, got {context!r}")
    elif confirmation != context:
        raise OrchestrationError("supplied mode requires exact disposable-context confirmation")
    namespace_resource = kubectl_json("get", "namespace", namespace)
    existing_labels = namespace_resource.get("metadata", {}).get("labels", {})
    if mode == "supplied" and existing_labels.get("eve-trade.io/chaos-safe") != "true":
        raise OrchestrationError("supplied namespace is not explicitly labelled chaos-safe")
    command(
        [
            "kubectl", "label", "namespace", namespace,
            "eve-trade.io/chaos-safe=true", f"eve-trade.io/run-id={run_id}",
            f"eve-trade.io/scenario-id={scenario_id[:63]}", "--overwrite",
        ]
    )
    marker = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "eve-trade-chaos-safety",
            "namespace": namespace,
            "labels": {
                "eve-trade.io/run-id": run_id,
                "eve-trade.io/scenario-id": scenario_id[:63],
                "eve-trade.io/managed-by": "deterministic-emulation",
            },
        },
        "data": {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "allow_litmus": "true",
            "disposable_context": context,
        },
    }
    command(
        ["kubectl", "-n", namespace, "apply", "-f", "-"],
        input_text=yaml.safe_dump(marker, sort_keys=False),
    )


def wait_for_registered_components(invocation: dict[str, Any]) -> None:
    scenario = json.loads(
        (SCENARIO_ROOT / invocation["scenario_id"] / "scenario.json").read_text(encoding="utf-8")
    )
    namespace = invocation["namespace"]
    physical_resources = {
        "encore-backend": "deployment/encore-backend",
        "market": "deployment/encore-backend",
        "settlement-worker": "deployment/encore-backend",
        "trade-settlement": "deployment/trade-settlement",
        "postgres": "deployment/postgres",
        "nsqd": "statefulset/nsqd",
        "quilkin": "deployment/quilkin",
        "simulator": "deployment/simulator",
        "otel-collector": "deployment/otel-collector",
    }
    resources = []
    for component in scenario["deployed_components"]:
        try:
            resource = physical_resources[component]
        except KeyError as error:
            raise OrchestrationError(f"scenario declares no Kubernetes readiness mapping for {component}") from error
        if resource not in resources:
            resources.append(resource)
    for resource in resources:
        command(
            ["kubectl", "-n", namespace, "rollout", "status", resource, "--timeout=300s"],
            timeout=320,
        )


def cleanup_litmus(namespace: str, run_id: str) -> list[str]:
    errors: list[str] = []
    commands = [
        ["helm", "uninstall", HELM_RELEASE, "--namespace", namespace, "--ignore-not-found", "--wait", "--timeout", "120s"],
        [
            "kubectl", "-n", namespace, "delete",
            "chaosengine,chaosresult,chaosexperiment,job,networkpolicy,role,rolebinding,serviceaccount,configmap",
            "-l", f"eve-trade.io/run-id={run_id}", "--ignore-not-found=true", "--wait=true", "--timeout=120s",
        ],
        [
            "kubectl", "delete", "clusterrole,clusterrolebinding",
            "-l", f"eve-trade.io/run-id={run_id}", "--ignore-not-found=true", "--wait=true", "--timeout=120s",
        ],
    ]
    for argv in commands:
        try:
            process = command(argv, timeout=140, check=False)
            if process.returncode:
                errors.append(f"{argv[0]} cleanup exited {process.returncode}: {process.stderr[-2000:]}")
        except BaseException as error:
            errors.append(f"{argv[0]} cleanup failed: {error}")
    return errors


def context_specific_selectors(scenario_id: str) -> list[str]:
    classification = json.loads((PROPERTY_ROOT / "classification.json").read_text(encoding="utf-8"))
    selectors = []
    for record in [*classification["records"], *classification["derived_business_tests"]]:
        if record.get("emulation_scenario_id") != scenario_id or not record.get("implementation_path"):
            continue
        selectors.append(f"{PROPERTY_ROOT / record['implementation_path']}::{record['name']}")
    return sorted(selectors)


def _pytest_business_stage(
    name: str,
    selectors: list[str],
    marker_expression: str | None,
    evidence_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    junit = artifact_root / f"{name}.xml"
    environment = os.environ.copy()
    environment["EVE_TRADE_EMULATION_EVIDENCE"] = str(evidence_path)
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(E2E_ROOT / "pytest.ini"),
    ]
    if marker_expression:
        argv.extend(["-m", marker_expression])
    argv.extend([*selectors, "-q", f"--junitxml={junit}"])
    started = time.monotonic()
    process = command(argv, env=environment, timeout=1800, check=False)
    (artifact_root / f"{name}.log").write_text(
        process.stdout + "\n--- stderr ---\n" + process.stderr,
        encoding="utf-8",
    )
    return {
        "command": argv,
        "exit_status": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "junit": str(junit),
        "log": str(artifact_root / f"{name}.log"),
    }


def run_business_tests(scenario_id: str, evidence_path: Path, artifact_root: Path) -> dict[str, Any]:
    stages = [
        _pytest_business_stage(
            "business-context-independent",
            [str(E2E_ROOT)],
            "context_independent",
            evidence_path,
            artifact_root,
        )
    ]
    scenario_selectors = context_specific_selectors(scenario_id)
    if scenario_selectors:
        stages.append(
            _pytest_business_stage(
                "business-context-specific",
                scenario_selectors,
                None,
                evidence_path,
                artifact_root,
            )
        )
    return {
        "exit_status": max(stage["exit_status"] for stage in stages),
        "stages": stages,
        "context_specific_test_count": len(scenario_selectors),
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    invocation_path = Path(args.invocation).resolve()
    invocation = load_invocation(invocation_path)
    if invocation.get("run_id") != args.run_id or invocation.get("namespace") != args.namespace:
        raise OrchestrationError("CLI run/namespace differ from the closed scenario invocation")
    if not RUN_ID.fullmatch(args.run_id) or not RUN_ID.fullmatch(args.namespace):
        raise OrchestrationError("run ID and namespace must be DNS-safe labels")
    scenario_path = SCENARIO_ROOT / str(invocation.get("scenario_id")) / "scenario.json"
    if not scenario_path.is_file():
        raise OrchestrationError("invocation references an unregistered scenario")
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    if scenario.get("status") != "IMPLEMENTED_BUT_EMULATION_VERIFICATION_PENDING":
        raise OrchestrationError(f"scenario is not executable: {scenario.get('status')}")
    uses_litmus = any(action.get("kind", "").startswith("LITMUS_") for action in scenario["action_plan"])

    artifact_root = Path(args.artifacts).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": "eve-trade.dagger-scenario-run/v1",
        "scenario_id": invocation["scenario_id"],
        "scenario_revision": invocation["scenario_revision"],
        "run_id": args.run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "uses_litmus": uses_litmus,
        "status": "running",
    }
    cleanup_errors: list[str] = []
    prepared = False
    try:
        prepare_safety(invocation, args.mode, args.confirm_disposable_context)
        prepared = True
        wait_for_registered_components(invocation)
        if uses_litmus:
            install(args.namespace, args.run_id, invocation["scenario_id"])
        controller_manifest = RUNTIME_ROOT / "deterministic-controller" / "Cargo.toml"
        command(["cargo", "build", "--release", "--manifest-path", str(controller_manifest)], timeout=900)
        controller = RUNTIME_ROOT / "deterministic-controller" / "target" / "release" / "deterministic-controller"
        runner = command(
            [
                sys.executable,
                str(RUNTIME_ROOT / "scenario_runner.py"),
                "--invocation",
                str(invocation_path),
                "--artifacts",
                str(artifact_root / "scenario-evidence"),
                "--controller",
                str(controller),
            ],
            timeout=7200,
        )
        runner_result = json.loads(runner.stdout.strip().splitlines()[-1])
        evidence_path = Path(runner_result["run_evidence"]).resolve()
        if not evidence_path.is_file():
            raise OrchestrationError("scenario runner reported no run evidence")
        business = run_business_tests(invocation["scenario_id"], evidence_path, artifact_root)
        summary["run_evidence"] = str(evidence_path)
        summary["business_tests"] = business
        if business["exit_status"] != 0:
            raise OrchestrationError("explicit business tests failed; see preserved JUnit and log")
        summary["status"] = "passed"
        return summary
    except BaseException as error:
        summary["status"] = "failed"
        summary["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # The safety marker is run-owned state even for scenarios that do not
        # use Litmus. The namespace is unique, so the same label-scoped
        # cleanup safely removes the marker and any partial chaos resources.
        if prepared:
            cleanup_errors = cleanup_litmus(args.namespace, args.run_id)
        if cleanup_errors:
            summary["status"] = "failed"
            summary.setdefault("failure", "Litmus cleanup did not complete")
        summary["cleanup_errors"] = cleanup_errors
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        (artifact_root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if cleanup_errors and sys.exc_info()[0] is None:
            raise OrchestrationError("Litmus cleanup failed: " + "; ".join(cleanup_errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("kind", "supplied"), required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--invocation", required=True)
    parser.add_argument("--confirm-disposable-context")
    args = parser.parse_args()
    result = execute(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
