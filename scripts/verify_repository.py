#!/usr/bin/env python3
"""Run or validate the canonical repository verification profile."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
FULL_GATES = (
    "protobuf",
    "architecture",
    "gui",
    "go",
    "rust",
    "python",
    "terraform",
    "kubernetes",
    "security",
    "canonical-regression",
    "e2e",
    "o11y",
)
CI_JOBS = {
    "protobuf": "proto",
    "architecture": "architecture",
    "gui": "gui-contract",
    "go": "go",
    "rust": "rust-trade-settlement",
    "python": "python",
    "terraform": "terraform",
    "kubernetes": "kubernetes",
    "security": "security",
    "canonical-regression": "canonical-regression",
    "e2e": "e2e",
}


@dataclass(frozen=True)
class Phase:
    name: str
    command: tuple[str, ...]
    gates: tuple[str, ...]


def local_phases(python: str = sys.executable) -> tuple[Phase, ...]:
    pipeline = str(ROOT / "distributed-backend" / "ci-cd" / "pipeline.py")
    return (
        Phase(
            "checks",
            (python, pipeline, "check"),
            ("protobuf", "architecture", "python", "terraform", "kubernetes"),
        ),
        Phase(
            "tests",
            (python, pipeline, "test"),
            ("gui", "go", "rust"),
        ),
        Phase(
            "security",
            (python, pipeline, "security"),
            ("security",),
        ),
        Phase(
            "canonical-regression",
            (python, str(ROOT / "scripts" / "run_canonical_regressions.py")),
            ("canonical-regression",),
        ),
        Phase(
            "e2e-observed",
            ("bash", str(ROOT / "scripts" / "run_kind_e2e.sh")),
            ("e2e", "o11y"),
        ),
    )


def classification(completed: set[str], failed: bool) -> tuple[str, int]:
    if failed:
        return "FAILED_VERIFICATION", 1
    if completed == set(FULL_GATES):
        return "COMPLETE_VERIFICATION_PASSED", 0
    return "PARTIAL_VERIFICATION_PASSED", 2


def write_result(path: Path | None, payload: Mapping[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_local(selected: Sequence[str], output: Path | None) -> int:
    phases = local_phases()
    selected_names = set(selected) if selected else {phase.name for phase in phases}
    unknown = selected_names.difference(phase.name for phase in phases)
    if unknown:
        raise SystemExit(f"unknown local verification phases: {', '.join(sorted(unknown))}")

    completed: set[str] = set()
    results: list[dict[str, object]] = []
    failed = False
    for phase in phases:
        if phase.name not in selected_names:
            continue
        print(f"\n==> verification phase: {phase.name}", flush=True)
        print("command: " + " ".join(phase.command), flush=True)
        result = subprocess.run(phase.command, cwd=ROOT, check=False)
        results.append(
            {
                "phase": phase.name,
                "command": list(phase.command),
                "exit_code": result.returncode,
                "gates": list(phase.gates),
            }
        )
        if result.returncode == 0:
            completed.update(phase.gates)
        else:
            failed = True

    status, exit_code = classification(completed, failed)
    payload = {
        "schema_version": "eve-trade.verification-profile/v1",
        "profile": "full",
        "status": status,
        "completed_gates": sorted(completed),
        "missing_gates": sorted(set(FULL_GATES).difference(completed)),
        "phases": results,
    }
    write_result(output, payload)
    print(f"\n{status}")
    if payload["missing_gates"]:
        print("missing gates: " + ", ".join(payload["missing_gates"]))
    return exit_code


def parse_needs(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit(f"CI needs JSON is invalid: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit("CI needs JSON must be an object")
    return value


def ci_gate_results(needs: Mapping[str, object], current_gates: Sequence[str]) -> dict[str, str]:
    current = set(current_gates)
    unknown = current.difference(FULL_GATES)
    if unknown:
        raise SystemExit(f"unknown current gates: {', '.join(sorted(unknown))}")
    results = {gate: "success" for gate in current}
    for gate, job in CI_JOBS.items():
        record = needs.get(job)
        if not isinstance(record, dict):
            results[gate] = "missing"
            continue
        result = record.get("result")
        results[gate] = result if isinstance(result, str) else "missing"
    return results


def validate_ci(needs: Mapping[str, object], current_gates: Sequence[str], output: Path | None) -> int:
    results = ci_gate_results(needs, current_gates)
    completed = {gate for gate, result in results.items() if result == "success"}
    failed = any(result not in {"success", "missing"} for result in results.values())
    status, exit_code = classification(completed, failed)
    if any(result == "missing" for result in results.values()):
        status, exit_code = "FAILED_VERIFICATION", 1
    payload = {
        "schema_version": "eve-trade.verification-profile/v1",
        "profile": "full",
        "status": status,
        "gate_results": dict(sorted(results.items())),
        "missing_gates": sorted(set(FULL_GATES).difference(completed)),
    }
    write_result(output, payload)
    print(status)
    for gate, result in sorted(results.items()):
        print(f"{gate}: {result}")
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run-local")
    run.add_argument("--phase", action="append", default=[])
    run.add_argument("--output", type=Path)
    validate = subcommands.add_parser("validate-ci")
    validate.add_argument("--needs-json")
    validate.add_argument("--needs-json-env", default="OBS_CI_NEEDS_JSON")
    validate.add_argument("--current-gate", action="append", default=[])
    validate.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run-local":
        return run_local(args.phase, args.output)
    raw = args.needs_json if args.needs_json is not None else os.environ.get(args.needs_json_env, "")
    if not raw:
        raise SystemExit(f"CI needs JSON is required via --needs-json or {args.needs_json_env}")
    return validate_ci(parse_needs(raw), args.current_gate, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
