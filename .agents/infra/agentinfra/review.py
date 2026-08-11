from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = {"ACCEPTED", "REJECTED", "PARTIAL"}
_WORKFLOW_INDEPENDENCE = {"WORKFLOW_INDEPENDENT"}
_IDENTITY_ATTESTATION = {"HOST_ATTESTED_IDENTITY", "UNATTESTED_IDENTITY"}
_REQUIRED_CHALLENGE_FIELDS = (
    "assumptions_tested",
    "counterexamples_attempted",
    "boundary_cases",
    "potential_failures",
)


class ReviewError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt_digest(receipt: dict) -> str:
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def review_payload_digest(payload: dict) -> str:
    """Return the canonical digest of a validated structured review payload."""

    return hashlib.sha256(_canonical(validate_review_payload(payload))).hexdigest()


def review_handoff_digest(handoff: dict) -> str:
    """Seal a closed workflow handoff without trusting a caller-provided seal."""

    if not isinstance(handoff, dict):
        raise ReviewError("review handoff must be an object")
    body = {key: value for key, value in handoff.items() if key != "handoff_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} must be non-empty")
    return value.strip()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ReviewError(f"{label} must be a canonical sha256")
    return value


def _require_string_list(value: object, label: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReviewError(f"{label} must be an array of non-empty strings")
    if nonempty and not value:
        raise ReviewError(f"rubber-stamp review: {label} is empty")
    return [item.strip() for item in value]


def validate_review_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ReviewError("review handoff payload schema must be exactly 1")
    normalized = json.loads(json.dumps(payload))
    for field in _REQUIRED_CHALLENGE_FIELDS:
        values = normalized.get(field)
        if not isinstance(values, list) or not values:
            raise ReviewError(f"rubber-stamp review: {field} is empty")
        for item in values:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("claim"), str) or not item["claim"].strip()
                or not isinstance(item.get("observation"), str) or not item["observation"].strip()
                or not isinstance(item.get("evidence_ids"), list) or not item["evidence_ids"]
                or any(not isinstance(value, str) or not value.startswith("E-") for value in item["evidence_ids"])
            ):
                raise ReviewError(f"structured review category is incomplete: {field}")
            if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
                raise ReviewError(f"structured review category repeats evidence: {field}")
    for field in ("unexpected_scope", "findings"):
        if not isinstance(normalized.get(field), list):
            raise ReviewError(f"review {field} must be an array")
    if normalized.get("outcome") not in _OUTCOMES:
        raise ReviewError("invalid review outcome")
    blocking = [
        item for item in normalized["findings"]
        if isinstance(item, dict)
        and item.get("severity") in {"HARD", "CRITICAL", "HIGH"}
        and item.get("status", "OPEN") != "RESOLVED"
    ]
    if normalized["outcome"] == "ACCEPTED" and blocking:
        raise ReviewError("accepted review contains unresolved blocking findings")
    return normalized


def build_operational_review_receipt(
    *,
    task_id: str,
    reviewer_lease: str,
    reviewer_role: str,
    reviewed_at: str,
    epoch: int,
    diff_digest: str,
    requirements_digest: str,
    evidence_set_digest: str,
    tdd_cycle_digest: str,
    test_law_baseline_digest: str,
    falsification_receipt_digest: str,
    context_brief_sha256: str,
    handoff_sha256: str,
    workflow_independence: str,
    identity_attestation: str,
    payload: dict,
    host_identity_attestation_evidence_id: str | None = None,
) -> dict:
    content = validate_review_payload(payload)
    if workflow_independence not in _WORKFLOW_INDEPENDENCE:
        raise ReviewError("review lacks workflow-independent lease/handoff provenance")
    if identity_attestation not in _IDENTITY_ATTESTATION:
        raise ReviewError("review identity-attestation status is invalid")
    if identity_attestation == "HOST_ATTESTED_IDENTITY":
        if (
            not isinstance(host_identity_attestation_evidence_id, str)
            or not host_identity_attestation_evidence_id.startswith("E-")
        ):
            raise ReviewError("host-attested review identity requires host evidence")
    elif host_identity_attestation_evidence_id is not None:
        raise ReviewError("unattested review identity cannot cite host-attestation evidence")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise ReviewError("review epoch must be non-negative")
    receipt = {
        "schema": 2,
        "task_id": _require_text(task_id, "task id"),
        "reviewer_lease": _require_text(reviewer_lease, "reviewer lease"),
        "reviewer_role": _require_text(reviewer_role, "reviewer role"),
        "reviewed_at": _require_text(reviewed_at, "review timestamp"),
        "epoch": epoch,
        "diff_digest": _require_digest(diff_digest, "diff digest"),
        "requirements_digest": _require_digest(requirements_digest, "requirements digest"),
        "evidence_set_digest": _require_digest(evidence_set_digest, "evidence-set digest"),
        "tdd_cycle_digest": _require_digest(tdd_cycle_digest, "TDD-cycle digest"),
        "test_law_baseline_digest": _require_digest(test_law_baseline_digest, "test/law baseline digest"),
        "falsification_receipt_digest": _require_digest(falsification_receipt_digest, "falsification receipt digest"),
        "context_brief_sha256": _require_digest(context_brief_sha256, "context brief digest"),
        "handoff_sha256": _require_digest(handoff_sha256, "handoff digest"),
        "workflow_independence": workflow_independence,
        "identity_attestation": identity_attestation,
        "host_identity_attestation_evidence_id": host_identity_attestation_evidence_id,
        **{field: content[field] for field in (*_REQUIRED_CHALLENGE_FIELDS, "unexpected_scope", "findings", "outcome")},
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)
    return receipt


