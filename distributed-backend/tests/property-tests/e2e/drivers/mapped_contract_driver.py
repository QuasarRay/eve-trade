#!/usr/bin/env python3
"""Protocol-v2 external driver backed by an exact contract -> argv map.

The mapped command receives the full harness request JSON on stdin and must print
RAW OBSERVATIONS as a JSON object.  It must *not* decide pass/fail by returning
{"ok": true}; the parent Hypothesis harness injects identity fields and evaluates
the contract-specific EvidenceSpec and audited semantic checks itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _fail(code: int, message: str) -> int:
    print(message, file=sys.stderr)
    return code


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        return _fail(2, f"invalid protocol-v2 request JSON: {exc}")

    if request.get("protocol_version") != 2:
        return _fail(2, f"unsupported protocol version: {request.get('protocol_version')!r}")
    contract = str(request.get("contract") or "")
    case_sha = str(request.get("case_sha256") or "")
    if not contract.startswith("test_") or len(case_sha) != 64:
        return _fail(2, "request lacks valid contract/case_sha256")

    mapping_path = os.environ.get("EVE_TRADE_CONTRACT_DRIVER_MAP")
    if not mapping_path:
        return _fail(3, "EVE_TRADE_CONTRACT_DRIVER_MAP is not set")
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    command = mapping.get(contract)
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        return _fail(4, f"contract has no argv mapping: {contract}")

    env = os.environ.copy()
    env["EVE_TRADE_CONTRACT_NAME"] = contract
    env["EVE_TRADE_CONTRACT_CASE_SHA256"] = case_sha
    env["EVE_TRADE_CONTRACT_REPO_ROOT"] = str(request.get("repo_root") or "")
    env["EVE_TRADE_CONTRACT_ROLE"] = str(request.get("role") or "")
    env["EVE_TRADE_EVIDENCE_SPEC_JSON"] = json.dumps(request.get("evidence_spec", {}), separators=(",", ":"))

    proc = subprocess.run(
        command,
        cwd=str(request.get("repo_root") or "."),
        env=env,
        input=json.dumps(request, separators=(",", ":"), default=str),
        text=True,
        capture_output=True,
        check=False,
        timeout=int(os.environ.get("EVE_TRADE_MAPPED_DRIVER_TIMEOUT", "300")),
    )
    if proc.returncode != 0:
        return _fail(
            proc.returncode or 5,
            f"mapped command failed for {contract} rc={proc.returncode}\nstdout={proc.stdout[-16384:]}\nstderr={proc.stderr[-16384:]}",
        )
    try:
        observations: Any = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return _fail(6, f"mapped command returned non-JSON evidence for {contract}: {exc}")
    if not isinstance(observations, dict):
        return _fail(6, f"mapped command evidence must be an object, got {type(observations).__name__}")

    # Explicitly reject the old trust-oracle shape even inside the wrapper.
    substantive = set(observations) - {"ok", "contract", "protocol_version", "case_sha256"}
    if not substantive:
        return _fail(7, f"mapped command returned no raw observations for {contract}")

    # Identity is set by this wrapper from the trusted parent request, not copied
    # from the mapped command.  The parent checks all three values again.
    observations.pop("protocol_version", None)
    observations.pop("contract", None)
    observations.pop("case_sha256", None)
    observations["protocol_version"] = 2
    observations["contract"] = contract
    observations["case_sha256"] = case_sha
    print(json.dumps(observations, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
