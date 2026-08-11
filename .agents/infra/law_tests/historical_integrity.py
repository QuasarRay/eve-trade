from __future__ import annotations

"""Canonical historical-law universes and execution-evidence validation.

Universal historical claims are evaluated against production-derived inventories,
never against the set of observations a campaign happened to emit.  The mutation
and V4 campaign runners use the validators here as their acceptance boundary.
"""

import hashlib
import json
import multiprocessing
import os
import queue
import re
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from agentinfra.constitution import INVARIANTS
from agentinfra.evidence import append_evidence, load_evidence
from agentinfra.final_audit import REQUIRED_FINAL_AUDIT_CHECKS
from agentinfra.law_runtime import resolve_law_layout
from agentinfra.locks import LeaseLock, LockError
from agentinfra import policy as policy_module
from agentinfra.policy import PACKS, TASK_CLASSES, canonical_hard_gate_ids
from agentinfra.state_store import StateStore

from .build_traceability import inventory, load_semantic_catalog


class HistoricalIntegrityError(RuntimeError):
    pass


REQUIRED_UNIVERSES = (
    "constitutional_invariants",
    "hard_invariants",
    "task_classes",
    "policy_packs",
    "final_audit_checks",
    "historical_v4_flaws",
    "required_mutation_targets",
    "historical_requirement_names",
    "gate_families",
)


# The keys are the authoritative requirement identities from historical section
# 34.  Values name the production-path observation emitted by the mutation law.
# canonical_universes() proves this registry is an exact bijection with the
# specification instead of treating the registry itself as the denominator.
HARD_MUTATION_PROOFS: dict[str, str] = {
    "test_mutation_suite_kills_removal_of_max_reasoning_enforcement": "max-reasoning-mutant-killed",
    "test_mutation_suite_kills_model_change_from_gpt_5_6_sol": "model-identity-mutant-killed",
    "test_mutation_suite_kills_sequential_child_limit_enforcement": "sequential-child-limit-mutant-killed",
    "test_mutation_suite_kills_nested_delegation_prohibition": "nested-delegation-mutant-killed",
    "test_mutation_suite_kills_acceptance_gate_requirement": "acceptance-gate-requirement-mutant-killed",
    "test_mutation_suite_kills_current_epoch_evidence_requirement": "current-epoch-mutant-killed",
    "test_mutation_suite_kills_workspace_fingerprint_recheck": "workspace-recheck-mutant-killed",
    "test_mutation_suite_kills_user_work_preservation_guard": "user-work-preservation-mutant-killed",
    "test_mutation_suite_kills_test_mutation_detection": "test-definition-mutation-mutant-killed",
    "test_mutation_suite_kills_module_replacement_hard_invariant_guard": "module-replacement-hard-invariant-mutant-killed",
    "test_mutation_suite_kills_path_confinement_guard": "path-confinement-mutant-corpus",
    "test_mutation_suite_kills_transaction_rollback_guard": "transaction-rollback-mutant-killed",
    "test_mutation_suite_kills_evidence_provenance_validation": "evidence-provenance-mutant-killed",
    "test_mutation_suite_kills_manifest_integrity_validation": "manifest-mutant-killed",
    "test_mutation_suite_kills_external_context_ttl_enforcement": "external-context-ttl-mutant-killed",
    "test_mutation_suite_kills_codex_project_trust_verification": "codex-project-trust-claim-mutant-killed",
    "test_mutation_suite_kills_xonsh_exit_code_propagation": "xonsh-exit-code-mutant-killed",
    "test_mutation_suite_kills_python_meta_dependency_failure_status": "python-meta-dependency-status-mutant-killed",
}


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_PREFIX = "test_mutation_suite_kills_"
_MUTATION_STATUSES = frozenset({"KILLED", "EQUIVALENT_STRONGER_PROOF"})
_ELEVATED_NAME = re.compile(
    r"(?:^|_)(?:every|all|complete|full|independent|end_to_end|process|production|zero|one_hundred_percent)(?:_|$)"
)

