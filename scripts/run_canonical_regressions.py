#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_LOG_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Command:
    name: str
    arguments: tuple[str, ...]
    cwd: Path = ROOT
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300


def go_group(name: str, package: str, pattern: str) -> Command:
    go_arguments = (
        "test",
        "-v",
        "-count=1",
        "-timeout=120s",
        package,
        "-run",
        pattern,
    )
    if sys.platform == "win32":
        image = os.environ.get("EVE_TRADE_GO_RACE_IMAGE", "golang:1.26.5-bookworm")
        arguments = (
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{ROOT}:/workspace",
            "--workdir",
            "/workspace",
            "--env",
            "ENCORERUNTIME_NOPANIC=1",
            "--env",
            "GOTOOLCHAIN=local",
            "--mount",
            "type=volume,source=eve-trade-go-build-1-26-5,target=/root/.cache/go-build",
            image,
            "sh",
            "-euc",
            (
                'version="$(go version)"; echo "$version"; '
                'case "$version" in "go version go1.26.5 "*) ;; '
                '*) echo "expected go1.26.5 canonical toolchain" >&2; exit 1 ;; esac; '
                'exec go "$@"'
            ),
            "go",
            *go_arguments,
        )
    else:
        arguments = ("go", *go_arguments)
    return Command(
        name,
        arguments,
    )


def commands() -> list[Command]:
    python = sys.executable
    return [
        Command(
            "market-outbox",
            (
                "cargo",
                "test",
                "--locked",
                "service::tests::test_",
                "--",
                "--nocapture",
            ),
            cwd=ROOT / "distributed-backend" / "src" / "trade-settlement",
        ),
        go_group("worker-delivery", "./distributed-backend/src/settlementworker", "^TestCanonicalWorkerDeliveryRegressions$"),
        go_group("gateway-replay", "./distributed-backend/src/gateway", "^TestCanonicalReplayCacheRegressions$"),
        go_group("gateway-limiter", "./distributed-backend/src/gateway", "^TestCanonicalRateLimiterRegressions$"),
        go_group("gateway-parser-config", "./distributed-backend/src/gateway", "^TestCanonical(JSON|Configuration)Regressions$"),
        go_group("worker-lifecycle", "./distributed-backend/src/settlementworker", "^TestCanonicalWorkerLifecycleRegressions$"),
        go_group("gateway-listener", "./distributed-backend/src/gateway", "^TestCanonicalGatewayListenerRegressions$"),
        Command(
            "simulator-udp",
            (python, "manage.py", "test", "trade_gui.test_udp_regressions", "-v", "2"),
            cwd=ROOT / "simulator",
        ),
        Command(
            "e2e-udp-pool",
            (python, "-m", "pytest", "-vv", "test_udp_pool_regressions.py"),
            cwd=ROOT / "distributed-backend" / "tests" / "e2e",
        ),
        Command(
            "race-crash",
            (python, "-m", "unittest", "-v", "tests.regression.test_race_and_crash_contracts"),
        ),
        Command(
            "observability",
            (python, "-m", "unittest", "-v", "observability.tests.test_regression_contracts"),
            environment={"PYTHONPATH": str(ROOT / "distributed-backend")},
        ),
        Command(
            "workflow-infrastructure",
            (python, "-m", "unittest", "-v", "scripts.tests.test_regression_contracts"),
        ),
        Command(
            "migrations",
            (python, "-m", "unittest", "-v", "test_regression_contracts.py"),
            cwd=ROOT / "distributed-backend" / "tests" / "migrations",
        ),
    ]


def require_environment() -> None:
    if not os.environ.get("EVE_TRADE_TEST_DATABASE_URL", "").strip():
        raise SystemExit(
            "EVE_TRADE_TEST_DATABASE_URL is required; canonical verification is incomplete without PostgreSQL"
        )


def run(command: Command, output_dir: Path) -> tuple[int, Path]:
    environment = os.environ.copy()
    environment.update(command.environment)
    environment.setdefault("ENCORERUNTIME_NOPANIC", "1")
    print(f"\n===== {command.name}: {' '.join(command.arguments)} =====", flush=True)
    try:
        result = subprocess.run(
            command.arguments,
            cwd=command.cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=command.timeout_seconds,
            check=False,
        )
        output = result.stdout
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"") + f"\ncommand timed out after {command.timeout_seconds}s\n".encode()
        exit_code = 124

    truncated = len(output) > MAX_LOG_BYTES
    persisted = output[:MAX_LOG_BYTES]
    if truncated:
        persisted += f"\n[log truncated at {MAX_LOG_BYTES} bytes]\n".encode()
    path = output_dir / f"{command.name}.log"
    path.write_bytes(persisted)
    sys.stdout.write(persisted.decode("utf-8", errors="replace"))
    if truncated:
        print(f"[{command.name} output exceeded the artifact cap]", flush=True)
    print(f"===== {command.name}: exit {exit_code} =====", flush=True)
    return exit_code, path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all 200 canonical regression tests without short-circuiting")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "canonical-regressions",
    )
    args = parser.parse_args()
    require_environment()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, int]] = []
    logs: list[Path] = []
    for command in commands():
        exit_code, path = run(command, output_dir)
        results.append((command.name, exit_code))
        logs.append(path)

    verifier = [
        sys.executable,
        str(ROOT / "scripts" / "verify_canonical_regression_tests.py"),
    ]
    for path in logs:
        verifier.extend(("--output", str(path)))
    verification = subprocess.run(verifier, cwd=ROOT, check=False)

    print("\nCanonical regression command summary:")
    for name, exit_code in results:
        print(f"  {name}: {'PASS' if exit_code == 0 else f'FAIL ({exit_code})'}")
    if verification.returncode != 0:
        print(f"  exact-name-verifier: FAIL ({verification.returncode})")
    else:
        print("  exact-name-verifier: PASS")

    failed = [name for name, exit_code in results if exit_code != 0]
    if failed or verification.returncode != 0:
        print("canonical regression verification FAILED")
        return 1
    print("canonical regression verification PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
