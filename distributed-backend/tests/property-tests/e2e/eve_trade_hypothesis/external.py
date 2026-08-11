from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from .catalog import category_for
from .chaos_oracles import validate_chaos_oracle
from .evidence_integrity import (
    EVIDENCE_SCHEMA,
    PROTOCOL_VERSION,
    EvidenceIntegrityError,
    isoformat_utc,
    validate_chaos_evidence,
    validate_execution_identity,
)
from .evidence_specs import build_evidence_spec, spec_to_dict, validate_evidence
from .requirements import litmus_contract_for, requirement_for
from .semantic_validation import validate_audited_semantics


def canonical_case_sha256(case: dict[str, Any]) -> str:
    encoded = json.dumps(case, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ExternalContractDriver:
    """Execute a probe while keeping pass/fail and evidence integrity in Python.

    Protocol v3 adds a fresh invocation ID and nonce, bounded timestamps, unique
    evidence IDs, raw-observation provenance, and (for Litmus contracts) physical
    target-effect plus request-window validation.  A matching case hash is still
    required, but is no longer treated as freshness or fault proof.
    """

    protocol_version = PROTOCOL_VERSION

    def __init__(
        self,
        command: str | None,
        *,
        repo_root: Path,
        strict: bool,
        role: str,
        run_id: str,
    ):
        self.command = command
        self.repo_root = repo_root
        self.strict = strict
        self.role = role
        self.run_id = run_id
        self._seen_evidence_ids: set[str] = set()

    def _unavailable(self, reason: str) -> None:
        if self.strict:
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    def run(self, contract_name: str, case: dict[str, Any]) -> dict[str, Any]:
        category = category_for(contract_name)
        if category is None:
            raise AssertionError(f"external contract is not in proposed catalog: {contract_name}")
        requirement = requirement_for(contract_name)
        status = requirement["implementation_status"]
        if status not in {"IMPLEMENTED", "JUSTIFIED_NON_APPLICABLE"}:
            blockers = "; ".join(requirement.get("blockers", []))
            self._unavailable(f"{contract_name} is fail-closed: {status}: {blockers}")

        spec = build_evidence_spec(category, contract_name)
        if not self.command:
            self._unavailable(
                f"{self.role} contract {contract_name} requires fresh protocol-v3 raw evidence; "
                f"set EVE_TRADE_{self.role.upper()}_DRIVER"
            )

        argv = shlex.split(self.command)
        if not argv:
            raise AssertionError(f"empty {self.role} driver command")
        case_sha = canonical_case_sha256(case)
        issued_at = datetime.now(timezone.utc)
        invocation_id = secrets.token_hex(16)
        nonce = secrets.token_urlsafe(24)
        max_age_seconds = int(os.environ.get("EVE_TRADE_EVIDENCE_MAX_AGE_SECONDS", "600"))
        if not 1 <= max_age_seconds <= 600:
            raise RuntimeError("EVE_TRADE_EVIDENCE_MAX_AGE_SECONDS must be between 1 and 600")
        request: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "evidence_schema": EVIDENCE_SCHEMA,
            "contract": contract_name,
            "category": category,
            "case": case,
            "case_sha256": case_sha,
            "repo_root": str(self.repo_root),
            "role": self.role,
            "requirement": requirement,
            "execution": {
                "run_id": self.run_id,
                "invocation_id": invocation_id,
                "nonce": nonce,
                "issued_at": isoformat_utc(issued_at),
                "max_age_seconds": max_age_seconds,
            },
            "evidence_spec": spec_to_dict(spec),
        }
        if requirement["mechanism"] == "LITMUS_CHAOS":
            request["litmus_contract"] = litmus_contract_for(contract_name)

        proc = subprocess.run(
            argv,
            input=json.dumps(request, default=str),
            text=True,
            capture_output=True,
            cwd=str(self.repo_root),
            timeout=int(os.environ.get("EVE_TRADE_EXTERNAL_DRIVER_TIMEOUT", "600")),
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"{self.role} driver exited {proc.returncode} for {contract_name}\n"
                f"stdout:\n{proc.stdout[-16384:]}\nstderr:\n{proc.stderr[-16384:]}"
            )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"{self.role} driver returned non-JSON output for {contract_name}: "
                f"{proc.stdout[-4096:]!r}"
            ) from exc
        if not isinstance(result, dict):
            raise AssertionError(f"{self.role} driver evidence must be a JSON object")

        try:
            validate_execution_identity(
                request,
                result,
                seen_evidence_ids=self._seen_evidence_ids,
            )
            if requirement["mechanism"] == "LITMUS_CHAOS":
                validate_chaos_evidence(request, result, request["litmus_contract"])
                validate_chaos_oracle(
                    contract_name,
                    request,
                    result,
                    request["litmus_contract"],
                )
            validate_evidence(spec, result)
            validate_audited_semantics(contract_name, result)
        except EvidenceIntegrityError as exc:
            raise AssertionError(f"{contract_name}: invalid external evidence: {exc}") from exc
        return result
