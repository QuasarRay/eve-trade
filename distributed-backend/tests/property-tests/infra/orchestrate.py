#!/usr/bin/env python3
"""Own readiness, Litmus lifecycle, pytest stages, evidence export, and cleanup."""
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
E2E_ROOT = PROPERTY_ROOT / "e2e"
REPO_ROOT = PROPERTY_ROOT.parents[2]


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
            f"command failed ({process.returncode}): {' '.join(argv)}\n"
            f"stdout={process.stdout[-12000:]}\nstderr={process.stderr[-12000:]}"
        )
    return process


def kubectl_json(*args: str) -> dict[str, Any]:
    process = command(["kubectl", *args, "-o", "json"])
    value = json.loads(process.stdout)
    if not isinstance(value, dict):
        raise OrchestrationError(f"kubectl JSON was not an object for {args!r}")
    return value


def current_context() -> str:
    return command(["kubectl", "config", "current-context"]).stdout.strip()


def prepare_safety(namespace: str, run_id: str, mode: str, confirmation: str | None) -> None:
    context = current_context()
    lowered = context.lower()
    if any(token in lowered for token in ("prod", "production", "staging", "stage", "live")):
        raise OrchestrationError(f"refusing protected Kubernetes context {context!r}")
    if mode == "kind":
        if not context.startswith("kind-"):
            raise OrchestrationError(f"kind mode requires a kind-* context, got {context!r}")
    else:
        if confirmation != context:
            raise OrchestrationError(
                "supplied mode requires --confirm-disposable-context equal to current-context"
            )
        namespace_resource = kubectl_json("get", "namespace", namespace)
        if namespace_resource.get("metadata", {}).get("labels", {}).get(
            "eve-trade.io/chaos-safe"
        ) != "true":
            raise OrchestrationError(
                "supplied namespace must be pre-labelled eve-trade.io/chaos-safe=true"
            )
    command(
        [
            "kubectl", "label", "namespace", namespace,
            "eve-trade.io/chaos-safe=true",
            f"eve-trade.io/run-id={run_id}",
            "--overwrite",
        ]
    )
    marker = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "eve-trade-chaos-safety",
            "namespace": namespace,
            "labels": {"eve-trade.io/run-id": run_id},
        },
        "data": {
            "run_id": run_id,
            "allow_litmus": "true",
            "disposable_cluster": "true",
            "context": context,
        },
    }
    command(
        ["kubectl", "-n", namespace, "apply", "-f", "-"],
        input_text=yaml.safe_dump(marker, sort_keys=False),
    )


def wait_for_application(namespace: str) -> None:
    for deployment in ("postgres", "trade-settlement", "encore-backend", "quilkin", "simulator"):
        command(
            [
                "kubectl", "-n", namespace, "rollout", "status",
                f"deployment/{deployment}", "--timeout=300s",
            ],
            timeout=320,
        )
    command(
        [
            "kubectl", "-n", namespace, "rollout", "status",
            "statefulset/nsqd", "--timeout=300s",
        ],
        timeout=320,
    )


