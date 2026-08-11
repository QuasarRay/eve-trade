from __future__ import annotations

"""Evidence-sealed FINAL_AUDIT contract.

The state machine decides when a task may enter FINAL_AUDIT.  This module
defines the exact observation set required to complete it.  Observation
producers are framework-controlled commands or verified/external boundaries;
manual assertions can never become a passing audit receipt.
"""

from collections import Counter
import hashlib
import json
import re


REQUIRED_FINAL_AUDIT_CHECKS = (
    "expected_repository_root",
    "boundary_snapshot_current",
    "governance_digest_unchanged",
    "no_agents_mutation",
    "no_governing_instruction_mutation",
    "no_unexpected_nested_repository_mutation",
    "no_overwritten_user_owned_dirty_file",
    "no_unexpected_generated_churn",
    "no_unexpected_lockfile_change",
    "no_unauthorized_dependency_change",
    "no_unauthorized_test_law_mutation",
    "no_reference_mutation",
    "write_scope_respected",
    "change_budget_respected_or_replanned",
    "semantic_budget_respected_or_replanned",
    "all_hard_gates_proven",
    "all_required_gates_proven_or_externally_waived",
    "advisory_omissions_justified",
    "mandatory_gate_families_present",
    "every_behavioral_production_change_has_tdd_cycle",
    "test_design_predates_implementation",
    "baseline_execution_predates_implementation",
    "required_red_legitimate_and_current",
    "characterization_present_where_required",
    "test_contract_frozen",
    "oracle_frozen",
    "green_same_frozen_contract",
    "green_current_implementation_epoch",
    "verification_current",
    "review_current",
    "review_lease_closed",
    "falsification_recorded",
    "no_unresolved_blocking_finding",
    "required_commands_executed",
    "no_secrets_introduced",
    "no_suspicious_test_aware_behavior",
    "no_fake_red",
    "no_fabricated_capability_claim",
    "final_workspace_fingerprint_recorded",
    "compiled_task_contract_current",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE = {"framework-command", "verified-observation", "external-source"}
_FINAL_STATUSES = {"PROVEN", "NOT_APPLICABLE"}


class FinalAuditError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def non_write_audit_binding_digest(
    *,
    binding: str,
    task_id: str,
    epoch: int,
    workspace_sha256: str,
) -> str:
    """Return a candidate-bound digest for a semantically absent write proof.

    Exact task-audit receipts retain a fixed schema for every task mode.  A
    read-only task therefore binds the TDD/review/falsification receipt slots
    to explicit, deterministic NOT_APPLICABLE material instead of inventing a
    trusted cycle or leaving an unauditable null.
    """

    if binding not in {"tdd-cycle", "review-receipt", "falsification-receipt"}:
        raise FinalAuditError("unknown non-write audit binding")
    if not isinstance(task_id, str) or not task_id or not isinstance(epoch, int) or epoch < 0:
        raise FinalAuditError("invalid non-write task binding identity")
    if not isinstance(workspace_sha256, str) or not _SHA256_RE.fullmatch(workspace_sha256):
        raise FinalAuditError("invalid non-write workspace binding")
    return _digest({
        "schema": 1,
        "status": "NOT_APPLICABLE",
        "justification": "task mode is read-only; no behavioral production mutation exists",
        "binding": binding,
        "task_id": task_id,
        "epoch": epoch,
        "workspace_sha256": workspace_sha256,
    })


def seal_audit_observation(
    *,
    check_id: str,
    status: str,
    evidence_digest: str,
    provenance: str,
    detail: str,
    justification: str | None = None,
) -> dict:
    if check_id not in REQUIRED_FINAL_AUDIT_CHECKS:
        raise FinalAuditError(f"unknown final-audit check: {check_id}")
    if status not in _FINAL_STATUSES:
        raise FinalAuditError("final-audit observation must be PROVEN or NOT_APPLICABLE")
    if not isinstance(evidence_digest, str) or not _SHA256_RE.fullmatch(evidence_digest):
        raise FinalAuditError("final-audit observation requires a canonical evidence digest")
    if provenance not in _PROVENANCE:
        raise FinalAuditError("manual or unknown provenance cannot prove final audit")
    if not isinstance(detail, str) or not detail.strip():
        raise FinalAuditError("final-audit observation requires execution detail")
    if status == "NOT_APPLICABLE" and (
        not isinstance(justification, str) or not justification.strip()
    ):
        raise FinalAuditError("NOT_APPLICABLE final-audit observation requires justification")
    body = {
        "schema": 1,
        "check_id": check_id,
        "status": status,
        "evidence_digest": evidence_digest,
        "provenance": provenance,
        "detail": detail.strip(),
        "justification": justification.strip() if isinstance(justification, str) else None,
    }
    body["observation_sha256"] = _digest(body)
    return body


def _validate_observation(record: object, expected_id: str) -> dict:
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise FinalAuditError(f"invalid final-audit observation schema: {expected_id}")
    if record.get("check_id") != expected_id:
        raise FinalAuditError("final-audit observations are missing, duplicated, or reordered")
    claimed = record.get("observation_sha256")
    body = {key: value for key, value in record.items() if key != "observation_sha256"}
    if claimed != _digest(body):
        raise FinalAuditError(f"final-audit observation seal is invalid: {expected_id}")
    if record.get("status") not in _FINAL_STATUSES:
        raise FinalAuditError(f"final-audit check is not proven: {expected_id}")
    if record.get("provenance") not in _PROVENANCE:
        raise FinalAuditError(f"final-audit check has manual/unknown provenance: {expected_id}")
    if not _SHA256_RE.fullmatch(str(record.get("evidence_digest", ""))):
        raise FinalAuditError(f"final-audit evidence digest is invalid: {expected_id}")
    if not isinstance(record.get("detail"), str) or not record["detail"].strip():
        raise FinalAuditError(f"final-audit execution detail is missing: {expected_id}")
    if record.get("status") == "NOT_APPLICABLE" and (
        not isinstance(record.get("justification"), str) or not record["justification"].strip()
    ):
        raise FinalAuditError(f"final-audit NOT_APPLICABLE check is unjustified: {expected_id}")
    return record


def finalize_audit(
    observations: list[dict],
    *,
    expected_workspace_digest: str,
    current_workspace_digest: str,
) -> dict:
    if not isinstance(observations, list) or len(observations) != len(REQUIRED_FINAL_AUDIT_CHECKS):
        raise FinalAuditError(
            f"final audit requires exactly {len(REQUIRED_FINAL_AUDIT_CHECKS)} observations"
        )
    if not _SHA256_RE.fullmatch(str(expected_workspace_digest)) or not _SHA256_RE.fullmatch(
        str(current_workspace_digest)
    ):
        raise FinalAuditError("final audit requires canonical workspace digests")
    if expected_workspace_digest != current_workspace_digest:
        raise FinalAuditError("workspace changed during final audit")
    validated = [
        _validate_observation(record, check_id)
        for record, check_id in zip(observations, REQUIRED_FINAL_AUDIT_CHECKS)
    ]
    if [record["check_id"] for record in validated] != list(REQUIRED_FINAL_AUDIT_CHECKS):
        raise FinalAuditError("final-audit observation identity is not an exact contract bijection")
    counts = dict(sorted(Counter(record["status"] for record in validated).items()))
    body = {
        "schema": 1,
        "outcome": "PASS",
        "check_count": len(validated),
        "counts": counts,
        "workspace_sha256": current_workspace_digest,
        "observations": validated,
    }
    body["receipt_sha256"] = _digest(body)
    return body


def seal_task_audit_observation(
    *,
    check_id: str,
    producer_id: str,
    status: str,
    proof_digest: str,
    evidence_record_ids: list[str],
    detail: str,
    justification: str | None = None,
) -> dict:
    """Seal one check-specific operational proof.

    Unlike the compatibility schema above, this schema names the unique
    producer and the exact evidence record(s) carrying that producer's facts.
    """

    if check_id not in REQUIRED_FINAL_AUDIT_CHECKS:
        raise FinalAuditError(f"unknown final-audit check: {check_id}")
    if not isinstance(producer_id, str) or not producer_id.strip():
        raise FinalAuditError("task-audit observation requires a producer id")
    if status not in _FINAL_STATUSES:
        raise FinalAuditError("task-audit observation must be PROVEN or NOT_APPLICABLE")
    if not isinstance(proof_digest, str) or not _SHA256_RE.fullmatch(proof_digest):
        raise FinalAuditError("task-audit observation requires a canonical proof digest")
    if (
        not isinstance(evidence_record_ids, list)
        or not evidence_record_ids
        or len(evidence_record_ids) != len(set(evidence_record_ids))
        or any(not isinstance(item, str) or not item.startswith("E-") for item in evidence_record_ids)
    ):
        raise FinalAuditError("task-audit observation requires unique evidence record ids")
    if not isinstance(detail, str) or not detail.strip():
        raise FinalAuditError("task-audit observation requires detail")
    if status == "NOT_APPLICABLE" and (
        not isinstance(justification, str) or not justification.strip()
    ):
        raise FinalAuditError("task-audit NOT_APPLICABLE requires derived justification")
    body = {
        "schema": 2,
        "check_id": check_id,
        "producer_id": producer_id.strip(),
        "status": status,
        "proof_digest": proof_digest,
        "evidence_record_ids": list(evidence_record_ids),
        "detail": detail.strip(),
        "justification": justification.strip() if isinstance(justification, str) else None,
    }
    body["observation_sha256"] = _digest(body)
    return body


_TASK_AUDIT_BINDING_FIELDS = (
    "implementation_digest",
    "diff_digest",
    "workspace_sha256",
    "compiled_contract_digest",
    "write_scope_digest",
    "governance_digest",
    "evidence_set_digest",
    "evidence_head",
    "tdd_cycle_digest",
    "review_receipt_digest",
    "falsification_receipt_digest",
    "test_law_baseline_digest",
    "gates_digest",
    "risks_digest",
    "decisions_digest",
    "child_history_digest",
    "verification_digest",
    "producer_registry_digest",
)


def build_task_audit_receipt(
    observations: list[dict],
    *,
    task_id: str,
    epoch: int,
    audited_at: str,
    **bindings: str,
) -> dict:
    if not isinstance(task_id, str) or not task_id.strip():
        raise FinalAuditError("task-audit receipt requires a task id")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise FinalAuditError("task-audit receipt requires a non-negative epoch")
    if not isinstance(audited_at, str) or not audited_at.strip():
        raise FinalAuditError("task-audit receipt requires an audit timestamp")
    missing = [field for field in _TASK_AUDIT_BINDING_FIELDS if field not in bindings]
    extra = sorted(set(bindings) - set(_TASK_AUDIT_BINDING_FIELDS))
    if missing or extra:
        raise FinalAuditError(
            "task-audit binding fields are not exact: missing="
            + ",".join(missing)
            + " extra="
            + ",".join(extra)
        )
    if any(not isinstance(bindings[field], str) or not _SHA256_RE.fullmatch(bindings[field]) for field in _TASK_AUDIT_BINDING_FIELDS):
        raise FinalAuditError("task-audit receipt bindings must be canonical sha256 values")
    if not isinstance(observations, list) or len(observations) != len(REQUIRED_FINAL_AUDIT_CHECKS):
        raise FinalAuditError(f"task audit requires exactly {len(REQUIRED_FINAL_AUDIT_CHECKS)} observations")
    validated: list[dict] = []
    for expected, item in zip(REQUIRED_FINAL_AUDIT_CHECKS, observations):
        if not isinstance(item, dict) or item.get("schema") != 2 or item.get("check_id") != expected:
            raise FinalAuditError("task-audit observations are missing, duplicated, or reordered")
        claimed = item.get("observation_sha256")
        if claimed != _digest({key: value for key, value in item.items() if key != "observation_sha256"}):
            raise FinalAuditError(f"task-audit observation seal is invalid: {expected}")
        # Re-run public construction validation without trusting the existing seal.
        seal_task_audit_observation(
            check_id=expected,
            producer_id=item.get("producer_id"),
            status=item.get("status"),
            proof_digest=item.get("proof_digest"),
            evidence_record_ids=item.get("evidence_record_ids"),
            detail=item.get("detail"),
            justification=item.get("justification"),
        )
        validated.append(json.loads(json.dumps(item)))
    producer_ids = [item["producer_id"] for item in validated]
    proof_digests = [item["proof_digest"] for item in validated]
    if len(set(producer_ids)) != len(validated):
        raise FinalAuditError("task-audit producer registry is not a bijection")
    if len(set(proof_digests)) != len(validated):
        raise FinalAuditError("task-audit checks do not have independent proof digests")
    counts = dict(sorted(Counter(item["status"] for item in validated).items()))
    body = {
        "schema": 2,
        "outcome": "PASS",
        "task_id": task_id.strip(),
        "epoch": epoch,
        "audited_at": audited_at.strip(),
        "authoritative_checks": list(REQUIRED_FINAL_AUDIT_CHECKS),
        "check_count": len(validated),
        "counts": counts,
        **{field: bindings[field] for field in _TASK_AUDIT_BINDING_FIELDS},
        "observations": validated,
    }
    body["receipt_sha256"] = _digest(body)
    return body


def validate_task_audit_receipt(
    receipt: object,
    *,
    task_id: str | None = None,
    epoch: int | None = None,
    **bindings: str | None,
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("schema") != 2:
        return False
    if receipt.get("receipt_sha256") != _digest(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    ):
        return False
    if receipt.get("outcome") != "PASS" or receipt.get("authoritative_checks") != list(REQUIRED_FINAL_AUDIT_CHECKS):
        return False
    if receipt.get("check_count") != len(REQUIRED_FINAL_AUDIT_CHECKS):
        return False
    try:
        rebuilt = build_task_audit_receipt(
            receipt.get("observations"),
            task_id=receipt.get("task_id"),
            epoch=receipt.get("epoch"),
            audited_at=receipt.get("audited_at"),
            **{field: receipt.get(field) for field in _TASK_AUDIT_BINDING_FIELDS},
        )
    except (FinalAuditError, TypeError):
        return False
    if rebuilt != receipt:
        return False
    expected: dict[str, object | None] = {"task_id": task_id, "epoch": epoch}
    expected.update(bindings)
    return all(value is None or receipt.get(field) == value for field, value in expected.items())
