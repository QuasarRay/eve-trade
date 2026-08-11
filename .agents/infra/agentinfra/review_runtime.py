from __future__ import annotations

"""Operational independent-review completion for one current candidate."""

import hashlib
import json
from pathlib import Path
import re

from .assurance import validate_falsification_receipt
from .evidence import load_evidence
from .review import (
    ReviewError,
    build_operational_review_receipt,
    review_handoff_digest,
    review_payload_digest,
    validate_review_payload,
)
from .state_store import StateStore


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_ROLES = ("adversarial", "security")
_DIRECT_PROVENANCE = {"framework-command", "verified-observation", "external-source"}
_CHALLENGE_FIELDS = (
    "assumptions_tested",
    "counterexamples_attempted",
    "boundary_cases",
    "potential_failures",
)


class ReviewRuntimeError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _artifact_digest(value: object, label: str) -> str:
    digest = value.get("digest", value.get("sha256")) if isinstance(value, dict) else None
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReviewRuntimeError(f"current {label} has no canonical digest")
    return digest


def _current_cycle(task: dict) -> dict:
    tdd = task.get("tdd")
    active = tdd.get("active_cycle_id") if isinstance(tdd, dict) else None
    cycles = tdd.get("cycles", []) if isinstance(tdd, dict) else []
    cycle = next(
        (
            item
            for item in cycles
            if isinstance(item, dict) and item.get("cycle_id") == active
        ),
        None,
    )
    if not isinstance(cycle, dict) or not _SHA256_RE.fullmatch(str(cycle.get("cycle_sha256", ""))):
        raise ReviewRuntimeError("review requires a current sealed TDD cycle")
    return cycle


def _payload_evidence_ids(payload: dict) -> list[str]:
    result: list[str] = []
    for field in _CHALLENGE_FIELDS:
        for item in payload[field]:
            result.extend(item["evidence_ids"])
    for finding in payload.get("findings", []):
        if isinstance(finding, dict):
            values = finding.get("evidence_ids", [])
            if isinstance(values, list):
                result.extend(value for value in values if isinstance(value, str))
    return list(dict.fromkeys(result))


def complete(
    root: Path,
    *,
    lease_id: str,
    task_id: str | None = None,
) -> dict:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") != "ADVERSARIAL_REVIEW":
        raise ReviewRuntimeError("review completion requires task state ADVERSARIAL_REVIEW")
    if task.get("active_child") is not None:
        raise ReviewRuntimeError("review completion requires a closed reviewer lease")
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise ReviewRuntimeError("review completion requires a reviewer lease id")

    matches = [
        item
        for item in task.get("child_history", [])
        if isinstance(item, dict) and item.get("lease_id", item.get("handoff_id")) == lease_id
    ]
    if len(matches) != 1:
        raise ReviewRuntimeError("review lease does not identify exactly one closed handoff")
    handoff = matches[0]
    role = str(handoff.get("role", ""))
    if not any(marker in role.casefold() for marker in _REVIEW_ROLES):
        raise ReviewRuntimeError("closed handoff is not an adversarial/security review")
    if handoff.get("outcome") != "accepted":
        raise ReviewRuntimeError("closed review handoff was not accepted")
    if handoff.get("schema") != 2:
        raise ReviewRuntimeError("closed review handoff lacks structured schema-2 provenance")
    if handoff.get("handoff_sha256") != review_handoff_digest(handoff):
        raise ReviewRuntimeError("closed review handoff integrity failure")

    context = handoff.get("context_brief")
    if not isinstance(context, dict):
        raise ReviewRuntimeError("closed review handoff lacks its bounded context brief")
    context_sha = hashlib.sha256(_canonical(context)).hexdigest()
    if handoff.get("context_brief_sha256") != context_sha:
        raise ReviewRuntimeError("review context brief integrity failure")
    if context.get("task_id") != task["id"] or context.get("change_epoch") != task["change_epoch"]:
        raise ReviewRuntimeError("review context is stale or belongs to another task")

    try:
        payload = validate_review_payload(handoff.get("review_payload"))
    except ReviewError as exc:
        raise ReviewRuntimeError(str(exc)) from exc
    if handoff.get("review_payload_sha256") != review_payload_digest(payload):
        raise ReviewRuntimeError("structured review payload integrity failure")
    if payload.get("outcome") != "ACCEPTED":
        raise ReviewRuntimeError("structured review did not accept the current candidate")

    records = load_evidence(store._task_dir(task["id"]), verify=True)
    by_id = {record["id"]: record for record in records}
    ledger_head = records[-1]["record_sha256"] if records else None
    evidence_set_digest = hashlib.sha256(
        b"".join(record["record_sha256"].encode("ascii") for record in records)
    ).hexdigest()
    if ledger_head != task.get("evidence_head") or evidence_set_digest != task.get("evidence_set_digest"):
        raise ReviewRuntimeError("review cannot bind an unanchored evidence ledger")

    payload_ids = _payload_evidence_ids(payload)
    context_ids = {
        item.get("id")
        for item in context.get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    handoff_ids = set(handoff.get("evidence", []))
    if not set(payload_ids).issubset(context_ids) or not set(payload_ids).issubset(handoff_ids):
        raise ReviewRuntimeError("structured review cites evidence outside its bounded context/handoff")
    for evidence_id in payload_ids:
        record = by_id.get(evidence_id)
        if (
            record is None
            or record.get("schema") != 2
            or record.get("task_id") != task["id"]
            or record.get("change_epoch") != task["change_epoch"]
            or record.get("provenance") not in _DIRECT_PROVENANCE
        ):
            raise ReviewRuntimeError(f"structured review evidence is missing, manual, foreign, or stale: {evidence_id}")

    cycle = _current_cycle(task)
    falsification = task.get("falsification")
    if not isinstance(falsification, dict) or falsification.get("schema") != 2 or not validate_falsification_receipt(
        falsification,
        task_id=task["id"],
        current_tdd_cycle_digest=cycle["cycle_sha256"],
        current_epoch=task["change_epoch"],
        current_diff_digest=task.get("diff_digest"),
        require_clean=True,
    ):
        raise ReviewRuntimeError("review requires current execution-backed clean falsification")
    falsification_digest = falsification.get("receipt_sha256")
    if not isinstance(falsification_digest, str) or not _SHA256_RE.fullmatch(falsification_digest):
        raise ReviewRuntimeError("current falsification receipt is not sealed")

    receipt = build_operational_review_receipt(
        task_id=task["id"],
        reviewer_lease=lease_id,
        reviewer_role=role,
        reviewed_at=str(handoff.get("closed", "")),
        epoch=task["change_epoch"],
        diff_digest=str(task.get("diff_digest", "")),
        requirements_digest=_artifact_digest(task.get("precheck", {}).get("compiled_policy"), "compiled policy"),
        evidence_set_digest=evidence_set_digest,
        tdd_cycle_digest=cycle["cycle_sha256"],
        test_law_baseline_digest=_artifact_digest(
            task.get("precheck", {}).get("test_law_baseline"), "test/law baseline"
        ),
        falsification_receipt_digest=falsification_digest,
        context_brief_sha256=context_sha,
        handoff_sha256=handoff["handoff_sha256"],
        workflow_independence="WORKFLOW_INDEPENDENT",
        identity_attestation="UNATTESTED_IDENTITY",
        payload=payload,
    )
    updated = store.record_review(receipt, task["id"])
    return {
        "task_id": task["id"],
        "receipt": receipt,
        "task_revision": updated["revision"],
    }
