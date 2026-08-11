from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from .catalog import category_for
from .evidence_specs import build_evidence_spec, spec_to_dict, validate_evidence
from .semantic_validation import validate_audited_semantics


def canonical_case_sha256(case: dict[str, Any]) -> str:
    encoded = json.dumps(case, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ExternalContractDriver:
    """Run an environment-specific probe and independently validate raw evidence.

    Protocol v2 deliberately forbids the old `{\"ok\": true}` trust-oracle
    response.  The child receives the compiled predicate spec and must return raw
    observations.  The Python harness evaluates every predicate itself.
    """

    protocol_version = 2

    def __init__(self, command: str | None, *, repo_root: Path, strict: bool, role: str):
        self.command = command
        self.repo_root = repo_root
        self.strict = strict
        self.role = role

    def run(self, contract_name: str, case: dict[str, Any]) -> dict[str, Any]:
        category = category_for(contract_name)
        if category is None:
            raise AssertionError(f"external contract is not in proposed catalog: {contract_name}")
        spec = build_evidence_spec(category, contract_name)
        if not self.command:
            reason = (
                f"{self.role} contract {contract_name} requires protocol-v2 raw evidence; "
                f"set EVE_TRADE_{self.role.upper()}_DRIVER"
            )
            if self.strict:
                pytest.fail(reason, pytrace=False)
            pytest.skip(reason)

        argv = shlex.split(self.command)
        case_sha = canonical_case_sha256(case)
        request = {
            "protocol_version": self.protocol_version,
            "contract": contract_name,
            "category": category,
            "case": case,
            "case_sha256": case_sha,
            "repo_root": str(self.repo_root),
            "role": self.role,
            "evidence_spec": spec_to_dict(spec),
        }
        proc = subprocess.run(
            argv,
            input=json.dumps(request, default=str),
            text=True,
            capture_output=True,
            cwd=str(self.repo_root),
            timeout=int(os.environ.get("EVE_TRADE_EXTERNAL_DRIVER_TIMEOUT", "300")),
            check=False,
        )
        assert proc.returncode == 0, (
            f"{self.role} driver exited {proc.returncode} for {contract_name}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"{self.role} driver returned non-JSON output for {contract_name}: {proc.stdout!r}"
            ) from exc
        assert isinstance(result, dict), result
        assert result.get("protocol_version") == self.protocol_version, result
        assert result.get("contract") == contract_name, result
        assert result.get("case_sha256") == case_sha, (
            f"driver did not prove it executed the exact Hypothesis case: expected {case_sha}, "
            f"got {result.get('case_sha256')!r}"
        )
        # A naked success flag is intentionally insufficient.
        assert set(result) - {"ok", "contract", "protocol_version", "case_sha256"}, (
            f"{contract_name}: driver returned no raw evidence"
        )
        validate_evidence(spec, result)
        validate_audited_semantics(contract_name, result)
        return result