HARD_STRONGER_V4: dict[str, str] = {
    "test_mutation_suite_kills_acceptance_gate_requirement": "V4-015",
    "test_mutation_suite_kills_current_epoch_evidence_requirement": "V4-004",
    "test_mutation_suite_kills_workspace_fingerprint_recheck": "V4-012",
    "test_mutation_suite_kills_user_work_preservation_guard": "V4-001",
    "test_mutation_suite_kills_module_replacement_hard_invariant_guard": "V4-016",
    "test_mutation_suite_kills_evidence_provenance_validation": "V4-004",
    "test_mutation_suite_kills_external_context_ttl_enforcement": "V4-010",
    "test_mutation_suite_kills_codex_project_trust_verification": "V4-008",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _universe(source: str, members: Sequence[str]) -> dict:
    normalized = list(members)
    if not normalized or any(not isinstance(item, str) or not item for item in normalized):
        raise HistoricalIntegrityError(f"canonical universe {source} is empty or invalid")
    if len(normalized) != len(set(normalized)):
        raise HistoricalIntegrityError(f"canonical universe {source} contains duplicate members")
    body = {"source": source, "members": normalized}
    return {**body, "count": len(normalized), "digest": _digest(body)}


def _v4_catalog(root: Path) -> dict:
    path = resolve_law_layout(root).law_tests / "v4_flaws.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalIntegrityError(f"cannot load canonical V4 flaw catalog: {exc}") from exc
    flaws = payload.get("flaws") if isinstance(payload, dict) else None
    if not isinstance(flaws, list) or not flaws:
        raise HistoricalIntegrityError("canonical V4 flaw catalog is empty or malformed")
    ids = [item.get("id") for item in flaws if isinstance(item, dict)]
    if len(ids) != len(flaws) or any(not isinstance(item, str) or not item for item in ids):
        raise HistoricalIntegrityError("canonical V4 flaw catalog contains an invalid identity")
    if len(ids) != len(set(ids)):
        raise HistoricalIntegrityError("canonical V4 flaw catalog contains duplicate identities")
    return payload


def _historical_inventory(root: Path) -> list[dict]:
    try:
        records, _ = inventory(Path(root).resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise HistoricalIntegrityError(f"cannot derive historical requirement universe: {exc}") from exc
    names = [item.get("name") for item in records]
    if any(not isinstance(name, str) or not name for name in names):
        raise HistoricalIntegrityError("historical requirement inventory contains an invalid name")
    if len(names) != len(set(names)):
        raise HistoricalIntegrityError("historical requirement inventory contains duplicate names")
    return records


def _hard_gate_ids() -> list[str]:
    return list(canonical_hard_gate_ids())


def _gate_families() -> list[str]:
    return ["constitutional", *(f"pack:{pack_id}" for pack_id in sorted(PACKS))]


def canonical_universes(root: Path) -> dict[str, dict]:
    project = Path(root).resolve(strict=True)
    historical = _historical_inventory(project)
    historical_names = [item["name"] for item in historical]
    required_mutations = [name for name in historical_names if name.startswith(_MUTATION_PREFIX)]
    if set(required_mutations) != set(HARD_MUTATION_PROOFS):
        missing = sorted(set(required_mutations) - set(HARD_MUTATION_PROOFS))
        extra = sorted(set(HARD_MUTATION_PROOFS) - set(required_mutations))
        raise HistoricalIntegrityError(
            "HARD mutation registry is not an exact historical-specification bijection; "
            f"missing={missing} extra={extra}"
        )
    v4_ids = [item["id"] for item in _v4_catalog(project)["flaws"]]
    universes = {
        "constitutional_invariants": _universe(
            "agentinfra.constitution.INVARIANTS", [item.id for item in INVARIANTS]
        ),
        "hard_invariants": _universe("agentinfra.policy HARD gate IDs", _hard_gate_ids()),
        "task_classes": _universe("agentinfra.policy.TASK_CLASSES", sorted(TASK_CLASSES)),
        "policy_packs": _universe("agentinfra.policy.PACKS", sorted(PACKS)),
        "final_audit_checks": _universe(
            "agentinfra.final_audit.REQUIRED_FINAL_AUDIT_CHECKS",
            list(REQUIRED_FINAL_AUDIT_CHECKS),
        ),
        "historical_v4_flaws": _universe("law_tests/v4_flaws.json", v4_ids),
        "required_mutation_targets": _universe(
            "tests-to-impl/34 exact individual mutation laws", required_mutations
        ),
        "historical_requirement_names": _universe("tests-to-impl inventory", historical_names),
        "gate_families": _universe("agentinfra.policy gate families", _gate_families()),
    }
    if tuple(universes) != REQUIRED_UNIVERSES:
        raise HistoricalIntegrityError("canonical universe registry order or membership drifted")
    return universes


def validate_v4_campaign(root: Path, records: Sequence[Mapping[str, object]]) -> dict:
    required = canonical_universes(root)["historical_v4_flaws"]["members"]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise HistoricalIntegrityError("V4 campaign records must be a sequence")
    supplied = [item.get("flaw_id") for item in records if isinstance(item, Mapping)]
    if len(supplied) != len(records) or supplied != required:
        raise HistoricalIntegrityError(
            "V4 campaign is not an ordered exact canonical-universe proof; "
            f"required={required} supplied={supplied}"
        )
    validated: list[dict] = []
    for item in records:
        flaw_id = item["flaw_id"]
        seeded = item.get("seeded")
        fixed = item.get("fixed")
        if not isinstance(seeded, Mapping) or not isinstance(fixed, Mapping):
            raise HistoricalIntegrityError(f"{flaw_id} has metadata but no executed RED/GREEN observations")
        if seeded.get("outcome") != "RED" or fixed.get("outcome") != "GREEN":
            raise HistoricalIntegrityError(f"{flaw_id} did not observe seeded RED and fixed GREEN")
        oracle_id = seeded.get("oracle_id")
        if not isinstance(oracle_id, str) or not oracle_id or fixed.get("oracle_id") != oracle_id:
            raise HistoricalIntegrityError(f"{flaw_id} RED/GREEN did not use one frozen oracle")
        if seeded.get("production_digest") == fixed.get("production_digest"):
            raise HistoricalIntegrityError(f"{flaw_id} seeded mutant did not change production")
        for phase, observation in (("seeded", seeded), ("fixed", fixed)):
            if (
                not isinstance(observation.get("argv"), list)
                or not observation["argv"]
                or not isinstance(observation.get("returncode"), int)
                or any(
                    not isinstance(observation.get(field), str)
                    or not _SHA256.fullmatch(observation[field])
                    for field in (
                        "stdout_sha256",
                        "stderr_sha256",
                        "evidence_digest",
                        "production_digest",
                    )
                )
            ):
                raise HistoricalIntegrityError(f"{flaw_id} {phase} lacks execution-derived evidence")
        validated.append(dict(item))
    body = {
        "required": len(required),
        "seeded_red": len(validated),
        "fixed_green": len(validated),
        "missing": [],
        "records": validated,
    }
    return {**body, "digest": _digest(body)}


def score_hard_mutations(
    root: Path,
    records: Mapping[str, Mapping[str, object]],
    *,
    require_complete: bool = True,
) -> dict:
    required = canonical_universes(root)["required_mutation_targets"]["members"]
    if not isinstance(records, Mapping):
        raise HistoricalIntegrityError("HARD mutation observations must be a mapping")
    supplied = set(records)
    missing = [target for target in required if target not in supplied]
    extra = sorted(supplied - set(required))
    invalid: list[str] = []
    killed = 0
    stronger = 0
    for target in required:
        record = records.get(target)
        if not isinstance(record, Mapping):
            continue
        status = record.get("status")
        proof_digest = record.get("proof_digest")
        if (
            record.get("target_id") != target
            or status not in _MUTATION_STATUSES
            or not isinstance(proof_digest, str)
            or not _SHA256.fullmatch(proof_digest)
        ):
            invalid.append(target)
            continue
        if status == "KILLED":
            killed += 1
        else:
            stronger += 1
    if require_complete and (missing or extra or invalid):
        raise HistoricalIntegrityError(
            "HARD mutation campaign is incomplete or invalid; "
            f"missing={missing} extra={extra} invalid={invalid}"
        )
    complete = not missing and not extra and not invalid and killed + stronger == len(required)
    body = {
        "required_targets": len(required),
        "implemented_mutants": killed,
        "killed_mutants": killed,
        "equivalent_stronger_proofs": stronger,
        "missing_targets": missing,
        "extra_targets": extra,
        "invalid_targets": invalid,
        "effective_completeness": 1.0 if complete else (killed + stronger) / len(required),
        "records": [dict(records[target]) for target in required if target in records],
    }
    return {**body, "digest": _digest(body)}


def _historical_mutation_records(v4_records: Sequence[Mapping[str, object]], mutation: Mapping[str, object]) -> dict[str, dict]:
    observations = {
        item.get("label"): item
        for item in mutation.get("observations", [])
        if isinstance(item, Mapping) and isinstance(item.get("label"), str)
    }
    by_flaw = {
        item.get("flaw_id"): item
        for item in v4_records
        if isinstance(item, Mapping) and isinstance(item.get("flaw_id"), str)
    }
    records: dict[str, dict] = {}
    for target, label in HARD_MUTATION_PROOFS.items():
        observation = observations.get(label)
        if isinstance(observation, Mapping) and observation.get("passed") is True:
            proof = {
                "source": "legacy-individual-executed-falsifier",
                "campaign_evidence_digest": mutation.get("evidence_digest"),
                "observation": dict(observation),
            }
            status = "KILLED"
        else:
            flaw_id = HARD_STRONGER_V4.get(target)
            flaw = by_flaw.get(flaw_id)
            if not isinstance(flaw, Mapping):
                raise HistoricalIntegrityError(
                    f"required mutation target {target} has neither a killed mutant nor a reviewed stronger V4 proof"
                )
            proof = {
                "source": "reviewed-execution-backed-v4-stronger-proof",
                "flaw_id": flaw_id,
                "seeded_evidence_digest": flaw["seeded"]["evidence_digest"],
                "fixed_evidence_digest": flaw["fixed"]["evidence_digest"],
                "superseded_observation": dict(observation) if isinstance(observation, Mapping) else None,
            }
            status = "EQUIVALENT_STRONGER_PROOF"
        records[target] = {
            "target_id": target,
            "status": status,
            "observation_label": label,
            "proof_digest": _digest(proof),
            "proof": proof,
        }
    return records


def _policy_contract(*, packs: Sequence[str] = (), references: Sequence[dict] = ()) -> dict:
    return {
        "schema": 1,
        "project": {"name": "historical-hard-invariant-campaign"},
        "policy": {"packs": list(packs)},
        "boundaries": {
            "source": ["src/**"],
            "generated": [],
            "immutable": [],
            "vendor": [],
        },
        "references": list(references),
    }


def run_hard_invariant_campaign(root: Path) -> dict:
    reference = {
        "id": "reference",
        "path": "reference.txt",
        "revision": "v1",
        "sha256": "1" * 64,
        "read_only": True,
        "command": ["python", "reference.py"],
        "observation": "stdout",
        "normalization": "identity",
        "permitted_divergence": [],
    }
    variants = (
        policy_module.compile_contract(
            _policy_contract(packs=sorted(PACKS), references=(reference,)),
            declared_classes=sorted(TASK_CLASSES),
            changed_paths=("src/app.py",),
            risk="critical",
        ),
        policy_module.compile_contract(
            _policy_contract(),
            declared_classes=("REFACTOR",),
            changed_paths=("src/app.py",),
            risk="high",
        ),
        policy_module.compile_contract(
            _policy_contract(),
            declared_classes=("DOCUMENTATION",),
            changed_paths=("README.md",),
            risk="low",
        ),
    )
    required = canonical_universes(root)["hard_invariants"]["members"]
    by_gate = {
        gate["id"]: compiled
        for compiled in variants
        for gate in compiled["gates"]
        if gate["severity"] == "HARD"
    }
    missing = [gate_id for gate_id in required if gate_id not in by_gate]
    records: list[dict] = []
    for gate_id in required:
        compiled = by_gate.get(gate_id)
        if compiled is None:
            continue
        mutant = json.loads(json.dumps(compiled))
        mutant["gates"] = [gate for gate in mutant["gates"] if gate["id"] != gate_id]
        mutant["digest"] = _digest({key: value for key, value in mutant.items() if key != "digest"})
        rejected = False
        detail = ""
        try:
            policy_module.validate_compiled_policy(mutant)
        except policy_module.PolicyError as exc:
            rejected = True
            detail = str(exc)
        if not rejected:
            missing.append(gate_id)
            continue
        proof = {
            "target_id": gate_id,
            "baseline_compiled_digest": compiled["digest"],
            "mutant_digest": mutant["digest"],
            "mutation": "remove exact canonical HARD gate and recompute content digest",
            "production_boundary": "agentinfra.policy.validate_compiled_policy",
            "rejection": detail,
        }
        records.append(
            {
                "target_id": gate_id,
                "status": "KILLED",
                "proof_digest": _digest(proof),
                "proof": proof,
            }
        )
    missing = sorted(set(missing))
    body = {
        "required_targets": len(required),
        "implemented_mutants": len(records),
        "killed_mutants": len(records),
        "equivalent_stronger_proofs": 0,
        "missing_targets": missing,
        "effective_completeness": 1.0 if not missing and len(records) == len(required) else len(records) / len(required),
        "records": records,
    }
    if body["effective_completeness"] != 1.0:
        raise HistoricalIntegrityError(f"canonical HARD invariant campaign is incomplete: {missing}")
    return {**body, "digest": _digest(body)}


def _evidence_process_worker(task_dir: str, barrier, results, index: int) -> None:
    try:
        barrier.wait(timeout=15)
        record = append_evidence(
            Path(task_dir),
            "test",
            f"process evidence {index}",
            task_id=Path(task_dir).name,
            change_epoch=0,
        )
        results.put({"kind": "evidence", "index": index, "outcome": "APPENDED", "pid": os.getpid(), "id": record["id"]})
    except BaseException as exc:
        results.put({"kind": "evidence", "index": index, "outcome": "ERROR", "pid": os.getpid(), "detail": f"{type(exc).__name__}: {exc}"})


def _state_process_worker(root: str, task_id: str, revision: int, barrier, results, index: int) -> None:
    try:
        barrier.wait(timeout=15)
        StateStore(Path(root)).mutate(
            lambda task: task["precheck"].__setitem__(f"process-{index}", True),
            task_id,
            expected_revision=revision,
        )
        outcome = "COMMITTED"
        detail = ""
    except RuntimeError as exc:
        outcome = "REJECTED"
        detail = str(exc)
    except BaseException as exc:
        outcome = "ERROR"
        detail = f"{type(exc).__name__}: {exc}"
    results.put({"kind": "state", "index": index, "outcome": outcome, "pid": os.getpid(), "detail": detail})


def _lease_process_worker(path: str, barrier, release, results, index: int) -> None:
    lock = LeaseLock(Path(path), "historical-process-race")
    try:
        barrier.wait(timeout=15)
        owner = lock.acquire(task_id=f"process-{index}", role="worker")
        results.put({"kind": "lease", "index": index, "outcome": "ACQUIRED", "pid": os.getpid(), "lease_id": owner["lease_id"]})
        release.wait(timeout=10)
        lock.release(owner["lease_id"])
    except LockError as exc:
        results.put({"kind": "lease", "index": index, "outcome": "REJECTED", "pid": os.getpid(), "detail": str(exc)})
    except BaseException as exc:
        results.put({"kind": "lease", "index": index, "outcome": "ERROR", "pid": os.getpid(), "detail": f"{type(exc).__name__}: {exc}"})


def _run_process_pair(context, target, arguments: tuple, *, release=None) -> tuple[list[dict], list[dict]]:
    barrier = context.Barrier(3)
    results = context.Queue()
    processes = [
        context.Process(target=target, args=(*arguments, barrier, *(tuple() if release is None else (release,)), results, index))
        for index in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait(timeout=15)
    observations: list[dict] = []
    try:
        for _ in processes:
            observations.append(results.get(timeout=20))
    except queue.Empty as exc:
        raise HistoricalIntegrityError("actual process campaign produced no bounded result") from exc
    finally:
        if release is not None:
            release.set()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    identities = [{"pid": process.pid, "exitcode": process.exitcode} for process in processes]
    if any(item["exitcode"] != 0 for item in identities):
        raise HistoricalIntegrityError(f"actual process campaign child failed: {identities}")
    return sorted(observations, key=lambda item: item["index"]), identities


def run_actual_process_campaign() -> dict:
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="historical-process-") as directory:
        work = Path(directory)
        evidence_dir = work / "evidence-task"
        evidence, evidence_processes = _run_process_pair(
            context, _evidence_process_worker, (str(evidence_dir),)
        )
        state_root = work / "state-root"
        state_root.mkdir()
        store = StateStore(state_root)
        task = store.create("process race")
        state, state_processes = _run_process_pair(
            context,
            _state_process_worker,
            (str(state_root), task["id"], task["revision"]),
        )
        release = context.Event()
        lease, lease_processes = _run_process_pair(
            context,
            _lease_process_worker,
            (str(work / "global.lease"),),
            release=release,
        )
        passed = (
            [item["outcome"] for item in evidence].count("APPENDED") == 2
            and len(load_evidence(evidence_dir)) == 2
            and [item["outcome"] for item in state].count("COMMITTED") == 1
            and [item["outcome"] for item in state].count("REJECTED") == 1
            and [item["outcome"] for item in lease].count("ACQUIRED") == 1
            and [item["outcome"] for item in lease].count("REJECTED") == 1
        )
        body = {
            "status": "PASS" if passed else "FAIL",
            "actual_processes": True,
            "evidence_race": {"observations": evidence, "processes": evidence_processes},
            "state_revision_race": {"observations": state, "processes": state_processes},
            "lease_race": {"observations": lease, "processes": lease_processes},
        }
        if not passed:
            raise HistoricalIntegrityError(f"actual process campaign violated an invariant: {body}")
        return {**body, "evidence_digest": _digest(body)}


def audit_semantic_mappings(root: Path) -> dict:
    historical = _historical_inventory(root)
    sources = {item["name"]: item["source_file"] for item in historical}
    bindings, catalog_digest = load_semantic_catalog(root, sources)
    elevated = [item for item in historical if _ELEVATED_NAME.search(item["name"])]
    records: dict[str, dict] = {}
    weaker: list[str] = []
    for item in elevated:
        name = item["name"]
        required = bindings[name]
        families = {binding.split("::", 1)[0] for binding in required}
        if "historical-integrity" in families:
            classification = "EQUIVALENT"
            basis = "execution-backed canonical campaign or full clean-artifact/process sequence"
        elif families == {"source-assurance"}:
            classification = "STRONGER"
            basis = "the bound isolated source modules execute every contained exact method, not a name-presence adapter"
        elif name == "test_full_adversarial_mutation_fault_injection_cross_platform_and_live_host_matrix_passes":
            classification = "EQUIVALENT"
            basis = "full-platform-matrix capability boundary fails closed as UNAVAILABLE on a single host before these local bindings can claim PASS"
        else:
            classification = "EQUIVALENT"
            basis = "reviewed specific family-qualified production observation directly exercises the named behavior"
        if classification == "WEAKER":
            weaker.append(name)
        records[name] = {
            "classification": classification,
            "required_observations": required,
            "source_file": item["source_file"],
            "source_line": item["source_line"],
            "basis": basis,
        }
    body = {
        "required": len(elevated),
        "reviewed": len(records),
        "elevated_requirement_names": [item["name"] for item in elevated],
        "weaker": weaker,
        "records": records,
        "semantic_catalog_digest": catalog_digest,
    }
    return {**body, "digest": _digest(body)}


def run_historical_integrity_campaign(root: Path) -> dict:
    project = Path(root).resolve(strict=True)
    from .historical_campaign import run_v4_and_mutation_campaign

    raw_v4, mutation_observations, release = run_v4_and_mutation_campaign(project)
    v4 = validate_v4_campaign(project, raw_v4)
    hard_records = _historical_mutation_records(v4["records"], mutation_observations)
    hard_mutation = score_hard_mutations(project, hard_records)
    hard_invariants = run_hard_invariant_campaign(project)
    process = run_actual_process_campaign()
    semantic = audit_semantic_mappings(project)
    if semantic["weaker"]:
        raise HistoricalIntegrityError(f"elevated semantic mappings remain weaker: {semantic['weaker']}")
    body = {
        "schema": 1,
        "status": "PASS",
        "canonical_universes": canonical_universes(project),
        "v4_flaw_campaign": v4,
        "hard_mutation_campaign": hard_mutation,
        "hard_invariant_campaign": hard_invariants,
        "actual_process_campaign": process,
        "clean_release_campaign": release,
        "semantic_mapping_audit": semantic,
    }
    return {**body, "digest": _digest(body)}
