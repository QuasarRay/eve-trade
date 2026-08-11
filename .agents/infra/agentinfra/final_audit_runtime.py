from __future__ import annotations

"""Framework-produced, check-specific FINAL_AUDIT execution."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .assurance import validate_falsification_receipt
from .evidence import load_evidence
from .final_audit import (
    REQUIRED_FINAL_AUDIT_CHECKS,
    build_task_audit_receipt,
    non_write_audit_binding_digest,
    seal_task_audit_observation,
)
from .governance import verify_governance
from .locks import LeaseLock
from .paths import leases_dir
from .review import review_handoff_digest, validate_review_receipt
from .security import SECRET_VALUE_PATTERNS, TEST_DETECTION_NAME, is_path_redirect
from .tdd_runtime import _current_snapshots, _matches_any, _task_cycle, _verify_sealed, green_current
from .workspace import workspace_fingerprint


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCKFILES = {
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_DEPENDENCY_FILES = _LOCKFILES | {
    "cargo.toml",
    "composer.json",
    "gemfile",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}


class FinalAuditRuntimeError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _artifact_digest(value: object, label: str) -> str:
    digest = value.get("digest", value.get("sha256")) if isinstance(value, dict) else None
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise FinalAuditRuntimeError(f"{label} has no canonical digest")
    return digest


def _artifact_payload(value: object, kind: str) -> object | None:
    if not isinstance(value, dict) or value.get("kind") != kind or "payload" not in value:
        return None
    body = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != _digest(body):
        raise FinalAuditRuntimeError(f"{kind} artifact integrity failure")
    return value["payload"]


def _entries(snapshot: object) -> dict[str, dict]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("entries"), list):
        return {}
    return {
        item["path"]: item
        for item in snapshot["entries"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def _changed_paths(before: object, after: object) -> list[str]:
    left = _entries(before)
    right = _entries(after)
    return sorted(
        path
        for path in set(left) | set(right)
        if left.get(path) != right.get(path)
    )


def _ok(detail: str, **facts: object) -> dict:
    return {"status": "PROVEN", "detail": detail, "justification": None, "facts": facts}


def _na(detail: str, justification: str, **facts: object) -> dict:
    return {
        "status": "NOT_APPLICABLE",
        "detail": detail,
        "justification": justification,
        "facts": facts,
    }


def _require(condition: object, message: str) -> None:
    if not condition:
        raise FinalAuditRuntimeError(message)


def _command_matches(required: list[str], observed: list[str]) -> bool:
    if not required or len(observed) < len(required):
        return False
    first_required = Path(required[0]).name.casefold().removesuffix(".exe")
    first_observed = Path(observed[0]).name.casefold().removesuffix(".exe")
    if first_required != first_observed:
        # A contract may use the portable ``python`` name while the selected
        # interpreter has a versioned basename.
        if not (first_required == "python" and first_observed.startswith("python")):
            return False
    return observed[1 : len(required)] == required[1:]


class _Context:
    def __init__(self, store, task: dict, records: list[dict], workspace: dict):
        self.store = store
        self.root = store.root
        self.task = task
        self.records = records
        self.by_id = {record["id"]: record for record in records}
        self.workspace = workspace
        self.precheck = task.get("precheck", {})
        self.compiled = _verify_sealed(self.precheck.get("compiled_policy"), "compiled policy")
        self.scope = _verify_sealed(self.precheck.get("write_scope"), "compiled write scope")
        self.non_write = task.get("mode") == "read"
        if self.non_write:
            self.cycle = {}
            self.current_snapshots = {}
            self.design = {}
            self.baseline = {}
            self.green = {}
            self.changed_production = []
        else:
            self.cycle = _task_cycle(task)
            self.current_snapshots = _current_snapshots(self.root, self.cycle)
            self.design = self.cycle.get("design_snapshots", {})
            self.baseline = self.cycle.get("baseline_snapshots", {})
            self.green = self.cycle.get("green_snapshots", {})
            self.changed_production = _changed_paths(
                self.design.get("production"), self.current_snapshots.get("production")
            )
        self.verification_records = [
            self.by_id[evidence_id]
            for evidence_id in task.get("verification_evidence", [])
            if evidence_id in self.by_id
        ]
        self.governance = verify_governance(self.root, self.precheck.get("governance_snapshot"))


def _read_workspace_current(c: _Context) -> bool:
    baseline = c.precheck.get("workspace_snapshot", {})
    return bool(
        c.non_write
        and isinstance(baseline, dict)
        and baseline.get("available") is True
        and baseline.get("sha256") == c.workspace.get("sha256")
        and c.task.get("implementation_digest") == c.workspace.get("sha256")
        and c.task.get("diff_digest") == c.workspace.get("sha256")
    )


def _non_write_na(check: str) -> dict:
    return _na(
        f"{check} is not applicable to a read-only task",
        "The task has no behavioral production mutation; Aegis did not fabricate a TDD, GREEN, review-receipt, or falsification receipt.",
        task_mode="read",
    )


def _p_expected_repository_root(c: _Context) -> dict:
    snapshot = c.precheck.get("governance_snapshot", {})
    _require(Path(str(snapshot.get("project_root", ""))).resolve() == c.root, "precheck repository root is stale")
    _require((c.root / ".agents" / "framework.toml").is_file(), "expected framework marker is absent")
    return _ok("governance snapshot and framework marker identify the selected root", root=str(c.root))


def _p_boundary_snapshot_current(c: _Context) -> dict:
    if c.non_write:
        _require(_read_workspace_current(c), "read-task workspace changed after PRECHECK/VERIFY")
        return _ok(
            "read-only candidate matches both PRECHECK and independently repeated VERIFY workspace observations",
            workspace=c.workspace["sha256"],
        )
    _require(green_current(c.root, c.task), "compiled boundary snapshots are not current")
    return _ok(
        "current production/test/oracle surfaces match the sealed GREEN boundary snapshots",
        production=c.current_snapshots["production"]["digest"],
        tests=c.current_snapshots["tests"]["digest"],
        oracles=c.current_snapshots["oracles"]["digest"],
    )


def _p_governance_digest_unchanged(c: _Context) -> dict:
    return _ok("governance was recaptured and exactly matches PRECHECK", **c.governance)


def _p_no_agents_mutation(c: _Context) -> dict:
    entries = c.precheck["governance_snapshot"].get("entries", [])
    protected = [item for item in entries if item.get("path") == ".agents" or str(item.get("path", "")).startswith(".agents/")]
    _require(protected, "PRECHECK did not inventory deployed .agents governance")
    return _ok("the independently recaptured .agents inventory is unchanged", entry_count=len(protected), digest=c.governance["digest"])


def _p_no_governing_instruction_mutation(c: _Context) -> dict:
    entries = c.precheck["governance_snapshot"].get("entries", [])
    instructions = [item for item in entries if str(item.get("path", "")).casefold().endswith("agents.md")]
    return _ok("all governing instruction entries remain in the exact governance snapshot", entry_count=len(instructions), digest=c.governance["digest"])


def _p_no_unexpected_nested_repository_mutation(c: _Context) -> dict:
    expected = sorted(str(item).replace("\\", "/").strip("/") for item in c.scope.get("nested_repositories", []))
    observed: list[str] = []
    for marker in c.root.rglob(".git"):
        relative = marker.relative_to(c.root).as_posix()
        if relative == ".git" or relative.startswith((".aegis/", ".agents/")):
            continue
        observed.append(marker.parent.relative_to(c.root).as_posix())
    _require(sorted(observed) == expected, "nested repository topology differs from compiled scope")
    return _ok("nested repository topology exactly matches compiled scope", repositories=expected)


def _p_no_overwritten_user_owned_dirty_file(c: _Context) -> dict:
    patterns = list(c.scope.get("user_dirty", []))
    if not patterns:
        return _na("compiled scope contains no user-owned dirty paths", "No user-owned dirty path was present at PRECHECK", patterns=[])
    if c.non_write:
        _require(_read_workspace_current(c), "read task changed the PRECHECK workspace containing user-owned dirty files")
        return _ok("read-only task preserved the entire PRECHECK workspace including user-owned dirty files", patterns=patterns, workspace=c.workspace["sha256"])
    designed = c.design.get("user_dirty", {})
    current = c.current_snapshots.get("user_dirty", {})
    _require(designed.get("digest") == current.get("digest"), "user-owned dirty content changed after TEST_DESIGN")
    return _ok("user-owned dirty snapshot remains byte-identical", patterns=patterns, digest=current.get("digest"))


def _p_no_unexpected_generated_churn(c: _Context) -> dict:
    patterns = list(c.scope.get("generated_paths", []))
    if not patterns:
        return _na("compiled scope declares no generated surface", "Generated-churn semantics are absent from this task", patterns=[])
    if c.non_write:
        _require(_read_workspace_current(c), "read task changed generated content after PRECHECK")
        return _ok("read-only task preserved the complete PRECHECK workspace including generated paths", patterns=patterns, workspace=c.workspace["sha256"])
    raise FinalAuditRuntimeError("generated surface exists but the TDD cycle has no generated-content baseline")


def _p_no_unexpected_lockfile_change(c: _Context) -> dict:
    changed = [path for path in c.changed_production if Path(path).name.casefold() in _LOCKFILES]
    budget = c.compiled.get("change_budget", {}).get("lockfile_churn", 0)
    _require(len(changed) <= int(budget or 0), "lockfile changes exceed compiled change authority")
    if not changed:
        return _na("no changed production path is a lockfile", "Lockfile churn is semantically absent", changed=[])
    return _ok("changed lockfiles remain within the compiled lockfile budget", changed=changed, budget=budget)


def _p_no_unauthorized_dependency_change(c: _Context) -> dict:
    changed = [path for path in c.changed_production if Path(path).name.casefold() in _DEPENDENCY_FILES]
    budget = c.compiled.get("semantic_budget", {}).get("dependencies", 0)
    _require(len(changed) <= int(budget or 0), "dependency changes exceed compiled semantic authority")
    if not changed:
        return _na("no changed production path is a dependency declaration", "Dependency mutation is semantically absent", changed=[])
    return _ok("dependency declarations changed only within compiled authority", changed=changed, budget=budget)


def _current_test_law_entries(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or ".aegis" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root)
        parts = {part.casefold() for part in relative.parts}
        if (path.name.startswith("test_") and path.suffix == ".py") or bool(parts & {"tests", "law_tests", "tests-to-impl", "laws"}):
            found[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _p_no_unauthorized_test_law_mutation(c: _Context) -> dict:
    if c.non_write:
        _require(_read_workspace_current(c), "read task changed test/law content after PRECHECK")
        return _ok("read-only task preserved every PRECHECK workspace file including tests and laws", workspace=c.workspace["sha256"])
    _require(
        c.current_snapshots["tests"]["digest"] == c.design["tests"]["digest"]
        and c.current_snapshots["oracles"]["digest"] == c.design["oracles"]["digest"],
        "frozen acceptance test/oracle content changed",
    )
    baseline = _artifact_payload(c.precheck.get("test_law_baseline"), "test-law-baseline")
    if isinstance(baseline, dict) and isinstance(baseline.get("entries"), list):
        before = {item["path"]: item["sha256"] for item in baseline["entries"] if isinstance(item, dict)}
        after = _current_test_law_entries(c.root)
        allowed = set(c.cycle.get("surface_contract", {}).get("test_paths", [])) | set(c.cycle.get("surface_contract", {}).get("oracle_paths", []))
        unauthorized = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path) and path not in allowed)
        _require(not unauthorized, "test/law files outside the frozen task contract changed: " + ", ".join(unauthorized))
        return _ok("test/law inventory changed only through the frozen task contract", allowed=sorted(allowed), baseline_count=len(before))
    return _ok("the current task's exact test and oracle contract remains frozen", tests=c.design["tests"]["digest"], oracles=c.design["oracles"]["digest"], inventory_detail="minimal precheck artifact")


def _p_no_reference_mutation(c: _Context) -> dict:
    references = list(c.scope.get("reference_paths", []))
    if not references:
        return _na("compiled scope declares no reference paths", "Reference immutability is semantically absent", paths=[])
    if c.non_write:
        _require(_read_workspace_current(c), "read task changed reference content after PRECHECK")
        return _ok("read-only task preserved the complete PRECHECK workspace including references", patterns=references, workspace=c.workspace["sha256"])
    explicit = set(c.cycle.get("surface_contract", {}).get("oracle_paths", []))
    _require(all(any(_matches_any(path, [pattern]) for path in explicit) for pattern in references), "compiled references are not fully represented by the frozen oracle snapshot")
    _require(c.current_snapshots["oracles"]["digest"] == c.design["oracles"]["digest"], "reference/oracle content changed")
    return _ok("all compiled references are present in the frozen oracle snapshot", patterns=references, digest=c.design["oracles"]["digest"])


def _p_write_scope_respected(c: _Context) -> dict:
    if c.non_write:
        _require(_read_workspace_current(c), "read task mutated the workspace")
        return _ok("read-only task made no workspace mutation between PRECHECK and final audit", changed=[])
    production = list(c.scope.get("production_paths", []))
    allow = list(c.scope.get("allow", []))
    deny = list(c.scope.get("deny", []))
    invalid = [
        path for path in c.changed_production
        if not _matches_any(path, production) or not _matches_any(path, allow) or _matches_any(path, deny)
    ]
    _require(not invalid, "production changes escaped compiled write scope: " + ", ".join(invalid))
    return _ok("every observed production change is inside allow/production and outside deny", changed=c.changed_production, production_patterns=production)


def _p_change_budget_respected_or_replanned(c: _Context) -> dict:
    budget = c.compiled.get("change_budget", {})
    file_limit = budget.get("files") if isinstance(budget, dict) else None
    if file_limit is None:
        return _na("compiled policy has no finite changed-file budget", "No numeric changed-file limit applies", changed_files=len(c.changed_production))
    _require(len(c.changed_production) <= int(file_limit), "changed-file count exceeds compiled budget")
    return _ok("changed-file count is within the compiled budget", changed_files=len(c.changed_production), limit=file_limit)


def _p_semantic_budget_respected_or_replanned(c: _Context) -> dict:
    budget = c.compiled.get("semantic_budget", {})
    sensitive = [path for path in c.changed_production if Path(path).name.casefold() in _DEPENDENCY_FILES]
    _require(not sensitive or int(budget.get("dependencies", 0)) >= len(sensitive), "dependency semantic budget exceeded")
    return _ok("observable semantic-budget categories remain within compiled limits", dependency_changes=sensitive, semantic_budget=budget)


def _p_all_hard_gates_proven(c: _Context) -> dict:
    hard = [gate for gate in c.task.get("gates", []) if gate.get("gate_severity") == "HARD"]
    if c.non_write and not hard:
        return _na("read-only task has no HARD acceptance gate", "No behavioral write gate applies; direct verification evidence remains mandatory", gate_ids=[])
    _require(hard and all(gate.get("status") == "PROVEN" for gate in hard), "not every HARD gate is proven")
    return _ok("every HARD gate has current proof evidence", gate_ids=[gate["id"] for gate in hard])


def _p_all_required_gates_proven_or_externally_waived(c: _Context) -> dict:
    required = [gate for gate in c.task.get("gates", []) if gate.get("gate_severity") == "REQUIRED"]
    _require(all(gate.get("status") in {"PROVEN", "WAIVED"} for gate in required), "a REQUIRED gate is unresolved")
    if not required:
        return _na("task has no REQUIRED-severity gate", "No REQUIRED gate requires proof or external waiver", gate_ids=[])
    return _ok("every REQUIRED gate is proven or externally waived", gate_ids=[gate["id"] for gate in required])


def _p_advisory_omissions_justified(c: _Context) -> dict:
    advisory = [gate for gate in c.task.get("gates", []) if gate.get("gate_severity") == "ADVISORY"]
    if not advisory:
        return _na("task has no ADVISORY gate", "No advisory omission exists", gate_ids=[])
    unresolved = [gate["id"] for gate in advisory if gate.get("status") not in {"PROVEN", "WAIVED"} and not str(gate.get("advisory_justification", "")).strip()]
    _require(not unresolved, "advisory omissions lack justification: " + ", ".join(unresolved))
    return _ok("every omitted advisory gate has explicit justification", gate_ids=[gate["id"] for gate in advisory])


def _p_mandatory_gate_families_present(c: _Context) -> dict:
    if c.non_write:
        return _na("compiled behavioral gate families do not apply to a read-only task", "The task made no behavioral production mutation", task_mode="read")
    expected = {item.get("id") for item in c.compiled.get("gates", []) if isinstance(item, dict)}
    actual = {item.get("id") for item in c.task.get("gates", []) if isinstance(item, dict)}
    _require(expected and expected.issubset(actual), "compiled mandatory gate inventory is not present in task state")
    return _ok("task gate inventory contains every compiled mandatory gate", expected=sorted(expected), actual=sorted(actual))


def _p_every_behavioral_production_change_has_tdd_cycle(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("behavioral production TDD cycle")
    _require(c.task.get("mode") != "write" or (c.changed_production and c.cycle.get("authority_source") == "FRAMEWORK_OBSERVED"), "behavioral production change lacks a framework-observed TDD cycle")
    return _ok("the current production change is bound to the active trusted cycle", changed=c.changed_production, cycle=c.cycle["cycle_id"])


def _transition_revision(task: dict, target: str) -> int | None:
    values = [item.get("revision") for item in task.get("transitions", []) if item.get("to") == target and isinstance(item.get("revision"), int)]
    return values[-1] if values else None


def _p_test_design_predates_implementation(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("TEST_DESIGN chronology")
    implementation = _transition_revision(c.task, "IMPLEMENT") or _transition_revision(c.task, "REMEDIATE")
    designed = c.cycle.get("designed_at_revision")
    _require(isinstance(designed, int) and isinstance(implementation, int) and designed < implementation, "TEST_DESIGN does not predate implementation authority")
    return _ok("sealed test design predates implementation transition", designed_revision=designed, implementation_revision=implementation)


def _p_baseline_execution_predates_implementation(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("baseline/implementation chronology")
    implementation = _transition_revision(c.task, "IMPLEMENT") or _transition_revision(c.task, "REMEDIATE")
    record = c.by_id.get(c.cycle.get("baseline_evidence_id"))
    observed_revision = record.get("details", {}).get("task_revision") if record else None
    _require(isinstance(observed_revision, int) and isinstance(implementation, int) and observed_revision < implementation, "baseline execution does not predate implementation transition")
    return _ok("framework baseline evidence predates implementation transition", baseline_revision=observed_revision, implementation_revision=implementation)


def _baseline_record(c: _Context) -> dict:
    record = c.by_id.get(c.cycle.get("baseline_evidence_id"))
    _require(isinstance(record, dict), "baseline evidence record is missing")
    return record


def _green_record(c: _Context) -> dict:
    record = c.by_id.get(c.cycle.get("green_evidence_id"))
    _require(isinstance(record, dict), "GREEN evidence record is missing")
    return record


def _p_required_red_legitimate_and_current(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("behavioral RED")
    if c.cycle.get("mode") != "RED_REQUIRED":
        return _na("task mode does not require behavioral RED", "The compiled TDD mode is not RED_REQUIRED", mode=c.cycle.get("mode"))
    record = _baseline_record(c)
    details = record.get("details", {})
    observation = details.get("observation", {})
    _require(details.get("classification") == "EXPECTED_BEHAVIORAL_RED" and observation.get("relevant_test_executed") is True and observation.get("assertion_boundary_reached") is True, "baseline is not a legitimate relevant semantic RED")
    return _ok("RED was independently observed at the intended assertion boundary", evidence_id=record["id"], classification=details.get("classification"))


def _p_characterization_present_where_required(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("write-task characterization")
    if c.cycle.get("mode") != "CHARACTERIZATION_REQUIRED":
        return _na("compiled mode does not require characterization", "Characterization is not the selected TDD mode", mode=c.cycle.get("mode"))
    record = _baseline_record(c)
    _require(record.get("details", {}).get("classification") == "CHARACTERIZATION_PASS", "required characterization was not observed")
    return _ok("required characterization execution is present", evidence_id=record["id"])


def _p_test_contract_frozen(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("frozen behavioral test contract")
    _require(c.cycle.get("test_contract_digest") == c.cycle.get("frozen_test_contract_digest") and c.current_snapshots["tests"]["digest"] == c.design["tests"]["digest"], "test contract is not frozen")
    return _ok("current test bytes equal the frozen baseline contract", digest=c.cycle["test_contract_digest"])


def _p_oracle_frozen(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("frozen behavioral oracle")
    _require(c.cycle.get("oracle_digest") == c.cycle.get("frozen_oracle_digest") and c.current_snapshots["oracles"]["digest"] == c.design["oracles"]["digest"], "oracle contract is not frozen")
    return _ok("current oracle bytes equal the frozen baseline oracle", digest=c.cycle["oracle_digest"])


def _p_green_same_frozen_contract(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("behavioral GREEN contract identity")
    record = _green_record(c)
    event = next((item for item in c.cycle.get("events", []) if item.get("status") == "GREEN_PROVEN"), None)
    _require(isinstance(event, dict) and event.get("test_contract_digest") == c.cycle.get("frozen_test_contract_digest") and event.get("oracle_digest") == c.cycle.get("frozen_oracle_digest"), "GREEN did not execute the frozen contract")
    return _ok("GREEN event binds the exact frozen test and oracle digests", evidence_id=record["id"], event_digest=_digest(event))


def _p_green_current_implementation_epoch(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("behavioral GREEN epoch")
    _require(green_current(c.root, c.task) and c.cycle.get("green_epoch") == c.task.get("change_epoch"), "GREEN is stale for current implementation epoch")
    return _ok("GREEN matches current production, diff, and epoch", epoch=c.task["change_epoch"], implementation=c.task["implementation_digest"], diff=c.task["diff_digest"])


def _p_verification_current(c: _Context) -> dict:
    _require(c.verification_records and c.task.get("verification_epoch") == c.task.get("change_epoch"), "verification evidence is absent or stale")
    allowed = {"framework-command", "verified-observation", "external-source"} if c.non_write else {"framework-command"}
    bad = []
    for record in c.verification_records:
        command = record.get("details", {}).get("command")
        if (
            record.get("provenance") not in allowed
            or record.get("details", {}).get("workspace", {}).get("sha256") != c.workspace.get("sha256")
            or (record.get("provenance") == "framework-command" and (not isinstance(command, dict) or command.get("success") is not True))
        ):
            bad.append(record["id"])
    _require(not bad, "verification evidence is failed, manual, or workspace-stale: " + ", ".join(bad))
    return _ok("direct verification evidence is current and independently provenance-bound", evidence_ids=[item["id"] for item in c.verification_records], workspace=c.workspace["sha256"], allowed_provenance=sorted(allowed))


def _read_review_handoffs(c: _Context) -> list[dict]:
    return [
        item for item in c.task.get("child_history", [])
        if isinstance(item, dict)
        and item.get("schema") == 2
        and item.get("outcome") == "accepted"
        and any(marker in str(item.get("role", "")).casefold() for marker in ("review", "verifier", "security", "adversarial"))
        and item.get("handoff_sha256") == review_handoff_digest(item)
        and isinstance(item.get("context_brief"), dict)
        and item["context_brief"].get("task_id") == c.task["id"]
        and item["context_brief"].get("change_epoch") == c.task["change_epoch"]
    ]


def _p_review_current(c: _Context) -> dict:
    if c.non_write:
        if c.task.get("risk") not in {"high", "critical"}:
            return _non_write_na("independent behavioral-write review receipt")
        matches = _read_review_handoffs(c)
        _require(matches, "high/critical read task lacks a current closed independent review handoff")
        return _ok("high/critical read task has a current integrity-bound closed review handoff", lease_id=matches[-1]["lease_id"], role=matches[-1]["role"])
    c.store._validate_bound_review(c.task, c.records)
    receipt = c.task["review_receipt"]
    return _ok("schema-2 review receipt is current for candidate and falsification", receipt=receipt["receipt_sha256"], identity_attestation=receipt["identity_attestation"])


def _p_review_lease_closed(c: _Context) -> dict:
    if c.non_write:
        if c.task.get("risk") not in {"high", "critical"}:
            return _non_write_na("behavioral-write reviewer lease")
        matches = _read_review_handoffs(c)
        _require(len(matches) >= 1 and c.task.get("active_child") is None, "read-task reviewer lease is not closed")
        return _ok("read-task independent reviewer lease is closed", lease_id=matches[-1]["lease_id"], closed=matches[-1].get("closed"))
    receipt = c.task.get("review_receipt", {})
    matches = [item for item in c.task.get("child_history", []) if item.get("lease_id") == receipt.get("reviewer_lease") and item.get("outcome") == "accepted"]
    _require(len(matches) == 1 and c.task.get("active_child") is None, "review lease is not uniquely closed")
    return _ok("review receipt names one accepted closed lease", lease_id=receipt.get("reviewer_lease"), closed=matches[0].get("closed"))


def _p_falsification_recorded(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("execution-backed behavioral falsification")
    receipt = c.task.get("falsification")
    _require(isinstance(receipt, dict) and receipt.get("schema") == 2 and validate_falsification_receipt(receipt, task_id=c.task["id"], current_tdd_cycle_digest=c.cycle["cycle_sha256"], current_epoch=c.task["change_epoch"], current_diff_digest=c.task["diff_digest"], require_clean=True), "execution-backed falsification is missing or stale")
    return _ok("all compiled falsification families have current executed attempts", families=receipt["required_families"], receipt=receipt["receipt_sha256"])


def _p_no_unresolved_blocking_finding(c: _Context) -> dict:
    receipt = c.task.get("review_receipt") or {}
    blocking = [item for item in receipt.get("findings", []) if isinstance(item, dict) and item.get("severity") in {"HARD", "CRITICAL", "HIGH"} and item.get("status", "OPEN") != "RESOLVED"]
    risks = [item["id"] for item in c.task.get("risks", []) if item.get("severity") in {"high", "critical"} and item.get("status") != "resolved"]
    _require(not blocking and not risks, "unresolved blocking review finding or risk remains")
    return _ok("review findings and task risks contain no unresolved blocker", finding_count=len(receipt.get("findings", [])), risk_count=len(c.task.get("risks", [])))


def _p_required_commands_executed(c: _Context) -> dict:
    if c.non_write:
        return _na("compiled behavioral command matrix does not apply to the read-only task", "The material claim is proven by current direct external/verified evidence, not a fabricated command execution", task_mode="read")
    matrix = c.compiled.get("commands", {})
    required = [command for commands in matrix.values() if isinstance(commands, list) for command in commands if isinstance(command, list) and all(isinstance(item, str) for item in command)] if isinstance(matrix, dict) else []
    successful = [record.get("details", {}).get("command", {}).get("argv", []) for record in c.records if record.get("provenance") == "framework-command" and record.get("details", {}).get("command", {}).get("success") is True]
    missing = [command for command in required if not any(_command_matches(command, observed) for observed in successful)]
    _require(required and not missing, "required compiled commands lack successful execution evidence")
    return _ok("every compiled command prefix has successful framework evidence", required=required, successful_matches=len(required))


def _changed_production_text(c: _Context) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for relative in c.changed_production:
        path = c.root / relative
        if not path.is_file() or is_path_redirect(path) or path.stat().st_size > 1_000_000:
            continue
        try:
            result.append((relative, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return result


def _p_no_secrets_introduced(c: _Context) -> dict:
    hits: list[str] = []
    for relative, text in _changed_production_text(c):
        if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS):
            hits.append(relative)
    _require(not hits, "secret scanner found secret-like material in changed production: " + ", ".join(hits))
    return _ok("secret scanner found no secret-like material in changed production", scanned=c.changed_production)


def _p_no_suspicious_test_aware_behavior(c: _Context) -> dict:
    hits = [relative for relative, text in _changed_production_text(c) if TEST_DETECTION_NAME.search(text)]
    _require(not hits, "changed production contains test-aware behavior markers: " + ", ".join(hits))
    return _ok("changed production contains no test-detection marker", scanned=c.changed_production)


def _p_no_fake_red(c: _Context) -> dict:
    if c.non_write:
        return _non_write_na("fake-RED detection")
    record = _baseline_record(c)
    observation = record.get("details", {}).get("observation", {})
    observer = observation.get("observer", {}) if isinstance(observation, dict) else {}
    _require(record.get("provenance") == "framework-command" and observer.get("relevant_collected") is True and observer.get("relevant_started") is True and observer.get("relevant_completed") is True, "RED/characterization lacks executed relevant-test identity")
    return _ok("baseline evidence proves collection, start, completion, and semantic classification", evidence_id=record["id"], observation_digest=_digest(observation))


def _p_no_fabricated_capability_claim(c: _Context) -> dict:
    if c.non_write:
        return _na("no falsification capability or reviewer identity claim is present", "The read-only task uses direct verification evidence and makes no behavioral capability/identity claim", task_mode="read")
    falsification = c.task.get("falsification", {})
    invalid = []
    for attempt in falsification.get("attempts", []):
        record = c.by_id.get(attempt.get("evidence_id"))
        if record is None or record.get("provenance") != "framework-command" or record.get("details", {}).get("capability_status") != attempt.get("capability_status"):
            invalid.append(attempt.get("attempt_id"))
    review = c.task.get("review_receipt", {})
    _require(not invalid and review.get("identity_attestation") in {"UNATTESTED_IDENTITY", "HOST_ATTESTED_IDENTITY"}, "capability/identity claim lacks its observation boundary")
    return _ok("capability claims match executed attempts and review identity is explicitly calibrated", attempt_ids=[item.get("attempt_id") for item in falsification.get("attempts", [])], identity=review.get("identity_attestation"))


def _p_final_workspace_fingerprint_recorded(c: _Context) -> dict:
    _require(c.workspace.get("available") is True and c.workspace.get("sha256") == c.task.get("diff_digest"), "final workspace fingerprint is unavailable or differs from candidate")
    return _ok("stable final workspace fingerprint is available and candidate-bound", kind=c.workspace.get("kind"), sha256=c.workspace["sha256"])


def _p_compiled_task_contract_current(c: _Context) -> dict:
    if c.non_write:
        _require(c.scope.get("governance_digest") == c.precheck.get("governance_snapshot", {}).get("digest"), "compiled read-task scope is not bound to current governance")
        _require(_read_workspace_current(c), "read-task compiled contract is workspace-stale")
        return _ok("compiled policy, read-only scope, governance, and candidate workspace agree", compiled=c.compiled["digest"], scope=c.scope["digest"], governance=c.governance["digest"], workspace=c.workspace["sha256"])
    _require(c.cycle.get("compiled_contract_digest") == c.compiled.get("digest") and c.cycle.get("write_scope_digest") == c.scope.get("digest") and c.cycle.get("governance_digest") == c.precheck.get("governance_snapshot", {}).get("digest"), "active TDD cycle is not bound to current compiled contract/scope/governance")
    return _ok("compiled policy, write scope, governance, and active cycle agree", compiled=c.compiled["digest"], scope=c.scope["digest"], governance=c.cycle["governance_digest"])


FINAL_AUDIT_PRODUCERS = {
    "expected_repository_root": _p_expected_repository_root,
    "boundary_snapshot_current": _p_boundary_snapshot_current,
    "governance_digest_unchanged": _p_governance_digest_unchanged,
    "no_agents_mutation": _p_no_agents_mutation,
    "no_governing_instruction_mutation": _p_no_governing_instruction_mutation,
    "no_unexpected_nested_repository_mutation": _p_no_unexpected_nested_repository_mutation,
    "no_overwritten_user_owned_dirty_file": _p_no_overwritten_user_owned_dirty_file,
    "no_unexpected_generated_churn": _p_no_unexpected_generated_churn,
    "no_unexpected_lockfile_change": _p_no_unexpected_lockfile_change,
    "no_unauthorized_dependency_change": _p_no_unauthorized_dependency_change,
    "no_unauthorized_test_law_mutation": _p_no_unauthorized_test_law_mutation,
    "no_reference_mutation": _p_no_reference_mutation,
    "write_scope_respected": _p_write_scope_respected,
    "change_budget_respected_or_replanned": _p_change_budget_respected_or_replanned,
    "semantic_budget_respected_or_replanned": _p_semantic_budget_respected_or_replanned,
    "all_hard_gates_proven": _p_all_hard_gates_proven,
    "all_required_gates_proven_or_externally_waived": _p_all_required_gates_proven_or_externally_waived,
    "advisory_omissions_justified": _p_advisory_omissions_justified,
    "mandatory_gate_families_present": _p_mandatory_gate_families_present,
    "every_behavioral_production_change_has_tdd_cycle": _p_every_behavioral_production_change_has_tdd_cycle,
    "test_design_predates_implementation": _p_test_design_predates_implementation,
    "baseline_execution_predates_implementation": _p_baseline_execution_predates_implementation,
    "required_red_legitimate_and_current": _p_required_red_legitimate_and_current,
    "characterization_present_where_required": _p_characterization_present_where_required,
    "test_contract_frozen": _p_test_contract_frozen,
    "oracle_frozen": _p_oracle_frozen,
    "green_same_frozen_contract": _p_green_same_frozen_contract,
    "green_current_implementation_epoch": _p_green_current_implementation_epoch,
    "verification_current": _p_verification_current,
    "review_current": _p_review_current,
    "review_lease_closed": _p_review_lease_closed,
    "falsification_recorded": _p_falsification_recorded,
    "no_unresolved_blocking_finding": _p_no_unresolved_blocking_finding,
    "required_commands_executed": _p_required_commands_executed,
    "no_secrets_introduced": _p_no_secrets_introduced,
    "no_suspicious_test_aware_behavior": _p_no_suspicious_test_aware_behavior,
    "no_fake_red": _p_no_fake_red,
    "no_fabricated_capability_claim": _p_no_fabricated_capability_claim,
    "final_workspace_fingerprint_recorded": _p_final_workspace_fingerprint_recorded,
    "compiled_task_contract_current": _p_compiled_task_contract_current,
}


if tuple(FINAL_AUDIT_PRODUCERS) != REQUIRED_FINAL_AUDIT_CHECKS:
    raise RuntimeError("final-audit producer registry is not an exact ordered check bijection")


def _producer_id(check_id: str) -> str:
    return f"aegis.final-audit.{check_id}.v1"


PRODUCER_REGISTRY_DIGEST = _digest(
    [{"check_id": check_id, "producer_id": _producer_id(check_id)} for check_id in REQUIRED_FINAL_AUDIT_CHECKS]
)


def _run_producers(context: _Context) -> dict[str, dict]:
    proofs: dict[str, dict] = {}
    for check_id, producer in FINAL_AUDIT_PRODUCERS.items():
        try:
            result = producer(context)
        except FinalAuditRuntimeError as exc:
            raise FinalAuditRuntimeError(f"final-audit producer failed [{check_id}]: {exc}") from exc
        if not isinstance(result, dict) or result.get("status") not in {"PROVEN", "NOT_APPLICABLE"}:
            raise FinalAuditRuntimeError(f"final-audit producer returned invalid result: {check_id}")
        producer_id = _producer_id(check_id)
        proof_body = {
            "schema": 1,
            "check_id": check_id,
            "producer_id": producer_id,
            "status": result["status"],
            "detail": result["detail"],
            "justification": result.get("justification"),
            "facts": result.get("facts", {}),
        }
        proof_body["proof_digest"] = _digest(proof_body)
        proofs[check_id] = proof_body
    if len({item["producer_id"] for item in proofs.values()}) != len(REQUIRED_FINAL_AUDIT_CHECKS):
        raise FinalAuditRuntimeError("final-audit producer ids are not unique")
    if len({item["proof_digest"] for item in proofs.values()}) != len(REQUIRED_FINAL_AUDIT_CHECKS):
        raise FinalAuditRuntimeError("final-audit proof material is not check-specific")
    return proofs


def complete(store, task_id: str) -> dict:
    task = store.load(task_id)
    if task.get("state") != "FINAL_AUDIT":
        raise FinalAuditRuntimeError("audit-complete requires task state FINAL_AUDIT")
    store._validate_proofs(task, finalizing=False)
    lease = LeaseLock(leases_dir(store.root) / "subagent-lease.json", "single-active-subagent").inspect()
    if lease.get("exists"):
        raise FinalAuditRuntimeError("global subagent lease blocks final-audit completion")
    workspace_before = workspace_fingerprint(store.root)
    if workspace_before.get("available") is not True:
        raise FinalAuditRuntimeError("cannot complete final audit without an available workspace fingerprint")
    records = load_evidence(store._task_dir(task["id"]), verify=True)
    context = _Context(store, task, records, workspace_before)
    proofs = _run_producers(context)
    workspace_after = workspace_fingerprint(store.root)
    if workspace_after.get("available") is not True or workspace_after.get("sha256") != workspace_before.get("sha256"):
        raise FinalAuditRuntimeError("workspace changed while final-audit producers executed")

    prior_record_digests = [record["record_sha256"] for record in records]
    non_write_bindings = {
        name: non_write_audit_binding_digest(
            binding=name,
            task_id=task["id"],
            epoch=task["change_epoch"],
            workspace_sha256=workspace_before["sha256"],
        )
        for name in ("tdd-cycle", "review-receipt", "falsification-receipt")
    } if context.non_write else {}
    captured = {
        "task_id": task["id"],
        "epoch": task["change_epoch"],
        "implementation_digest": task["implementation_digest"],
        "diff_digest": task["diff_digest"],
        "compiled_contract_digest": context.compiled["digest"],
        "write_scope_digest": context.scope["digest"],
        "governance_digest": context.governance["digest"],
        "tdd_cycle_digest": non_write_bindings.get("tdd-cycle", context.cycle.get("cycle_sha256")),
        "review_receipt_digest": non_write_bindings.get(
            "review-receipt",
            task.get("review_receipt", {}).get("receipt_sha256") if isinstance(task.get("review_receipt"), dict) else None,
        ),
        "falsification_receipt_digest": non_write_bindings.get(
            "falsification-receipt",
            task.get("falsification", {}).get("receipt_sha256") if isinstance(task.get("falsification"), dict) else None,
        ),
        "test_law_baseline_digest": _artifact_digest(context.precheck.get("test_law_baseline"), "test/law baseline"),
        "gates_digest": _digest(task.get("gates", [])),
        "risks_digest": _digest(task.get("risks", [])),
        "decisions_digest": _digest(task.get("decisions", [])),
        "child_history_digest": _digest(task.get("child_history", [])),
        "verification_digest": _digest(task.get("verification_evidence", [])),
    }
    holder: dict[str, dict] = {}

    def bind(current: dict, record: dict) -> None:
        for field in ("id", "change_epoch", "implementation_digest", "diff_digest"):
            expected_key = "task_id" if field == "id" else ("epoch" if field == "change_epoch" else field)
            if current.get(field) != captured[expected_key]:
                raise FinalAuditRuntimeError("candidate changed before final-audit commit")
        current_workspace = workspace_fingerprint(store.root)
        if current_workspace.get("available") is not True or current_workspace.get("sha256") != workspace_before["sha256"]:
            raise FinalAuditRuntimeError("workspace changed before final-audit commit")
        evidence_set_digest = hashlib.sha256(
            b"".join(value.encode("ascii") for value in [*prior_record_digests, record["record_sha256"]])
        ).hexdigest()
        observations = [
            seal_task_audit_observation(
                check_id=check_id,
                producer_id=proofs[check_id]["producer_id"],
                status=proofs[check_id]["status"],
                proof_digest=proofs[check_id]["proof_digest"],
                evidence_record_ids=[record["id"]],
                detail=proofs[check_id]["detail"],
                justification=proofs[check_id].get("justification"),
            )
            for check_id in REQUIRED_FINAL_AUDIT_CHECKS
        ]
        receipt = build_task_audit_receipt(
            observations,
            task_id=current["id"],
            epoch=current["change_epoch"],
            audited_at=datetime.now(timezone.utc).isoformat(),
            implementation_digest=current["implementation_digest"],
            diff_digest=current["diff_digest"],
            workspace_sha256=current_workspace["sha256"],
            compiled_contract_digest=captured["compiled_contract_digest"],
            write_scope_digest=captured["write_scope_digest"],
            governance_digest=captured["governance_digest"],
            evidence_set_digest=evidence_set_digest,
            evidence_head=record["record_sha256"],
            tdd_cycle_digest=captured["tdd_cycle_digest"],
            review_receipt_digest=captured["review_receipt_digest"],
            falsification_receipt_digest=captured["falsification_receipt_digest"],
            test_law_baseline_digest=captured["test_law_baseline_digest"],
            gates_digest=captured["gates_digest"],
            risks_digest=captured["risks_digest"],
            decisions_digest=captured["decisions_digest"],
            child_history_digest=captured["child_history_digest"],
            verification_digest=captured["verification_digest"],
            producer_registry_digest=PRODUCER_REGISTRY_DIGEST,
        )
        holder["receipt"] = receipt
        current["final_audit_receipt"] = receipt
        current["final_audit_workspace"] = current_workspace
        current["final_audit_complete"] = True

    evidence_details = {
        "operation": "final-task-audit",
        "authoritative_checks": list(REQUIRED_FINAL_AUDIT_CHECKS),
        "producer_registry_digest": PRODUCER_REGISTRY_DIGEST,
        "candidate_bindings": captured,
        "proofs": proofs,
        "workspace": workspace_before,
    }
    updated, evidence = store._record_framework_observation(
        bind,
        task_id=task["id"],
        kind="audit",
        summary="Exact 40-check task final audit",
        provenance="verified-observation",
        evidence_details=evidence_details,
        preserve_final_audit=True,
    )
    return {
        **updated,
        "final_audit_receipt": holder["receipt"],
        "final_audit_evidence_id": evidence["id"],
    }