def pytest_stage(
    title: str,
    arguments: list[str],
    *,
    env: dict[str, str],
    artifact_root: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    junit = artifact_root / f"{title}.xml"
    process = command(
        [
            sys.executable,
            "-m",
            "pytest",
            *arguments,
            f"--junitxml={junit}",
        ],
        env=env,
        timeout=timeout,
        check=False,
    )
    log = artifact_root / f"{title}.log"
    log.write_text(process.stdout + "\n--- stderr ---\n" + process.stderr, encoding="utf-8")
    record = {
        "stage": title,
        "command": [sys.executable, "-m", "pytest", *arguments],
        "exit_status": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "junit": str(junit),
        "log": str(log),
    }
    return record


def cleanup(namespace: str, run_id: str) -> list[str]:
    errors: list[str] = []
    cleanup_commands = [
        [
            "helm", "uninstall", HELM_RELEASE, "--namespace", namespace,
            "--ignore-not-found", "--wait", "--timeout", "120s",
        ],
        [
            "kubectl", "-n", namespace, "delete",
            "chaosengine,chaosresult,chaosexperiment,job,pod,networkpolicy,role,rolebinding,serviceaccount,configmap",
            "-l", f"eve-trade.io/run-id={run_id}", "--ignore-not-found=true", "--wait=true", "--timeout=120s",
        ],
        [
            "kubectl", "delete", "clusterrole,clusterrolebinding",
            "-l", f"eve-trade.io/run-id={run_id}", "--ignore-not-found=true", "--wait=true", "--timeout=120s",
        ],
    ]
    for argv in cleanup_commands:
        try:
            process = command(argv, timeout=140, check=False)
            if process.returncode != 0:
                errors.append(
                    f"{' '.join(argv)} exited {process.returncode}: {process.stderr[-2000:]}"
                )
        except BaseException as exc:
            errors.append(f"{' '.join(argv)}: {exc}")
    namespace_resource = command(
        ["kubectl", "get", "namespace", namespace, "-o", "json"], check=False
    )
    if namespace_resource.returncode == 0:
        try:
            labels = json.loads(namespace_resource.stdout).get("metadata", {}).get("labels", {})
            if labels.get("eve-trade.io/run-id") == run_id:
                process = command(
                    [
                        "kubectl", "label", "namespace", namespace,
                        "eve-trade.io/run-id-", "eve-trade.io/chaos-safe-",
                    ],
                    check=False,
                )
                if process.returncode != 0:
                    errors.append(
                        "namespace label cleanup exited "
                        f"{process.returncode}: {process.stderr[-2000:]}"
                    )
        except BaseException as exc:
            errors.append(f"namespace label cleanup: {exc}")
    return errors


def validate_run_id(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized):
        raise OrchestrationError("run ID must be a DNS-safe label of at most 63 characters")
    return normalized


def execute(args: argparse.Namespace) -> dict[str, Any]:
    run_id = validate_run_id(args.run_id)
    artifact_root = Path(args.artifacts).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []
    cleanup_errors: list[str] = []
    failure: str | None = None
    prepared = False

    def required_stage(
        title: str,
        arguments: list[str],
        *,
        env: dict[str, str],
        timeout: int,
    ) -> None:
        record = pytest_stage(
            title,
            arguments,
            env=env,
            artifact_root=artifact_root,
            timeout=timeout,
        )
        # Persist the failed stage before aborting so summary.json is evidence of
        # the actual outcome rather than an empty/false-green stage list.
        stages.append(record)
        if record["exit_status"] != 0:
            raise OrchestrationError(
                f"pytest stage failed: {json.dumps(record, sort_keys=True)}"
            )

    try:
        prepare_safety(args.namespace, run_id, args.mode, args.confirm_disposable_context)
        prepared = True
        wait_for_application(args.namespace)
        install(args.namespace, run_id)
        test_env = os.environ.copy()
        test_env.update(
            {
                "EVE_TRADE_HYPOTHESIS_STRICT": "1",
                "EVE_TRADE_TEST_RUN_ID": run_id,
                "EVE_TRADE_APP_NAMESPACE": args.namespace,
                "EVE_TRADE_FAULT_DRIVER": (
                    f'"{sys.executable}" "{INFRA_ROOT / "litmus_driver.py"}"'
                ),
                "EVE_TRADE_CHAOS_ARTIFACTS": str(artifact_root / "evidence"),
                "EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES": str(args.chaos_examples),
            }
        )
        required_stage(
            "meta",
            [str(E2E_ROOT / "tests"), "--eve-hypothesis-strict", "-q"],
            env=test_env,
            timeout=600,
        )
        if args.suite in {"implemented", "full-strict"}:
            generated = str(E2E_ROOT / "eve_trade_hypothesis" / "generated")
            required_stage(
                "static-implemented",
                [
                    generated,
                    "-m", "eve_static and not eve_unimplemented",
                    "--eve-hypothesis-strict", "-q", "--maxfail=1",
                ],
                env=test_env,
                timeout=1800,
            )
            required_stage(
                "native-existing",
                [
                    generated,
                    "-m", "eve_existing",
                    "--eve-hypothesis-strict", "-q", "--maxfail=1",
                ],
                env=test_env,
                timeout=3600,
            )
            required_stage(
                "direct-live-implemented",
                [
                    generated,
                    "-m", "eve_live and not eve_unimplemented",
                    "--eve-hypothesis-strict", "-q", "--maxfail=1",
                ],
                env=test_env,
                timeout=7200,
            )
            required_stage(
                "chaos-implemented",
                [
                    generated,
                    "-m", "eve_fault and not eve_unimplemented",
                    "--eve-hypothesis-strict", "-q", "--maxfail=1",
                ],
                env=test_env,
                timeout=7200,
            )
        if args.suite == "full-strict":
            required_stage(
                "catalog-full-strict",
                [
                    str(E2E_ROOT / "eve_trade_hypothesis" / "generated"),
                    "--eve-hypothesis-strict", "-q", "--maxfail=1",
                ],
                env=test_env,
                timeout=7200,
            )
        return {"run_id": run_id, "suite": args.suite, "stages": stages}
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if prepared:
            cleanup_errors = cleanup(args.namespace, run_id)
        summary = {
            "schema_version": "eve-trade.property-pipeline/v1",
            "run_id": run_id,
            "suite": args.suite,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stages": stages,
            "status": "failed" if failure or cleanup_errors else "passed",
            "failure": failure,
            "cleanup_errors": cleanup_errors,
        }
        (artifact_root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if cleanup_errors and sys.exc_info()[0] is None:
            raise OrchestrationError("cleanup failed: " + "; ".join(cleanup_errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("kind", "supplied"), required=True)
    parser.add_argument("--namespace", default="eve-trade")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--suite", choices=("structural", "implemented", "full-strict"), default="implemented")
    parser.add_argument("--chaos-examples", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--confirm-disposable-context")
    args = parser.parse_args()
    result = execute(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