def build_review_receipt(
    *,
    task_id: str,
    reviewer_lease: str,
    reviewer_role: str,
    reviewer_identity: str,
    implementer_identity: str,
    epoch: int,
    diff_digest: str,
    requirements_digest: str,
    evidence_set_digest: str,
    tdd_cycle_digest: str,
    test_law_baseline_digest: str,
    assumptions_tested: list[str],
    counterexamples_attempted: list[str],
    boundary_cases: list[str],
    potential_failures: list[str],
    unexpected_scope: list[str],
    findings: list[dict],
    outcome: str,
) -> dict:
    reviewer = _require_text(reviewer_identity, "reviewer identity")
    implementer = _require_text(implementer_identity, "implementer identity")
    if reviewer.casefold() == implementer.casefold():
        raise ReviewError("implementer cannot independently accept their own change")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise ReviewError("review epoch must be a non-negative integer")
    if outcome not in _OUTCOMES:
        raise ReviewError("invalid review outcome")
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise ReviewError("findings must be an array of objects")
    blocking = [item for item in findings if item.get("severity") in {"HARD", "CRITICAL", "HIGH"} and item.get("status", "OPEN") != "RESOLVED"]
    if outcome == "ACCEPTED" and blocking:
        raise ReviewError("accepted review contains unresolved blocking findings")
    receipt = {
        "schema": 1,
        "task_id": _require_text(task_id, "task id"),
        "reviewer_lease": _require_text(reviewer_lease, "reviewer lease"),
        "reviewer_role": _require_text(reviewer_role, "reviewer role"),
        "reviewer_identity": reviewer,
        "implementer_identity": implementer,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "epoch": epoch,
        "diff_digest": _require_digest(diff_digest, "diff digest"),
        "requirements_digest": _require_digest(requirements_digest, "requirements digest"),
        "evidence_set_digest": _require_digest(evidence_set_digest, "evidence-set digest"),
        "tdd_cycle_digest": _require_digest(tdd_cycle_digest, "TDD-cycle digest"),
        "test_law_baseline_digest": _require_digest(test_law_baseline_digest, "test/law baseline digest"),
        "assumptions_tested": _require_string_list(assumptions_tested, "assumptions_tested", nonempty=True),
        "counterexamples_attempted": _require_string_list(counterexamples_attempted, "counterexamples_attempted", nonempty=True),
        "boundary_cases": _require_string_list(boundary_cases, "boundary_cases", nonempty=True),
        "potential_failures": _require_string_list(potential_failures, "potential_failures", nonempty=True),
        "unexpected_scope": _require_string_list(unexpected_scope, "unexpected_scope", nonempty=False),
        "findings": findings,
        "outcome": outcome,
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)
    return receipt


def validate_review_receipt(
    receipt: dict,
    *,
    current_epoch: int,
    current_diff_digest: str,
    current_requirements_digest: str,
    current_evidence_set_digest: str,
    current_tdd_cycle_digest: str,
    current_test_law_baseline_digest: str,
    current_falsification_receipt_digest: str | None = None,
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("schema") not in {1, 2}:
        return False
    if receipt.get("receipt_sha256") != _receipt_digest(receipt):
        return False
    if receipt.get("outcome") != "ACCEPTED":
        return False
    if receipt.get("schema") == 1:
        if any(not isinstance(receipt.get(field), list) or not receipt[field] for field in _REQUIRED_CHALLENGE_FIELDS):
            return False
    else:
        try:
            validate_review_payload({
                "schema": 1,
                **{field: receipt.get(field) for field in (*_REQUIRED_CHALLENGE_FIELDS, "unexpected_scope", "findings", "outcome")},
            })
        except ReviewError:
            return False
        if (
            receipt.get("workflow_independence") != "WORKFLOW_INDEPENDENT"
            or receipt.get("identity_attestation") not in _IDENTITY_ATTESTATION
            or any(not _SHA256_RE.fullmatch(str(receipt.get(field, ""))) for field in ("falsification_receipt_digest", "context_brief_sha256", "handoff_sha256"))
        ):
            return False
        identity = receipt.get("identity_attestation")
        attestation_evidence = receipt.get("host_identity_attestation_evidence_id")
        if identity == "HOST_ATTESTED_IDENTITY" and (
            not isinstance(attestation_evidence, str) or not attestation_evidence.startswith("E-")
        ):
            return False
        if identity == "UNATTESTED_IDENTITY" and attestation_evidence is not None:
            return False
    if any(
        item.get("severity") in {"HARD", "CRITICAL", "HIGH"} and item.get("status", "OPEN") != "RESOLVED"
        for item in receipt.get("findings", [])
        if isinstance(item, dict)
    ):
        return False
    expected = {
        "epoch": current_epoch,
        "diff_digest": current_diff_digest,
        "requirements_digest": current_requirements_digest,
        "tdd_cycle_digest": current_tdd_cycle_digest,
        "test_law_baseline_digest": current_test_law_baseline_digest,
    }
    # A schema-1 receipt claimed the entire evidence ledger and therefore must
    # remain exactly equal to it.  A schema-2 operational receipt records the
    # reviewed ledger prefix; later framework verification/audit observations
    # may extend that prefix.  StateStore independently proves that the sealed
    # prefix still exists before accepting the receipt.
    if receipt.get("schema") == 1:
        expected["evidence_set_digest"] = current_evidence_set_digest
    elif current_falsification_receipt_digest is not None:
        expected["falsification_receipt_digest"] = current_falsification_receipt_digest
    return all(receipt.get(field) == value for field, value in expected.items())


def review_receipt_digest(receipt: dict) -> str:
    if not isinstance(receipt, dict) or receipt.get("receipt_sha256") != _receipt_digest(receipt):
        raise ReviewError("review receipt integrity failure")
    return receipt["receipt_sha256"]
