"""Fail-closed validation for downloaded producer evidence envelopes.

The repository aggregator currently treats digest/signature fields as optional.
This boundary makes them mandatory before any aggregate can be accepted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


SCHEMA_VERSION = "o11y.ci-evidence.v2"
SIGNATURE_VERSION = "sha256-workflow-bound-v1"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_FIELDS = {
    "schema_version",
    "repository",
    "branch_ref",
    "commit_sha",
    "workflow",
    "run_id",
    "run_attempt",
    "workflow_definition_digest",
    "job_id",
    "job_name",
    "step_identity",
    "started_at",
    "ended_at",
    "command_identity",
    "exit_status",
    "normalized_diagnostic",
    "dependencies",
    "commands",
    "collector_status",
    "provenance",
    "artifact_digest",
    "signature",
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def canonical_digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key not in {"artifact_digest", "signature"}}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def expected_signature(value: Mapping[str, Any]) -> dict[str, str]:
    workflow_digest = str(value.get("workflow_definition_digest", ""))
    artifact_digest = str(value.get("artifact_digest", ""))
    payload = f"{SIGNATURE_VERSION}\n{workflow_digest}\n{artifact_digest}".encode("utf-8")
    return {
        "algorithm": SIGNATURE_VERSION,
        "key_id": workflow_digest,
        "value": f"sha256:{hashlib.sha256(payload).hexdigest()}",
    }


def validate_bundle(bundle: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(bundle))
    if missing:
        errors.append("missing mandatory fields: " + ", ".join(missing))
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported evidence schema")
    if bundle.get("collector_status") != "COMPLETE":
        errors.append("collector did not complete")
    provenance = bundle.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    elif bool(provenance.get("historical")):
        errors.append("historical producer evidence is non-current")
    if not isinstance(bundle.get("commands"), list):
        errors.append("commands must be an array")
    if not isinstance(bundle.get("dependencies"), list):
        errors.append("dependencies must be an array")

    artifact_digest = bundle.get("artifact_digest")
    if not isinstance(artifact_digest, str) or not DIGEST_RE.fullmatch(artifact_digest):
        errors.append("artifact digest is missing or malformed")
    elif not hmac.compare_digest(artifact_digest, canonical_digest(bundle)):
        errors.append("artifact digest mismatch")

    signature = bundle.get("signature")
    wanted_signature = expected_signature(bundle)
    if not isinstance(signature, dict):
        errors.append("signature is missing")
    else:
        for field, wanted in wanted_signature.items():
            actual = signature.get(field)
            if not isinstance(actual, str) or not hmac.compare_digest(actual, wanted):
                errors.append("signature is unverifiable")
                break
    return errors


def validate_directory(path: Path) -> list[str]:
    if not path.is_dir():
        return [f"producer evidence directory is missing: {path}"]
    candidates = sorted(path.glob("*.json"))
    if not candidates:
        return [f"producer evidence directory contains no JSON artifacts: {path}"]
    errors: list[str] = []
    for candidate in candidates:
        try:
            if candidate.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact exceeds size limit")
            raw = candidate.read_text(encoding="utf-8-sig")
            bundle = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
            if not isinstance(bundle, dict):
                raise ValueError("artifact root must be an object")
            errors.extend(f"{candidate.name}: {message}" for message in validate_bundle(bundle))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{candidate.name}: corrupted artifact: {exc}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(f"usage: {Path(sys.argv[0]).name} <producer-evidence-directory>", file=sys.stderr)
        return 2
    errors = validate_directory(Path(args[0]))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
