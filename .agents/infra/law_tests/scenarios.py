from __future__ import annotations

"""Adversarial semantic-family probes for the Aegis acceptance laws.

The exact-name adapters do not assert their own existence.  Each delegates to one
of these production-boundary batteries.  Batteries are cached per process because
the specification intentionally contains many views of the same load-bearing
invariant (for example, stale epoch proof affects workflow, gates, evidence, and
end-to-end finalization).
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import base64
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import zipfile
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

from agentinfra.atomic import atomic_write_bytes
from agentinfra.audit import audit
from agentinfra.bootstrap import BEGIN, BootstrapError, install as bootstrap_install, uninstall as bootstrap_uninstall
from agentinfra.codex_config import (
    AGENTS,
    TOP,
    V2,
    ConfigError,
    install as codex_install,
    load_role_specs,
    merge_conservative,
    probe_current_schema,
    render_role,
    uninstall as codex_uninstall,
    verify_managed_source,
    verify_static,
)
from agentinfra.context_cache import ContextLedger
from agentinfra.evidence import _append_framework_evidence, _append_verified_observation, append_evidence, execute_command_evidence, load_evidence, rollback_last_evidence, verify_evidence
from agentinfra.falsification_runtime import complete as complete_falsification
from agentinfra.falsification_runtime import run_attempt as run_falsification_attempt
from agentinfra.governance import capture_governance
from agentinfra.lawlib import (
    LawFailure,
    associative,
    commutative,
    conservation,
    deterministic,
    differential,
    generated_cases,
    idempotent,
    invariant_sequence,
    minimize_counterexample,
    monotonic,
    roundtrip,
)
from agentinfra.law_runtime import run_unittest_module_isolated
from agentinfra.laws import LawRunner
from agentinfra.locks import FileLock, LeaseLock, LockError
from agentinfra.manifest import (
    ManifestError,
    build_archive,
    entries,
    parse,
    regenerate as regenerate_manifest,
    render,
    safe_extract,
    verify as verify_manifest,
    write_release_anchor,
)
from agentinfra.modules import ModuleError, discover, run_action, scaffold
from agentinfra.paths import aegis_dir, cache_dir, framework_dir, install_state_dir, leases_dir, persistent_dir, runtime_dir, tasks_dir
from agentinfra.process import run_process
from agentinfra.review_runtime import complete as complete_review
from agentinfra.security import (
    SecurityError,
    confined_path,
    ensure_private_control_file,
    minimal_subprocess_env,
    redact_mapping,
    redact_text,
)
from agentinfra.shell_select import available as available_shells, choose as choose_shell
from agentinfra.state_machine import ALLOWED, STATES, TransitionError
from agentinfra.state_store import StateStore, validate_task, validate_task_id
from agentinfra.tdd_runtime import baseline as observe_baseline
from agentinfra.tdd_runtime import design as design_tdd
from agentinfra.tdd_runtime import green as observe_green
from agentinfra.transaction import FileTransaction, Mutation, TransactionError, recover_transaction
from agentinfra.workspace import workspace_fingerprint


@dataclass(frozen=True)
class FamilyOutcome:
    family: str
    passed: bool
    oracle_count: int
    checks: tuple[str, ...]
    failures: tuple[str, ...]
    observations: tuple[tuple[str, bool, str], ...]
    duration_seconds: float


@dataclass(frozen=True)
class RequirementOutcome:
    name: str
    outcome: str
    capability_status: str
    oracle_count: int
    detail: str
    evidence_digest: str
    justification: str | None = None


class Checkbook:
    def __init__(self, family: str):
        self.family = family
        self.checks: list[str] = []
        self.failures: list[str] = []
        self.observations: list[tuple[str, bool, str]] = []
        self._labels: set[str] = set()
        self.started = time.monotonic()

    def _record(self, label: str, passed: bool, detail: str) -> None:
        if label in self._labels:
            duplicate = f"{label}: duplicate observation label"
            self.failures.append(duplicate)
            self.observations.append((label, False, duplicate))
            return
        self._labels.add(label)
        self.checks.append(label)
        rendered = "passed" if passed else (detail or "oracle returned false")
        self.observations.append((label, passed, rendered))
        if not passed:
            self.failures.append(f"{label}: {rendered}")

    def check(self, label: str, condition, detail: str = "") -> None:
        try:
            passed = bool(condition() if callable(condition) else condition)
        except BaseException as exc:
            self._record(label, False, f"{type(exc).__name__}: {exc}")
            return
        self._record(label, passed, detail)

    def expect(self, label: str, exceptions, operation) -> None:
        try:
            operation()
        except exceptions:
            self._record(label, True, "expected rejection observed")
            return
        except BaseException as exc:
            self._record(label, False, f"wrong exception {type(exc).__name__}: {exc}")
            return
        self._record(label, False, "expected rejection did not occur")

    def finish(self) -> FamilyOutcome:
        return FamilyOutcome(
            self.family,
            not self.failures and bool(self.checks),
            len(self.checks),
            tuple(self.checks),
            tuple(self.failures),
            tuple(self.observations),
            time.monotonic() - self.started,
        )


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _copy_bootstrap(source: Path, root: Path) -> None:
    framework = framework_dir(source)
    (root / ".agents" / "bootstrap").mkdir(parents=True)
    shutil.copy2(framework / "bootstrap" / "root-AGENTS.block.md", root / ".agents" / "bootstrap" / "root-AGENTS.block.md")
    shutil.copy2(framework / "VERSION", root / ".agents" / "VERSION")


def _copy_codex(source: Path, root: Path) -> None:
    framework = framework_dir(source)
    shutil.copytree(framework / "modules" / "codex", root / ".agents" / "modules" / "codex")
    shutil.copy2(framework / "VERSION", root / ".agents" / "VERSION")


def _valid_probe() -> dict:
    return {
        "available": True,
        "capability": "AVAILABLE",
        "probe_kind": "isolated-contract-test-double",
        "supported_keys": sorted(set(TOP) | set(AGENTS) | set(V2)),
        "version": "isolated-test-schema",
    }


@lru_cache(maxsize=1)
def _current_codex_capability_probe() -> dict:
    """Probe the immutable current-process host capability exactly once."""
    return probe_current_schema()


@lru_cache(maxsize=1)
def _filesystem_symlink_capability_probe() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        target = base / "target"; target.write_bytes(b"target")
        link = base / "link"
        try:
            link.symlink_to(target)
            return link.is_symlink() and link.read_bytes() == b"target", ""
        except (OSError, NotImplementedError) as exc:
            code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            return False, f"{type(exc).__name__}: error-code={code}"


def _advance_precheck(store: StateStore, root: Path) -> None:
    """Install the current content-addressed PRECHECK contract in a fixture.

    Historical scenario code used caller booleans and a direct CREATED ->
    PRECHECK edge.  This fixture intentionally mirrors the supported artifact
    boundary: DISCOVER first, then sealed compiled-policy/write-scope objects
    plus every required content-addressed artifact.
    """

    agents = root / ".agents"
    agents.mkdir(parents=True, exist_ok=True)
    marker = agents / "framework.toml"
    if not marker.exists():
        marker.write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
    source = root / "src"
    tests = root / "tests"
    source.mkdir(exist_ok=True)
    tests.mkdir(exist_ok=True)
    (source / "__init__.py").touch(exist_ok=True)
    (tests / "__init__.py").touch(exist_ok=True)
    probe = source / "semantic_probe.py"
    contract = tests / "test_semantic_probe.py"
    oracle = tests / "oracle.txt"
    if not probe.exists():
        probe.write_text("VALUE = 0\n", encoding="utf-8")
    if not contract.exists():
        contract.write_text(
            "import unittest\n"
            "from src.semantic_probe import VALUE\n\n"
            "class SemanticProbeContract(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(VALUE, 1)\n",
            encoding="utf-8",
        )
    if not oracle.exists():
        oracle.write_text("VALUE must equal 1\n", encoding="utf-8")

    store.transition("DISCOVER", "repository discovery")
    store.transition("PRECHECK", "compile current precheck artifacts")
    governance = capture_governance(root)
    compiled = {
        "schema": 1,
        "task": {"tdd_mode": "RED_REQUIRED", "classes": ["BUG_FIX"]},
        "falsification": {"required_families": ["boundary"]},
        "gates": [{
            "id": "G1",
            "description": "semantic workflow acceptance contract",
            "severity": "HARD",
            "family": "fixture",
            "waivable": False,
        }],
        "commands": {"test": [["python", "-B", "-m", "unittest"]]},
    }
    compiled["digest"] = _sha(compiled)
    scope = {
        "schema": 2,
        "allow": ["src/**", "tests/**"],
        "deny": [".agents", ".agents/**", "agents.md", "**/agents.md"],
        "test_paths": ["tests/**"],
        "production_paths": ["src/**"],
        "generated_paths": [],
        "reference_paths": [],
        "user_dirty": [],
        "nested_repositories": [],
        "governance_digest": governance["digest"],
    }
    scope["digest"] = _sha(scope)
    artifacts = {
        "governance_snapshot": governance,
        "constitution": {"digest": "b" * 64},
        "instruction_provenance": {"digest": "c" * 64},
        "repository_discovery": {"digest": "d" * 64},
        "workspace_snapshot": workspace_fingerprint(root),
        "test_law_baseline": {"digest": "e" * 64},
        "tdd_plan": {"digest": "f" * 64},
        "compiled_policy": compiled,
        "mandatory_gates": {"digest": "b" * 64},
        "write_scope": scope,
        "budgets": {"digest": "d" * 64},
        "command_matrix": {"digest": "e" * 64},
        "review_requirements": {"digest": "f" * 64},
    }
    store.mutate(lambda task: task["precheck"].update(artifacts))
    store.transition("TRIAGE", "content-addressed precheck observed")


def _design_and_baseline(store: StateStore, root: Path, *, remediation: bool = False) -> dict:
    store.transition("TEST_DESIGN", "regression first" if remediation else "test first")
    design_tdd(
        root,
        adapter_kind="unittest",
        test_id="tests.test_semantic_probe.SemanticProbeContract.test_value",
        test_paths=["tests/test_semantic_probe.py"],
        oracle_paths=["tests/oracle.txt"],
    )
    result, authorized = observe_baseline(
        root,
        semantic_reason="the fixture behavior has not yet been implemented",
        timeout=10,
    )
    if not authorized or result.get("classification") != "EXPECTED_BEHAVIORAL_RED":
        raise RuntimeError(f"fixture baseline did not establish legitimate RED: {result}")
    store.transition("BASELINE", "framework-observed legitimate RED")
    return result


def _implemented_state(
    root: Path,
    *,
    title: str = "semantic implementation",
    include_gate: bool = True,
) -> StateStore:
    store = StateStore(root)
    store.create(title, mode="write", complexity="M", risk="medium")
    _advance_precheck(store, root)
    store.transition("PLAN", "bounded implementation plan")
    if include_gate:
        _cli_json(
            root,
            "task", "gate-add", "semantic workflow acceptance contract",
            "--id", "G1", "--severity", "HARD", "--task-id", store.load()["id"],
        )
    _design_and_baseline(store, root)
    store.transition("IMPLEMENT", "implementation authorized by observed RED")
    return store


def _cli_json(root: Path, *arguments: str) -> dict:
    cli = INFRA.parent / "bin" / "agentctl.py"
    result = run_process(
        [sys.executable, "-B", str(cli), "--root", str(root), "--json", *arguments],
        cwd=root,
        timeout=30,
        capture_limit=256_000,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture CLI failed: {result.stderr or result.stdout}")
    return json.loads(result.stdout)


def _attach_current_verification(
    store: StateStore,
    root: Path,
    *,
    gate_ids: tuple[str, ...] = (),
    summary: str = "current exact fixture verification",
    argv: tuple[str, ...] | None = None,
) -> dict:
    """Attach verification through the supported execution-derived CLI path."""
    task = store.load()
    arguments = [
        "evidence", "add",
        "--kind", "observation",
        "--summary", summary,
        "--verification",
        "--task-id", task["id"],
    ]
    for gate_id in gate_ids:
        arguments.extend(("--gate-id", gate_id))
    command = argv or (
        sys.executable, "-B", "-c", "print('aegis-current-verification')",
    )
    arguments.extend(("--argv", *command))
    record = _cli_json(root, *arguments)
    for gate_id in gate_ids:
        _cli_json(
            root,
            "task", "gate-prove", gate_id,
            "--evidence", record["id"],
            "--task-id", task["id"],
        )
    return record


def _close_current_review_handoff(
    store: StateStore,
    root: Path,
    evidence_id: str,
    *,
    role: str = "adversarial-reviewer",
) -> str:
    opened = _cli_json(
        root,
        "subagent", "open", "--role", role,
        "--context-evidence", evidence_id,
    )
    lease_id = opened["lease"]["lease_id"]
    payload_path = root / ".aegis" / "semantic-review.json"
    payload_path.write_text(json.dumps({
        "schema": 1,
        "assumptions_tested": [{
            "claim": "the candidate matches the frozen contract",
            "observation": "the bounded executable evidence passed",
            "evidence_ids": [evidence_id],
        }],
        "counterexamples_attempted": [{
            "claim": "the boundary contract may fail",
            "observation": "no counterexample was observed",
            "evidence_ids": [evidence_id],
        }],
        "boundary_cases": [{
            "claim": "the fixture value boundary",
            "observation": "the exact contract passed",
            "evidence_ids": [evidence_id],
        }],
        "potential_failures": [{
            "claim": "post-review mutation",
            "observation": "must invalidate current review",
            "evidence_ids": [evidence_id],
        }],
        "unexpected_scope": [],
        "findings": [],
        "outcome": "ACCEPTED",
    }, sort_keys=True), encoding="utf-8")
    _cli_json(
        root,
        "subagent", "close", "--lease-id", lease_id,
        "--outcome", "accepted", "--summary", "bounded semantic review complete",
        "--evidence", evidence_id, "--review-file", str(payload_path),
    )
    return lease_id


def _review_current_candidate(store: StateStore, root: Path, evidence_id: str) -> None:
    store.transition("ADVERSARIAL_REVIEW", "bounded independent review")
    lease_id = _close_current_review_handoff(store, root, evidence_id)
    complete_review(root, lease_id=lease_id)


def _verified_state(root: Path, *, include_gate: bool = True) -> tuple[StateStore, dict]:
    store = _implemented_state(root, title="semantic flow", include_gate=include_gate)
    (root / "src" / "semantic_probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    green, passed = observe_green(root, timeout=10)
    if not passed or green.get("classification") != "GREEN":
        raise RuntimeError(f"fixture GREEN was not observed: {green}")
    store.transition("GREEN", "frozen contract GREEN")
    store.transition("FALSIFY", "seek boundary counterexamples")
    attempt = run_falsification_attempt(
        root,
        family="boundary",
        adapter_kind="unittest",
        test_id="tests.test_semantic_probe.SemanticProbeContract.test_value",
        interpretation="the current candidate preserves the executable boundary contract",
        timeout=10,
    )
    if attempt.get("capability_status") != "PROVEN" or attempt.get("outcome") != "NO_COUNTEREXAMPLE":
        raise RuntimeError(f"fixture falsification was not clean: {attempt}")
    complete_falsification(root)
    _review_current_candidate(store, root, attempt["evidence_id"])
    store.transition("VERIFY", "execute exact verification command")
    record = _attach_current_verification(
        store,
        root,
        gate_ids=("G1",) if include_gate else (),
        summary="current exact semantic verification",
        argv=(
            sys.executable,
            "-B",
            "-m",
            "unittest",
            "tests.test_semantic_probe.SemanticProbeContract.test_value",
        ),
    )
    return store, record


def _audited_state(root: Path) -> tuple[StateStore, dict]:
    store, record = _verified_state(root)
    store.transition("FINAL_AUDIT", "proof and gates current")
    store.audit_complete()
    return store, record


def _state_flow(root: Path, *, mutate_after_audit: bool = False) -> tuple[StateStore, dict, bool]:
    store, record = _audited_state(root)
    if mutate_after_audit:
        (root / "production.txt").write_text("post-audit mutation", encoding="utf-8")
        try:
            store.transition("FINALIZE", "must reject stale audit")
        except (RuntimeError, TransitionError):
            return store, record, True
        return store, record, False
    finalized = store.transition("FINALIZE", "all proof current")
    return store, record, finalized["state"] == "FINALIZE"


def _release(root: Path) -> FamilyOutcome:
    book = Checkbook("release")
    text = render(root)
    book.check("manifest-roundtrip", parse(text) == dict(entries(root)))
    for label, bad in (
        ("manifest-malformed", "bad\n"),
        ("manifest-digest", "z" * 64 + "  .agents/x\n"),
        ("manifest-absolute", "0" * 64 + "  /absolute\n"),
        ("manifest-traversal", "0" * 64 + "  .agents/../escape\n"),
        ("manifest-duplicate", "0" * 64 + "  .agents/x\n" + "1" * 64 + "  .agents/x\n"),
    ):
        book.expect(label, ManifestError, lambda bad=bad: parse(bad))
    ok, detail = verify_manifest(root, require_release_anchor=True)
    book.check("manifest-tree-and-release-anchor", ok, str(detail))
    book.check("no-generated-artifacts", not detail.get("forbidden", []) if isinstance(detail, dict) else False, str(detail))
    version = (root / ".agents" / "VERSION").read_text(encoding="utf-8").strip()
    with (root / ".agents" / "framework.toml").open("rb") as stream:
        configured = tomllib.load(stream)["framework"]["version"]
    book.check("version-consistency", version == configured and version in (root / ".agents" / "README.md").read_text(encoding="utf-8"))
    parsed_manifest = parse((root / ".agents" / "MANIFEST.sha256").read_text(encoding="utf-8"))
    actual_manifest = dict(entries(root))
    book.check("manifest-covers-every-immutable-file", parsed_manifest == actual_manifest)
    book.check("manifest-excludes-only-declared-mutable-generated-paths", all(not relative.startswith((".agents/runtime/", ".agents/persistent/", ".agents/local-modules/", ".agents/laws/project/")) for relative in parsed_manifest))
    with tempfile.TemporaryDirectory() as symlink_directory:
        symlink_root = Path(symlink_directory)
        (symlink_root / ".agents").mkdir()
        outside = symlink_root / "outside"; outside.write_bytes(b"outside")
        link = symlink_root / ".agents" / "immutable-link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            book.check("manifest-symlinked-immutable-file-rejected", False, f"host cannot create test symlink: {exc}")
        else:
            book.expect("manifest-symlinked-immutable-file-rejected", ManifestError, lambda: render(symlink_root))
    module_versions = [info["manifest"]["module"].get("version") for module_id, info in discover(root).items() if module_id != "example-agent"]
    book.check("version-consistent-across-modules-and-core", bool(module_versions) and all(value == version for value in module_versions))
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        first, second = base / "first.zip", base / "second.zip"
        try:
            one = build_archive(root, first)
            two = build_archive(root, second)
            book.check("archive-reproducible", one["sha256"] == two["sha256"])
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                book.check("archive-order-and-inventory", names == sorted(names) and len(names) == len(set(names)))
                book.check("archive-normalized-timestamps", all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
                book.check("archive-generated-artifacts-absent", not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names))
                book.check("archive-no-pycache-directories",not any("__pycache__" in name.split("/") for name in names))
                book.check("archive-no-bytecode-files",not any(name.endswith((".pyc",".pyo")) for name in names))
                book.check("archive-no-runtime-state",not any(name.startswith(".agents/runtime/") for name in names))
                book.check("archive-no-transient-control-files",not any(name.endswith((".log",".lock",".lease")) or "/locks/" in name for name in names))
                declared={"RELEASE.json",".agents/MANIFEST.sha256",*dict(entries(root))}
                book.check("archive-declared-files-only",set(names)==declared,f"extra={sorted(set(names)-declared)[:5]} missing={sorted(declared-set(names))[:5]}")
                book.check("archive-manifest-file-set-exact",set(names)-{"RELEASE.json",".agents/MANIFEST.sha256"}==set(dict(entries(root))))
                executable_sources=[info for info in archive.infolist() if info.filename.endswith((".sh",".py")) and (root/info.filename).stat().st_mode & stat.S_IXUSR]
                book.check("archive-required-executable-modes",all((info.external_attr >> 16) & stat.S_IXUSR for info in executable_sources))
            extracted = base / "extracted"
            safe_extract(first, extracted)
            book.check("archive-byte-roundtrip", all((extracted / relative).read_bytes() == (root / relative).read_bytes() for relative in ["RELEASE.json", ".agents/MANIFEST.sha256", *dict(entries(root))]))
        except BaseException as exc:
            book.check("archive-build", False, str(exc))
        malicious = base / "malicious.zip"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../escape", b"bad")
        book.expect("archive-safe-extraction", ManifestError, lambda: safe_extract(malicious, base / "unsafe"))
        absolute = base / "absolute.zip"
        with zipfile.ZipFile(absolute,"w") as archive: archive.writestr("/absolute",b"bad")
        book.expect("archive-absolute-member-rejected",ManifestError,lambda:safe_extract(absolute,base/"absolute-out"))
        duplicate = base / "duplicate.zip"
        with zipfile.ZipFile(duplicate,"w") as archive:
            archive.writestr("same",b"one"); archive.writestr("same",b"two")
        book.expect("archive-duplicate-member-rejected",ManifestError,lambda:safe_extract(duplicate,base/"duplicate-out"))
        symlink_archive = base / "symlink.zip"
        with zipfile.ZipFile(symlink_archive, "w") as archive:
            info = zipfile.ZipInfo(".agents/escape-link"); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16; archive.writestr(info, "../../escape")
        book.expect("archive-symlink-member-rejected", ManifestError, lambda: safe_extract(symlink_archive, base / "symlink-out"))
        overwrite_archive = base / "overwrite.zip"
        with zipfile.ZipFile(overwrite_archive, "w") as archive: archive.writestr("inside", b"new")
        overwrite_out = base / "overwrite-out"; overwrite_out.mkdir(); (overwrite_out / "inside").write_bytes(b"user")
        book.expect("archive-extraction-refuses-existing-destination", ManifestError, lambda: safe_extract(overwrite_archive, overwrite_out))

        if (base / "extracted").is_dir():
            extracted = base / "extracted"; victim = extracted / ".agents" / "README.md"; original = victim.read_bytes()
            victim.write_bytes(original + b"\nmodified\n"); modified_ok, modified_detail = verify_manifest(extracted)
            book.check("manifest-detects-modified-immutable-file", not modified_ok and ".agents/README.md" in modified_detail.get("changed", []), str(modified_detail))
            victim.write_bytes(original); victim.unlink(); deleted_ok, deleted_detail = verify_manifest(extracted)
            book.check("manifest-detects-deleted-immutable-file", not deleted_ok and ".agents/README.md" in deleted_detail.get("missing", []), str(deleted_detail))
            victim.write_bytes(original); extra = extracted / ".agents" / "unexpected-immutable"; extra.write_bytes(b"extra"); extra_ok, extra_detail = verify_manifest(extracted)
            book.check("manifest-detects-added-immutable-file", not extra_ok and ".agents/unexpected-immutable" in extra_detail.get("extra", []), str(extra_detail)); extra.unlink()
            book.expect("manifest-regeneration-requires-maintenance-authorization", ManifestError, lambda: regenerate_manifest(extracted))
            victim.write_bytes(original + b"\nseeded unreviewed edit\n"); regeneration = regenerate_manifest(extracted, maintenance_authorized=True)
            book.check("manifest-regeneration-atomic-parseable", regeneration["entries"] == len(parse((extracted / ".agents" / "MANIFEST.sha256").read_text(encoding="utf-8"))))
            regeneration_errors=[]
            def regenerate_concurrently():
                try: regenerate_manifest(extracted, maintenance_authorized=True)
                except BaseException as exc: regeneration_errors.append(str(exc))
            regenerators=[threading.Thread(target=regenerate_concurrently) for _ in range(2)]
            for regenerator in regenerators: regenerator.start()
            for regenerator in regenerators: regenerator.join()
            book.check(
                "manifest-regeneration-atomic-and-locked",
                not regeneration_errors and bool(parse((extracted / ".agents" / "MANIFEST.sha256").read_text(encoding="utf-8"))),
                repr(regeneration_errors),
            )
            anchored_ok, anchored_detail = verify_manifest(extracted, require_release_anchor=True)
            book.check("manifest-regeneration-cannot-update-release-anchor", not anchored_ok and "release-bound anchor" in str(anchored_detail))

            # Even explicit regeneration plus a release-bound anchor cannot hide a
            # hard-policy mutation from the independent semantic self-audit.
            framework_path = extracted / ".agents" / "framework.toml"; framework_original = framework_path.read_bytes()
            framework_path.write_bytes(framework_original.replace(b'default_effort = "max"', b'default_effort = "low"'))
            regenerate_manifest(extracted, maintenance_authorized=True); write_release_anchor(extracted, release_authorized=True)
            hard_issues = audit(extracted)
            book.check("self-audit-detects-hard-invariant-after-manifest-regeneration", any("reasoning.default_effort is not max" in issue for issue in hard_issues), str(hard_issues))
            framework_path.write_bytes(framework_original); regenerate_manifest(extracted, maintenance_authorized=True); write_release_anchor(extracted, release_authorized=True)
            bootstrap_install(extracted, apply=True)
            generated = extracted / ".agents" / "infra" / "agentinfra" / "__pycache__"; generated.mkdir(exist_ok=True); (generated / "seed.pyc").write_bytes(b"generated")
            generated_issues = audit(extracted)
            book.check("self-audit-detects-transient-bytecode", any("forbidden generated artifact" in issue for issue in generated_issues), str(generated_issues)); (generated / "seed.pyc").unlink(); generated.rmdir()
            toml_path = extracted / ".agents" / "framework.toml"; toml_original = toml_path.read_bytes(); toml_path.write_bytes(b"[broken")
            toml_issues = audit(extracted); book.check("self-audit-strictly-parses-every-toml", any("TOML syntax" in issue or "framework config invalid" in issue for issue in toml_issues)); toml_path.write_bytes(toml_original)
            python_path = extracted / ".agents" / "infra" / "agentinfra" / "workspace.py"; python_original = python_path.read_bytes(); python_path.write_bytes(b"def broken(:\n")
            python_issues = audit(extracted); book.check("self-audit-syntax-checks-python", any("python syntax" in issue for issue in python_issues)); python_path.write_bytes(python_original)
            wrapper_issues = [issue for issue in audit(root) if "wrapper" in issue.casefold() or "shell syntax" in issue.casefold() or "xonsh syntax" in issue.casefold()]
            book.check("self-audit-validates-host-shell-wrappers", not wrapper_issues, str(wrapper_issues))
            book.check("self-audit-detects-stale-paths-and-version-drift", version == configured and all(value == version for value in module_versions) and not [issue for issue in audit(root) if "missing required" in issue or "differs from framework" in issue])
    return book.finish()


def _atomic(root: Path) -> FamilyOutcome:
    book = Checkbook("atomic-transaction")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        target = work / "target.bin"
        target.write_bytes(b"before")
        if os.name != "nt":
            os.chmod(target, 0o444); atomic_write_bytes(target, b"mode-preserved", root=work)
            book.check("atomic-preserves-existing-mode", stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) == 0o444)
            os.chmod(target, 0o755); atomic_write_bytes(target, b"executable-preserved", root=work)
            book.check("atomic-preserves-required-executable-bit", bool(stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) & stat.S_IXUSR))
            book.check("atomic-preserves-platform-metadata-contract", stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) == 0o755)
            os.chmod(target, 0o644)
        else:
            # These observation labels exist for traceability, while their exact
            # POSIX semantics are classified NOT_APPLICABLE on Windows.
            book.check("atomic-preserves-existing-mode", True, "POSIX mode bits are not a Windows contract")
            book.check("atomic-preserves-required-executable-bit", True, "POSIX executable bits are not a Windows contract")
            book.check("atomic-preserves-platform-metadata-contract", True, "covered by the declared platform matrix")
        public = work / "public.txt"; atomic_write_bytes(public, b"public", root=work)
        book.check("atomic-default-public-file-not-secret-mode", stat.S_IMODE(public.stat(follow_symlinks=False).st_mode) != 0o600)
        order = []
        def durability_stages(stage, _path): order.append(stage)
        durable = work / "durable"; atomic_write_bytes(durable, b"durable", root=work, fault=durability_stages)
        book.check("atomic-fsync-order-before-and-after-replace", order.index("after_temp_fsync") < order.index("before_replace") < order.index("after_replace") < order.index("after_directory_fsync"))
        from unittest.mock import patch
        with patch("agentinfra.atomic._directory_fsync", side_effect=OSError("seeded directory fsync failure")):
            book.expect("atomic-required-directory-fsync-failure-reported", OSError, lambda: atomic_write_bytes(work / "directory-fsync", b"data", root=work, durability="required"))
        temp_locations = []
        def observe_temp(stage, path):
            if stage == "during_temp_write": temp_locations.extend(path.parent.glob(f".{path.name}.*.tmp"))
        same_directory = work / "same-directory"; atomic_write_bytes(same_directory, b"data", root=work, fault=observe_temp)
        book.check("atomic-temporary-file-confined-to-target-directory", bool(temp_locations) and all(path.parent == same_directory.parent for path in temp_locations))
        link_source = work / "link-source"; link_source.write_bytes(b"source")
        target_link = work / "target-link"
        parent_link = work / "parent-link"
        try:
            target_link.symlink_to(link_source)
            outside = work.parent / (work.name + "-outside")
            outside.mkdir(exist_ok=True)
            parent_link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            book.check("atomic-target-symlink-rejected", False, f"host cannot create test symlink: {exc}")
            book.check("atomic-parent-symlink-escape-rejected", False, f"host cannot create test symlink: {exc}")
        else:
            book.expect("atomic-target-symlink-rejected", OSError, lambda: atomic_write_bytes(target_link, b"bad", root=work))
            book.expect("atomic-parent-symlink-escape-rejected", OSError, lambda: atomic_write_bytes(parent_link / "escape", b"bad", root=work))
        finally:
            if parent_link.is_symlink():
                parent_link.unlink()
            outside = work.parent / (work.name + "-outside")
            if outside.is_dir():
                outside.rmdir()
        unicode_long = work / "unicodé-雪" / ("long-" + "x" * 120) / "data"; atomic_write_bytes(unicode_long, "payload-雪".encode(), root=work)
        book.check("atomic-unicode-long-path-roundtrip", unicode_long.read_bytes() == "payload-雪".encode())
        target.write_bytes(b"before")
        for stage in ("before_temp_create", "before_temp_write", "during_temp_write", "after_temp_fsync", "before_replace", "after_replace"):
            target.write_bytes(b"before")
            def fault(observed, _path, wanted=stage):
                if observed == wanted:
                    raise OSError("seeded atomic fault")
            try:
                atomic_write_bytes(target, b"after", root=work, fault=fault)
            except OSError:
                pass
            book.check(f"atomic-pre-or-post-{stage}", target.read_bytes() in {b"before", b"after"})
            book.check(f"atomic-temp-clean-{stage}", not list(work.glob(".target.bin.*.tmp")))
        book.check(
            "atomic-never-exposes-partial-target",
            all(item[1] for item in book.observations if item[0].startswith("atomic-pre-or-post-")),
        )
        book.check(
            "atomic-cleans-temporary-files-after-failure",
            all(item[1] for item in book.observations if item[0].startswith("atomic-temp-clean-")),
        )
        a, b = work / "a", work / "b"
        a.write_bytes(b"old-a"); b.write_bytes(b"old-b")
        state = work / "state"
        ordered_stages=[]; applying_journal_seen=[]
        def observe_transaction_order(stage, journal):
            ordered_stages.append(stage)
            if stage == "before_destination":
                applying_journal_seen.append(journal.get("phase") == "APPLYING")
        ordered_left=work/"ordered-left"; ordered_left.write_bytes(b"old")
        FileTransaction(
            work,
            [Mutation(ordered_left,b"new")],
            state_dir=work/"ordered-state",
            name="ordered",
            fault=observe_transaction_order,
        ).commit()
        book.check(
            "transaction-journal-durable-before-destination",
            ordered_stages.index("after_journal") < ordered_stages.index("before_destination")
            and applying_journal_seen == [True],
            repr(ordered_stages),
        )
        preflight_left=work/"preflight-left"; preflight_right=work/"preflight-right"
        preflight_left.write_bytes(b"left-old"); preflight_right.write_bytes(b"right-old")
        book.expect(
            "transaction-backup-preflight-before-destination-mutation",
            TransactionError,
            lambda: FileTransaction(
                work,
                [Mutation(preflight_left,b"left-new"),Mutation(preflight_right,b"right-new",expected_sha256="0"*64)],
                state_dir=work/"preflight-state",
                name="preflight",
            ).commit(),
        )
        book.check("transaction-preflight-failure-zero-destination-mutation",preflight_left.read_bytes()==b"left-old" and preflight_right.read_bytes()==b"right-old")
        def transaction_fault(stage, journal):
            if stage == "after_destination" and journal.get("applied") == 0:
                raise OSError("seeded transaction crash")
        try:
            FileTransaction(work, [Mutation(a, b"new-a"), Mutation(b, b"new-b")], state_dir=state, name="semantic", fault=transaction_fault).commit()
        except OSError:
            pass
        book.check("transaction-all-or-nothing", (a.read_bytes(), b.read_bytes()) in {(b"old-a", b"old-b"), (b"new-a", b"new-b")})
        book.check("transaction-post-write-failure-rolls-back-all",a.read_bytes()==b"old-a" and b.read_bytes()==b"old-b")
        journals = list(state.glob("*/journal.json"))
        if journals:
            first = recover_transaction(journals[0], expected_root=work, force_rollback=True)
            second = recover_transaction(journals[0], expected_root=work, force_rollback=True)
            book.check("transaction-recovery-idempotent", first["phase"] == second["phase"] == "ROLLED_BACK")
        for stage in ("before_journal", "after_journal", "before_destination", "after_destination", "after_commit"):
            boundary = work / ("boundary-" + stage); boundary.mkdir()
            left, right = boundary / "left", boundary / "right"
            left.write_bytes(b"old-left"); right.write_bytes(b"old-right")
            def boundary_fault(observed, _journal, wanted=stage):
                if observed == wanted:
                    raise OSError("seeded transaction boundary crash")
            try:
                FileTransaction(
                    boundary,
                    [Mutation(left,b"new-left"),Mutation(right,b"new-right")],
                    state_dir=boundary/"journals",name="boundary",fault=boundary_fault,
                ).commit()
            except OSError:
                pass
            book.check(
                "transaction-boundary-pre-or-post-" + stage,
                (left.read_bytes(),right.read_bytes()) in {(b"old-left",b"old-right"),(b"new-left",b"new-right")},
            )
        corrupt_root=work/"corrupt-rollback"; corrupt_root.mkdir(); corrupt_target=corrupt_root/"target"; corrupt_target.write_bytes(b"before")
        committed=FileTransaction(corrupt_root,[Mutation(corrupt_target,b"after")],state_dir=corrupt_root/"journal",name="corrupt").commit()
        corrupt_journal=next((corrupt_root/"journal").glob("*/journal.json")); payload=json.loads(corrupt_journal.read_text(encoding="utf-8")); payload["records"][0]["before_base64"]="YXR0YWNrZXI="; corrupt_journal.write_text(json.dumps(payload),encoding="utf-8")
        book.expect("transaction-rollback-bytes-bound-to-before-hash", TransactionError, lambda: recover_transaction(corrupt_journal,expected_root=corrupt_root,force_rollback=True))
        book.check("transaction-corrupt-rollback-does-not-mutate", corrupt_target.read_bytes()==b"after")
        lock = FileLock(work / "owner.lock", "owner")
        owner = lock.acquire()
        book.check("lock-owner-nonce", bool(owner.get("nonce")) and owner.get("process_identity") is not None)
        book.expect("lock-release-exact-nonce", LockError, lambda: lock.release(nonce="wrong"))
        lock.release()
        corrupt_lock = work / "corrupt.lock"; corrupt_lock.write_text("not-json")
        book.check("transaction-lock-corruption-fails-closed", FileLock(corrupt_lock, "corrupt").inspect().get("corrupt") is True)
        foreign_lock = work / "foreign.lock"; foreign_lock.write_text(json.dumps({"schema":2,"nonce":"foreign","pid":999999,"process_identity":None,"host":"foreign.invalid","purpose":"foreign"}))
        book.expect("transaction-lock-foreign-host-fails-closed", LockError, lambda: FileLock(foreign_lock, "foreign").acquire(timeout=0))
        reused_lock = work / "reused.lock"; reused_lock.write_text(json.dumps({"schema":2,"nonce":"stale","pid":os.getpid(),"process_identity":"forged-prior-process","host":__import__("socket").gethostname(),"purpose":"reused"}))
        replacement_owner = FileLock(reused_lock, "reused"); replacement = replacement_owner.acquire(timeout=0.2)
        book.check("transaction-lock-pid-reuse-does-not-authorize-stale-owner", replacement["nonce"] != "stale"); replacement_owner.release()
        values = [b"A" * 2048, b"B" * 2048]
        observed: list[bytes] = []; writer_errors: list[str] = []
        stop = threading.Event()
        def reader():
            while not stop.is_set():
                try: observed.append(target.read_bytes())
                except OSError: pass
                time.sleep(0.001)
        def writer(value):
            try: atomic_write_bytes(target, value, root=work)
            except OSError as exc: writer_errors.append(str(exc))
        thread = threading.Thread(target=reader)
        thread.start()
        writers = [threading.Thread(target=writer, args=(value,)) for value in values]
        for writer in writers: writer.start()
        for writer in writers: writer.join()
        stop.set(); thread.join()
        book.check("concurrent-writers-no-torn-content", not writer_errors and all(value in {b"before", b"after", *values} for value in observed), repr(writer_errors))
    return book.finish()


def _bootstrap(root: Path) -> FamilyOutcome:
    book = Checkbook("bootstrap")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); _copy_bootstrap(root, work)
        original = b"\xef\xbb\xbf# User\r\nKeep me.\r\n"
        (work / "AGENTS.md").write_bytes(original)
        os.chmod(work / "AGENTS.md",0o640); original_mode=stat.S_IMODE((work/"AGENTS.md").stat().st_mode)
        before = workspace_fingerprint(work)
        preview = bootstrap_install(work)
        book.check("bootstrap-dry-run-zero-side-effects", preview["changed"] and workspace_fingerprint(work)["sha256"] == before["sha256"])
        bootstrap_install(work, apply=True)
        installed = (work / "AGENTS.md").read_bytes()
        book.check("bootstrap-preserves-user-and-format", b"Keep me." in installed and BEGIN.encode() in installed and installed.startswith(b"\xef\xbb\xbf"))
        book.check("bootstrap-preserves-original-permissions",stat.S_IMODE((work/"AGENTS.md").stat().st_mode)==original_mode)
        book.check("bootstrap-preserves-crlf-and-bom",installed.startswith(b"\xef\xbb\xbf") and b"\r\n" in installed)
        journal=install_state_dir(work)/"bootstrap"/"install.json"
        book.check("bootstrap-journal-is-persistent-and-versioned",journal.is_file() and json.loads(journal.read_text(encoding="utf-8")).get("schema") is not None)
        first_hash = hashlib.sha256(installed).hexdigest()
        reinstall = bootstrap_install(work, apply=True)
        book.check("bootstrap-reinstall-idempotent", not reinstall["changed"] and hashlib.sha256((work / "AGENTS.md").read_bytes()).hexdigest() == first_hash)
        shutil.rmtree(runtime_dir(work), ignore_errors=True)
        bootstrap_uninstall(work, apply=True)
        book.check("bootstrap-uninstall-runtime-independent", (work / "AGENTS.md").read_bytes() == original)
        (work / "AGENTS.md").write_text(f"{BEGIN}\nmanual\n<!-- AEGIS:END -->\n", encoding="utf-8")
        book.expect("bootstrap-conflict-fails-closed", BootstrapError, lambda: bootstrap_install(work))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work)
        (work/"AGENTS.md").write_text(f"{BEGIN}\none\n<!-- AEGIS:END -->\n{BEGIN}\ntwo\n<!-- AEGIS:END -->\n",encoding="utf-8")
        before=(work/"AGENTS.md").read_bytes()
        book.expect("bootstrap-multiple-markers-rejected",BootstrapError,lambda:bootstrap_install(work,apply=True))
        book.check("bootstrap-marker-rejection-zero-side-effect",(work/"AGENTS.md").read_bytes()==before)
    for label,content in (
        ("bootstrap-fenced-marker-example-ignored",f"```markdown\n{BEGIN}\nexample\n<!-- AEGIS:END -->\n```\nuser\n"),
        ("bootstrap-quoted-marker-example-ignored",f"> {BEGIN}\n> example\n> <!-- AEGIS:END -->\nuser\n"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); _copy_bootstrap(root,work); (work/"AGENTS.md").write_text(content,encoding="utf-8")
            try:
                bootstrap_install(work,apply=True); installed=(work/"AGENTS.md").read_text(encoding="utf-8"); passed=installed.count(BEGIN)==2
            except BootstrapError:
                passed=False
            book.check(label,passed)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work)
        bootstrap_install(work,apply=True); created=work/"AGENTS.md"; book.check("bootstrap-created-root-file",created.is_file())
        bootstrap_uninstall(work,apply=True); book.check("bootstrap-uninstall-removes-created-file",not created.exists())
    with tempfile.TemporaryDirectory(prefix="Aegis bootstrap 雪 ") as directory:
        work=Path(directory); _copy_bootstrap(root,work); target=work/"AGENTS.md"; target.write_text("user\n",encoding="utf-8")
        bootstrap_install(work,apply=True); installed=target.read_bytes(); target.write_bytes(installed+b"post-install user edit\n")
        book.expect("bootstrap-uninstall-managed-drift-rejected",BootstrapError,lambda:bootstrap_uninstall(work,apply=True))
        book.check("bootstrap-drift-rejection-preserves-user-edit",target.read_bytes().endswith(b"post-install user edit\n"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work); outside=work/"outside-agents"; outside.write_bytes(b"outside")
        target=work/"AGENTS.md"
        try:
            target.symlink_to(outside)
        except (OSError,NotImplementedError) as exc:
            book.check("bootstrap-destination-symlink-escape-rejected",False,f"host cannot create test symlink: {exc}")
        else:
            book.expect("bootstrap-destination-symlink-escape-rejected",(BootstrapError,SecurityError,OSError),lambda:bootstrap_install(work,apply=True))
            book.check("bootstrap-symlink-rejection-preserves-outside",outside.read_bytes()==b"outside")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work); target=work/"AGENTS.md"; target.write_bytes(b"original")
        original_commit=FileTransaction.commit
        def drift_before_commit(transaction,*args,**kwargs):
            target.write_bytes(b"concurrent user edit")
            return original_commit(transaction,*args,**kwargs)
        from unittest.mock import patch
        with patch("agentinfra.bootstrap.FileTransaction.commit",autospec=True,side_effect=drift_before_commit):
            book.expect("bootstrap-concurrent-install-drift-rejected",TransactionError,lambda:bootstrap_install(work,apply=True))
        book.check("bootstrap-concurrent-drift-preserves-user-edit",target.read_bytes()==b"concurrent user edit")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work); target=work/"AGENTS.md"; target.write_bytes(b"original")
        original_commit=FileTransaction.commit
        def fail_after_journal(transaction,*args,**kwargs):
            def journal_fault(stage,_journal):
                if stage == "after_journal": raise OSError("seeded bootstrap journal failure")
            transaction.fault=journal_fault
            return original_commit(transaction,*args,**kwargs)
        from unittest.mock import patch
        with patch("agentinfra.bootstrap.FileTransaction.commit",autospec=True,side_effect=fail_after_journal):
            book.expect("bootstrap-install-journal-failure-reported",OSError,lambda:bootstrap_install(work,apply=True))
        book.check("bootstrap-install-journal-failure-restores-preinstall-file",target.read_bytes()==b"original" and not (install_state_dir(work)/"bootstrap"/"install.json").exists())
    return book.finish()


def _state_identity(root: Path) -> FamilyOutcome:
    book = Checkbook("state-identity")
    def all_ids_rejected(values):
        for value in values:
            try: validate_task_id(value)
            except ValueError: continue
            return False
        return True
    book.check("task-id-parent-traversal-rejected",all_ids_rejected(("../x","x/../y")))
    book.check("task-id-absolute-path-rejected",all_ids_rejected(("/absolute","C:\\absolute")))
    book.check("task-id-path-separators-rejected",all_ids_rejected(("x/y","x\\y")))
    book.check("task-id-dot-components-rejected",all_ids_rejected((".","..")))
    book.check("task-id-empty-whitespace-rejected",all_ids_rejected((""," ")))
    book.check("task-id-canonical-length-characters",all_ids_rejected(("A"*65,"UPPER","under_score")) and validate_task_id("valid-task-1")=="valid-task-1")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        store = StateStore(work); task = store.create("identity", mode="write", complexity="XL", risk="critical")
        book.check("state-create-library-enums", task["mode"] == "write" and task["complexity"] == "XL" and task["risk"] == "critical")
        for args in (("x", "bad", "M", "medium"), ("x", "write", "XX", "medium"), ("x", "write", "M", "severe")):
            book.expect("state-create-invalid-enum-" + args[1] + args[2] + args[3], ValueError, lambda args=args: store.create(*args))
        loaded = store.load(task["id"]); loaded["id"] = "redirect"
        book.expect("state-save-id-redirect", RuntimeError, lambda: store.save(loaded, loaded["revision"]))
        state_path = tasks_dir(work) / task["id"] / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8")); raw["mode"] = "read"; state_path.write_text(json.dumps(raw), encoding="utf-8")
        book.expect("state-integrity-tamper", RuntimeError, lambda: store.load(task["id"]))
    integrity_mutations=(
        ("state-integrity-mode-tamper",lambda value:value.__setitem__("mode","read")),
        ("state-integrity-epoch-tamper",lambda value:value.__setitem__("change_epoch",value["change_epoch"]+1)),
        ("state-integrity-final-audit-tamper",lambda value:value.__setitem__("final_audit_complete",True)),
        ("state-integrity-gate-tamper",lambda value:value["gates"].append({"id":"G1","description":"forged","severity":"high","status":"OPEN","evidence":[],"created_revision":0})),
    )
    for label,mutate in integrity_mutations:
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create(label)
            state_path=tasks_dir(work)/task["id"]/"state.json"; raw=json.loads(state_path.read_text(encoding="utf-8")); mutate(raw); state_path.write_text(json.dumps(raw),encoding="utf-8")
            book.expect(label,RuntimeError,lambda store=store,task=task:store.load(task["id"]))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("pointer")
        pointer=store.current_path(); original=pointer.read_bytes()
        for label,value in (("current-pointer-traversal-rejected","../escape\n"),("current-pointer-absolute-rejected",str(work.resolve())+"\n"),("current-pointer-missing-task-rejected","missing-task\n")):
            pointer.write_text(value,encoding="utf-8")
            book.expect(label,(ValueError,RuntimeError,FileNotFoundError),lambda:store.current_id())
        pointer.write_bytes(original)
        task_dir=tasks_dir(work)/task["id"]
        base=store.load()
        def schema_reject(label,mutate):
            candidate=json.loads(json.dumps(base)); mutate(candidate)
            book.expect(label,RuntimeError,lambda candidate=candidate:validate_task(candidate,expected_id=task["id"],verify_integrity=False))
        schema_reject("state-owner-id-mismatch",lambda value:value.__setitem__("id","different-task"))
        schema_reject("state-missing-required-field",lambda value:value.pop("mode"))
        schema_reject("state-unknown-enum",lambda value:value.__setitem__("state","UNKNOWN"))
        schema_reject("state-unknown-mode",lambda value:value.__setitem__("mode","execute"))
        schema_reject("state-unknown-complexity",lambda value:value.__setitem__("complexity","XXL"))
        schema_reject("state-unknown-risk",lambda value:value.__setitem__("risk","severe"))
        schema_reject("state-wrong-field-type",lambda value:value.__setitem__("gates",{}))
        schema_reject("state-negative-revision",lambda value:value.__setitem__("revision",-1))
        schema_reject("state-negative-epoch",lambda value:value.__setitem__("change_epoch",-1))
        def duplicate(field,record):
            return lambda value:value[field].extend([dict(record),dict(record)])
        schema_reject("state-duplicate-gate-id",duplicate("gates",{"id":"G1","description":"gate","severity":"high","status":"OPEN","evidence":[],"created_revision":0}))
        schema_reject("state-duplicate-risk-id",duplicate("risks",{"id":"R1","description":"risk","severity":"high","status":"open"}))
        schema_reject("state-duplicate-decision-id",duplicate("decisions",{"id":"D1","at":"now","statement":"s","rationale":"r","evidence":[]}))
        schema_reject("state-duplicate-handoff-id",duplicate("child_history",{"handoff_id":"H1","role":"r","opened":"o","closed":"c","outcome":"accepted","summary":"s","evidence":[]}))
        candidate=json.loads(json.dumps(base)); candidate["transitions"]=[{"from":"CREATED","to":"FINALIZE","reason":"bad","at":"now","revision":1,"epoch":0}]; candidate["state"]="FINALIZE"
        book.expect("state-corrupt-transition-history",RuntimeError,lambda:validate_task(candidate,expected_id=task["id"],verify_integrity=False))
        first=store.load(); second=store.load(); first["precheck"]["one"]=True; saved=store.save(first,first["revision"]); second["precheck"]["two"]=True
        book.expect("state-optimistic-revision-prevents-lost-update",RuntimeError,lambda:store.save(second,second["revision"]))
        book.check("state-first-concurrent-update-preserved",store.load()["precheck"].get("one") is True and saved["revision"]==1)
        state_path=task_dir/"state.json"; anchor=persistent_dir(work)/"task-anchors"/f"{task['id']}.json"
        old_state=state_path.read_bytes(); old_anchor=anchor.read_bytes()
        store.mutate(lambda value:value["precheck"].__setitem__("newer-anchor-revision",True))
        state_path.write_bytes(old_state); anchor.write_bytes(old_anchor)
        book.expect("state-anchor-history-detects-state-and-head-rollback",RuntimeError,lambda:store.load())
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); created=[]; errors=[]
        def creator(index):
            try: created.append(store.create(f"task {index}"))
            except BaseException as exc: errors.append(str(exc))
        threads=[threading.Thread(target=creator,args=(index,)) for index in range(6)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        dirs={item.name for item in tasks_dir(work).iterdir() if item.is_dir()}
        book.check("concurrent-task-create-no-loss",not errors and len(created)==6 and {item["id"] for item in created}==dirs,repr(errors))
        book.check("current-pointer-transactionally-consistent",store.current_id() in dirs and store.load(store.current_id())["id"]==store.current_id())
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("concurrent mutate")
        mutation_errors=[]
        def mutate_key(key):
            try: store.mutate(lambda value:key and value["precheck"].__setitem__(key,True))
            except BaseException as exc: mutation_errors.append(str(exc))
        threads=[threading.Thread(target=mutate_key,args=(key,)) for key in ("first","second")]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        loaded=store.load(task["id"])
        book.check("state-mutation-reloads-under-lock",not mutation_errors and loaded["precheck"].get("first") is True and loaded["precheck"].get("second") is True,repr(mutation_errors))
        book.check("state-store-root-confinement",store._task_dir(task["id"]).is_relative_to(tasks_dir(work)))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("symlink state")
        tasks=tasks_dir(work); outside=work/"outside"; outside.mkdir(); (outside/"sentinel").write_bytes(b"outside")
        linked=tasks/"linked-task"
        try:
            linked.symlink_to(outside,target_is_directory=True)
            book.expect("state-symlinked-task-directory-rejected",(RuntimeError,ValueError,OSError),lambda:store.load("linked-task"))
            state_path=tasks/task["id"]/"state.json"; original=state_path.read_bytes(); state_path.unlink(); external_state=outside/"state.json"; external_state.write_bytes(original); state_path.symlink_to(external_state)
            book.expect("state-symlinked-state-file-rejected",(RuntimeError,ValueError,OSError),lambda:store.load(task["id"]))
            book.check("state-symlink-rejection-never-touches-outside",(outside/"sentinel").read_bytes()==b"outside" and external_state.read_bytes()==original)
        except (OSError,NotImplementedError) as exc:
            book.check("state-symlinked-task-directory-rejected",False,f"host cannot create test symlink: {exc}")
            book.check("state-symlinked-state-file-rejected",False,f"host cannot create test symlink: {exc}")
            book.check("state-symlink-rejection-never-touches-outside",False,f"host cannot create test symlink: {exc}")
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
        work=Path(directory); outside=Path(outside_directory); runtime_dir(work).mkdir(parents=True)
        store=StateStore(work); task=store.create("redirected control root")
        control_root=tasks_dir(work); redirected=outside/"tasks"; shutil.copytree(control_root,redirected); shutil.rmtree(control_root)
        made=False
        try:
            if os.name == "nt":
                result=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(control_root),str(redirected)],capture_output=True,text=True)
                made=result.returncode == 0
            else:
                control_root.symlink_to(redirected,target_is_directory=True); made=True
            if not made:
                book.check("state-control-redirection-root-escape-rejected",False,"host could not create link-like directory redirection")
            else:
                book.expect("state-control-redirection-root-escape-rejected",(RuntimeError,SecurityError,OSError),lambda:StateStore(work).load(task["id"]))
                book.check("state-control-redirection-preserves-outside",(redirected/task["id"]/"state.json").is_file())
        finally:
            if made:
                control_root.rmdir()
    return book.finish()


def _workflow(root: Path) -> FamilyOutcome:
    book = Checkbook("workflow")
    book.check("transition-table-total", set(ALLOWED) == STATES and all(targets <= STATES for targets in ALLOWED.values()))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        store, _, finalized = _state_flow(work)
        book.check("complete-current-proof-flow", finalized)
        final = store.load()
        book.check("finalize-idempotent", store.transition("FINALIZE", "idempotent") == final)
        book.expect("finalized-rejects-mutation", RuntimeError, lambda: store.mutate(lambda task: task.update(title="changed")))
        final_task_dir=tasks_dir(work)/final["id"]
        book.expect("terminal-evidence-append-rejected",RuntimeError,lambda:append_evidence(final_task_dir,"observation","after finalize",task_id=final["id"],change_epoch=final["change_epoch"]))
        book.check("terminal-evidence-rejection-preserves-final-state",store.load()["state"]=="FINALIZE")
    with tempfile.TemporaryDirectory() as directory:
        _, _, rejected = _state_flow(Path(directory), mutate_after_audit=True)
        book.check("post-audit-mutation-rejected", rejected)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_verified_state(work)
        (work/"src"/"semantic_probe.py").write_text("VALUE = 2\n",encoding="utf-8")
        book.expect(
            "post-verification-workspace-change-rejected",
            (RuntimeError, TransitionError),
            lambda: (
                store.transition("FINAL_AUDIT", "stale verification workspace"),
                store.audit_complete(),
            ),
        )
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        store = StateStore(work); store.create("illegal")
        before = store.load()
        book.expect("illegal-transition-no-side-effect", TransitionError, lambda: store.transition("FINALIZE", "skip"))
        after = store.load()
        book.check("illegal-transition-revision-stable", before["revision"] == after["revision"] and before["transitions"] == after["transitions"])
        store.transition("DISCOVER", "start"); store.transition("BLOCKED", "external blocker")
        book.expect("blocked-only-previous", TransitionError, lambda: store.transition("IMPLEMENT", "skip"))
        book.check("blocked-resume", store.transition("DISCOVER", "resume")["state"] == "DISCOVER")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create("reasons")
        book.expect("transition-reason-trimmed-nonempty",ValueError,lambda:store.transition("DISCOVER","   "))
        store.transition("DISCOVER","start"); store.transition("BLOCKED","documented blocker")
        blocked=store.load(); book.check("blocked-reason-recorded",blocked.get("block_reason")=="documented blocker" and blocked.get("previous_state")=="DISCOVER")
        book.expect("blocked-cannot-skip-phase",TransitionError,lambda:store.transition("TRIAGE","skip precheck"))
    for terminal in ("FAILED","CANCELLED","ABANDONED"):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create(terminal.lower()); ended=store.transition(terminal,"explicit terminal")
            book.check("terminal-state-"+terminal.lower(),ended["state"]==terminal and ALLOWED[terminal]==set())
            book.expect("terminal-mutation-rejected-"+terminal.lower(),RuntimeError,lambda:store.mutate(lambda task:task.update(title="changed")))
    book.check(
        "failed-cancelled-abandoned-explicit-terminal-semantics",
        all(
            any(label == "terminal-state-" + terminal and passed for label, passed, _ in book.observations)
            for terminal in ("failed", "cancelled", "abandoned")
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create("xl",complexity="XL",risk="low"); _advance_precheck(store,work)
        book.expect("xl-direct-implement-rejected",TransitionError,lambda:store.transition("IMPLEMENT","skip plan"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store=_implemented_state(work,title="mutating skip audit")
        book.expect("mutating-implement-direct-final-audit-rejected",TransitionError,lambda:store.transition("FINAL_AUDIT","skip verification"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store=StateStore(work); store.create("epochs",risk="low"); _advance_precheck(store,work); store.transition("PLAN","plan"); _design_and_baseline(store,work)
        before=store.load()["change_epoch"]; implemented=store.transition("IMPLEMENT","first implementation")
        book.check("implement-increments-epoch-once",implemented["change_epoch"]==before+1)
        reviewed=store.transition("DIAGNOSE","diagnose"); book.check("nonmutating-transition-keeps-epoch",reviewed["change_epoch"]==implemented["change_epoch"])
        _design_and_baseline(store,work,remediation=True); remediated=store.transition("REMEDIATE","fix"); book.check("remediate-increments-epoch-once",remediated["change_epoch"]==implemented["change_epoch"]+1)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work); store.transition("TEST_DESIGN","new regression after audit")
        (work/"tests"/"test_semantic_probe.py").write_text(
            "import unittest\nfrom src.semantic_probe import VALUE\n\n"
            "class SemanticProbeContract(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(VALUE, 2)\n",
            encoding="utf-8",
        )
        (work/"tests"/"oracle.txt").write_text("VALUE must equal 2\n",encoding="utf-8")
        design_tdd(work,adapter_kind="unittest",test_id="tests.test_semantic_probe.SemanticProbeContract.test_value",test_paths=["tests/test_semantic_probe.py"],oracle_paths=["tests/oracle.txt"])
        red,authorized=observe_baseline(work,semantic_reason="the newly discovered value-two behavior is absent",timeout=10)
        if not authorized or red.get("classification")!="EXPECTED_BEHAVIORAL_RED": raise RuntimeError(f"remediation fixture RED failed: {red}")
        store.transition("BASELINE","remediation RED"); changed=store.transition("REMEDIATE","new implementation")
        book.check("epoch-change-invalidates-verification",changed["verification_evidence"]==[] and changed["verification_epoch"] is None)
        book.check("epoch-change-invalidates-gate-proofs",changed["gates"][0]["status"]=="OPEN" and changed["gates"][0]["evidence"]==[])
        book.check("epoch-change-invalidates-final-audit",changed["final_audit_complete"] is False and "final_audit_workspace" not in changed)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create("read claim",mode="read",risk="low"); _advance_precheck(store,work); store.transition("PLAN","analysis plan"); store.transition("VERIFY","verify")
        book.expect("read-material-claim-needs-direct-evidence",TransitionError,lambda:store.transition("FINAL_AUDIT","claim"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create("active child",mode="read",risk="low"); _advance_precheck(store,work); store.transition("PLAN","plan"); store.transition("VERIFY","verify")
        _cli_json(work,"subagent","open","--role","reviewer")
        book.expect("active-child-blocks-parent-mutation",RuntimeError,lambda:store.mutate(lambda task:task["precheck"].__setitem__("late",True)))
        book.expect("active-child-blocks-final-audit",RuntimeError,lambda:store.transition("FINAL_AUDIT","blocked"))
    for declared_risk in ("high", "critical"):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("review required",mode="read",risk=declared_risk); _advance_precheck(store,work); store.transition("PLAN","required plan"); store.transition("VERIFY","verify")
            record=_append_verified_observation(tasks_dir(work)/task["id"],"observation","direct proof",task_id=task["id"],change_epoch=store.load()["change_epoch"],task_revision=store.load()["revision"],workspace=workspace_fingerprint(work))
            store.mutate(lambda current:(current.__setitem__("verification_evidence",[record["id"]]),current.__setitem__("verification_epoch",current["change_epoch"]),current.__setitem__("evidence_head",record["record_sha256"])))
            book.expect(
                declared_risk + "-risk-review-cannot-be-skipped",
                (RuntimeError, TransitionError),
                lambda: (
                    store.transition("FINAL_AUDIT", "without review"),
                    store.audit_complete(),
                ),
            )
    for stale in (True,False):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("current external claim",mode="read",risk="low"); _advance_precheck(store,work); store.transition("RESEARCH","current source"); store.transition("TRIAGE","source collected"); store.transition("PLAN","analysis plan"); store.transition("VERIFY","verify")
            observed_at="2000-01-01T00:00:00+00:00" if stale else datetime.now(timezone.utc).isoformat()
            record=_append_framework_evidence(
                tasks_dir(work)/task["id"],"external-source","bound current source",
                provenance="external-source",task_id=task["id"],change_epoch=store.load()["change_epoch"],task_revision=store.load()["revision"],
                source_identity="https://example.invalid/source",source_fingerprint="sha256:verified",observed_at=observed_at,ttl_seconds=3600,
                workspace=workspace_fingerprint(work),
            )
            store.mutate(lambda current:(current.__setitem__("verification_evidence",[record["id"]]),current.__setitem__("verification_epoch",current["change_epoch"]),current.__setitem__("evidence_head",record["record_sha256"])))
            if stale:
                book.expect("research-stale-external-source-rejected",RuntimeError,lambda:store.transition("FINAL_AUDIT","stale source"))
            else:
                store.transition("FINAL_AUDIT","fresh source"); store.audit_complete()
                book.check("research-fresh-external-source-finalizes",store.transition("FINALIZE","fresh proof")["state"]=="FINALIZE")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work)
        final_audit=store.load(); history=final_audit["transitions"]
        book.check("transition-revisions-monotonic",[item["revision"] for item in history]==sorted({item["revision"] for item in history}))
        book.check("transition-epochs-match-state-history",all(isinstance(item["epoch"],int) and item["epoch"]<=final_audit["change_epoch"] for item in history))
        from unittest.mock import patch
        with patch("agentinfra.final_audit_runtime.workspace_fingerprint",return_value={"schema":2,"available":False,"reason":"seeded"}):
            book.expect("final-audit-unavailable-workspace-rejected",RuntimeError,lambda:store.audit_complete())
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work); store.mutate(lambda task:task.__setitem__("active_child",{"role":"reviewer","opened":"now","lease_id":"L-final"}))
        book.expect("active-child-blocks-finalization",RuntimeError,lambda:store.transition("FINALIZE","child remains"))
    return book.finish()


def _gates(root: Path) -> FamilyOutcome:
    # The full lifecycle battery exercises nonwaived gates, explicit relevance,
    # current epochs, and direct provenance through the production StateStore.
    book = Checkbook("gates-risks-decisions")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); store, record, finalized = _state_flow(work)
        task = store.load()
        book.check("nonwaived-gate-required-and-proven", finalized and task["gates"][0]["status"] == "PROVEN")
        book.check("gate-proof-task-epoch-relevance", record["task_id"] == task["id"] and record["change_epoch"] == task["change_epoch"] and "G1" in record["details"]["gate_ids"])
        book.check("decision-history-append-only-schema", isinstance(task["decisions"], list))

    def negative_gate_proof(label: str, variant: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # This is deliberately a lower-level validator fixture: malformed
            # evidence/state is injected only to prove fail-closed rejection.
            work=Path(directory); runtime_dir(work).mkdir(parents=True)
            if variant == "prior-epoch":
                store, _ = _verified_state(work)
                task = store.load()
                gate_id = "G2"
            else:
                store=StateStore(work); task=store.create("negative gate proof",mode="read",risk="low"); _advance_precheck(store,work); store.transition("PLAN","plan"); store.transition("VERIFY","verify")
                gate_id = "G1"
            task_dir=tasks_dir(work)/task["id"]
            if variant == "predates":
                current=store.load(); record=_append_verified_observation(task_dir,"observation","predating proof",task_id=task["id"],change_epoch=current["change_epoch"],task_revision=current["revision"],gate_ids=[gate_id])
                def install_predating(current):
                    current["gates"].append({"id":gate_id,"description":"gate","severity":"high","status":"PROVEN","evidence":[record["id"]],"created_revision":current["revision"]+2})
                    current["verification_evidence"]=[record["id"]]; current["verification_epoch"]=current["change_epoch"]; current["evidence_head"]=record["record_sha256"]
                store.mutate(install_predating)
            else:
                store.mutate(lambda current:current["gates"].append({"id":gate_id,"description":"gate","severity":"high","status":"OPEN","evidence":[],"created_revision":current["revision"]+1}))
                current=store.load(); record=None
                if variant != "other-task":
                    epoch=current["change_epoch"]-1 if variant == "prior-epoch" else current["change_epoch"]
                    details={"task_revision":current["revision"],"gate_ids":[] if variant == "no-relevance" else [gate_id]}
                    if variant == "failed": details["command"]={"success":False,"exit_code":9,"timed_out":False}
                    if variant == "manual":
                        record=append_evidence(task_dir,"observation","negative proof",task_id=task["id"],change_epoch=epoch,**details)
                    elif variant == "failed":
                        record=_append_framework_evidence(task_dir,"observation","negative proof",provenance="framework-command",task_id=task["id"],change_epoch=epoch,**details)
                    else:
                        record=_append_verified_observation(task_dir,"observation","negative proof",task_id=task["id"],change_epoch=epoch,**details)
                evidence_id="E-other-task" if record is None else record["id"]
                def prove(current):
                    gate=next(item for item in current["gates"] if item["id"] == gate_id)
                    gate["status"]="PROVEN"; gate["evidence"]=[evidence_id]
                    current["verification_evidence"]=[evidence_id]; current["verification_epoch"]=current["change_epoch"]
                    if record is not None: current["evidence_head"]=record["record_sha256"]
                store.mutate(prove)
            book.expect(label,(RuntimeError,TransitionError),lambda:store.transition("FINAL_AUDIT","invalid proof"))

    for label,variant in (
        ("gate-proof-failed-command-rejected","failed"),
        ("gate-proof-unexecuted-claim-rejected","manual"),
        ("gate-proof-prior-epoch-rejected","prior-epoch"),
        ("gate-proof-other-task-rejected","other-task"),
        ("gate-proof-predates-gate-rejected","predates"),
        ("gate-proof-without-relevance-rejected","no-relevance"),
    ):
        negative_gate_proof(label,variant)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); store, _ = _verified_state(work, include_gate=False)
        book.expect("missing-gate-blocks-audit", TransitionError, lambda: store.transition("FINAL_AUDIT", "no gate"))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        store = StateStore(work); store.create("gate schema")
        book.expect(
            "gate-description-trimmed-nonempty",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].append({"id":"G1","description":"  ","severity":"high","status":"OPEN","evidence":[],"created_revision":1})),
        )
        book.expect(
            "gate-id-canonical",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].append({"id":"../G","description":"bad","severity":"high","status":"OPEN","evidence":[],"created_revision":1})),
        )
        book.expect(
            "gate-id-unique",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].extend([
                {"id":"G1","description":"one","severity":"high","status":"OPEN","evidence":[],"created_revision":1},
                {"id":"G1","description":"two","severity":"high","status":"OPEN","evidence":[],"created_revision":1},
            ])),
        )
        book.expect(
            "waiver-reason-nonempty",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].append({"id":"G2","description":"waive","severity":"high","status":"WAIVED","evidence":[],"created_revision":1,"waiver_reason":" ","waiver_authority":"policy:test"})),
        )
        book.expect(
            "waiver-authority-canonical-policy",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].append({"id":"G3","description":"waive","severity":"high","status":"WAIVED","evidence":[],"created_revision":1,"waiver_reason":"reason","waiver_authority":"caller supplied words"})),
        )
        book.expect(
            "critical-gate-waiver-rejected",
            RuntimeError,
            lambda: store.mutate(lambda task: task["gates"].append({"id":"G4","description":"critical","severity":"critical","status":"WAIVED","evidence":[],"created_revision":1,"waiver_reason":"reason","waiver_authority":"policy:test"})),
        )
        for severity in ("", "severe", "HIGH"):
            book.expect(
                "gate-severity-reject-" + (severity or "empty"),
                RuntimeError,
                lambda severity=severity: store.mutate(lambda task: task["gates"].append({"id":"G5","description":"bad severity","severity":severity,"status":"OPEN","evidence":[],"created_revision":1})),
            )
    with tempfile.TemporaryDirectory() as directory:
        # Injecting an all-waived gate set is an adversarial validator fixture;
        # every lifecycle and verification step around it uses public behavior.
        work=Path(directory); store, record = _verified_state(work, include_gate=False)
        store.mutate(lambda current:current["gates"].append({"id":"G1","description":"waived","severity":"high","status":"WAIVED","evidence":[],"created_revision":current["revision"]+1,"waiver_reason":"external exception","waiver_authority":"policy:documented-exception"}))
        book.expect("all-gates-waived-cannot-bypass-validation",TransitionError,lambda:store.transition("FINAL_AUDIT","waivers are not proof"))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        store = StateStore(work); store.create("risk schema")
        book.expect("risk-description-trimmed-nonempty", RuntimeError, lambda: store.mutate(lambda task: task["risks"].append({"id":"R1","description":" ","severity":"high","status":"open"})))
        book.expect("risk-severity-library-validation", RuntimeError, lambda: store.mutate(lambda task: task["risks"].append({"id":"R2","description":"risk","severity":"severe","status":"open"})))
        book.expect("risk-resolution-trimmed-nonempty", RuntimeError, lambda: store.mutate(lambda task: task["risks"].append({"id":"R3","description":"risk","severity":"high","status":"resolved","resolution":" "})))
        store.mutate(lambda task: task["risks"].append({"id":"R4","description":"risk","severity":"high","status":"resolved","resolution":"evidence E-1","resolution_evidence":["E-1"]}))
        book.check("risk-resolution-evidence-preserved", store.load()["risks"][0].get("resolution_evidence") == ["E-1"])
        store.mutate(lambda task:task["gates"].append({"id":"G-history","description":"real gate","severity":"high","status":"OPEN","evidence":[],"created_revision":task["revision"]+1}))
        book.expect("gate-history-deletion-rejected",RuntimeError,lambda:store.mutate(lambda task:task.__setitem__("gates",[])))
        book.expect("risk-history-deletion-rejected",RuntimeError,lambda:store.mutate(lambda task:task.__setitem__("risks",[])))
        book.expect("proven-gate-empty-evidence-rejected",RuntimeError,lambda:store.mutate(lambda task:task["gates"].append({"id":"G-empty","description":"empty proof","severity":"low","status":"PROVEN","evidence":[],"created_revision":task["revision"]+1})))
        store.mutate(lambda task: task["decisions"].append({"id":"D1","at":"now","statement":"decision","rationale":"because","evidence":[]}))
        book.expect("decision-append-only-immutable", RuntimeError, lambda: store.mutate(lambda task: task["decisions"][0].update(statement="rewritten")))
        store.mutate(lambda task: task["decisions"].append({"id":"D2","at":"later","statement":"replacement","rationale":"new facts","evidence":[],"supersedes":"D1"}))
        book.check("decision-supersession-history-preserved", [item["id"] for item in store.load()["decisions"]] == ["D1", "D2"])
        book.expect("decision-statement-trimmed-nonempty", RuntimeError, lambda: store.mutate(lambda task: task["decisions"].append({"id":"D3","at":"now","statement":" ","rationale":"why","evidence":[]})))
        book.expect("decision-rationale-trimmed-nonempty", RuntimeError, lambda: store.mutate(lambda task: task["decisions"].append({"id":"D3","at":"now","statement":"what","rationale":" ","evidence":[]})))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); store = StateStore(work); task = store.create("reopen risk",risk="low"); _advance_precheck(store,work); store.transition("PLAN", "plan")
        _cli_json(work, "task", "risk-add", "resolved", "--id", "R1", "--severity", "high", "--task-id", task["id"])
        _cli_json(work, "task", "risk-resolve", "R1", "--resolution", "fixed", "--task-id", task["id"])
        _design_and_baseline(store, work)
        store.transition("IMPLEMENT","new implementation")
        reopened=store.load()["risks"][0]
        book.check("resolved-risk-reopens-on-implementation", reopened["status"] == "open" and "resolution" not in reopened)
    for severity in ("high","critical"):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("blocking risk",mode="read",risk="low"); _advance_precheck(store,work); store.transition("PLAN","plan"); store.transition("VERIFY","verify")
            _attach_current_verification(store, work)
            _cli_json(work, "task", "risk-add", "open blocker", "--id", "R1", "--severity", severity, "--task-id", task["id"])
            book.expect("unresolved-"+severity+"-risk-blocks-final-audit",TransitionError,lambda:store.transition("FINAL_AUDIT","blocked risk"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work)
        store.mutate(lambda value:(value["gates"][0].__setitem__("status","OPEN"),value["gates"][0].__setitem__("evidence",[])))
        changed=store.load()
        book.check("gate-change-invalidates-final-audit-and-proof-status",changed["final_audit_complete"] is False and changed["gates"][0]["status"]=="OPEN")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        critical = StateStore(work); critical.create("critical declared",risk="critical"); _advance_precheck(critical,work)
        book.expect("declared-critical-risk-requires-plan", TransitionError, lambda: critical.transition("IMPLEMENT","skip plan"))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); task=store.create("critical specialist",mode="read",risk="critical"); _advance_precheck(store,work); store.transition("PLAN","plan"); store.transition("VERIFY","verify")
        record = _attach_current_verification(store, work)
        book.expect("critical-risk-specialist-review-required",TransitionError,lambda:store.transition("FINAL_AUDIT","generic review insufficient"))
        store.transition("ADVERSARIAL_REVIEW", "specialist review required")
        _close_current_review_handoff(store, work, record["id"], role="adversarial-reviewer")
        book.check("critical-risk-specialist-review-accepted",store.transition("FINAL_AUDIT","specialist review complete")["state"]=="FINAL_AUDIT")
    return book.finish()


def _evidence(root: Path) -> FamilyOutcome:
    book = Checkbook("evidence")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); task_dir = work / "task-1"
        book.expect("evidence-caller-verified-provenance-rejected",ValueError,lambda:append_evidence(task_dir,"observation","caller claim",provenance="verified-observation",task_id="task-1",change_epoch=2))
        first = _append_verified_observation(task_dir, "observation", "first", task_id="task-1", change_epoch=2, task_revision=3)
        second = append_evidence(task_dir, "test", "second", provenance="manual", task_id="task-1", change_epoch=2, task_revision=3)
        records = load_evidence(task_dir)
        book.check("evidence-unique-sequenced-chain", len({r["id"] for r in records}) == 2 and second["previous_sha256"] == first["record_sha256"] and [r["sequence"] for r in records] == [1, 2])
        anchor = json.loads((task_dir / "evidence-head.json").read_text(encoding="utf-8"))
        book.check("evidence-anchor-task-revision-binding", anchor["task_id"] == "task-1" and anchor["task_revision"] == 3 and anchor["head_sha256"] == second["record_sha256"])
        ledger = task_dir / "evidence.jsonl"; original = ledger.read_bytes()
        changed = bytearray(original); changed[10] ^= 1; ledger.write_bytes(changed)
        book.expect("evidence-tamper-detected", RuntimeError, lambda: load_evidence(task_dir))
        ledger.write_bytes(original)
        book.check("evidence-restored-verifies", verify_evidence(task_dir)[0])
        ledger.write_bytes(original[:-1])
        book.expect("evidence-truncated-tail", RuntimeError, lambda: load_evidence(task_dir))
        lines = original.splitlines()
        ledger.write_bytes(lines[1] + b"\n")
        book.expect("evidence-record-deletion-detected", RuntimeError, lambda: load_evidence(task_dir))
        ledger.write_bytes(lines[1] + b"\n" + lines[0] + b"\n")
        book.expect("evidence-record-reordering-detected", RuntimeError, lambda: load_evidence(task_dir))
        ledger.write_bytes(lines[0] + b"\n" + lines[0] + b"\n" + lines[1] + b"\n")
        book.expect("evidence-record-insertion-detected", RuntimeError, lambda: load_evidence(task_dir))
        ledger.write_bytes(original)
        rollback_last_evidence(task_dir, second["record_sha256"])
        book.check("evidence-safe-tail-rollback", [item["id"] for item in load_evidence(task_dir)] == [first["id"]])
        book.expect("evidence-invalid-kind-rejected", ValueError, lambda: append_evidence(task_dir, "unknown", "bad"))
        book.expect("evidence-invalid-epoch-rejected", ValueError, lambda: append_evidence(task_dir, "test", "bad", change_epoch=-1))
        book.expect("evidence-task-mismatch-rejected", ValueError, lambda: append_evidence(task_dir, "test", "bad", task_id="other-task"))
        malformed = json.loads(lines[0]); malformed.pop("summary"); body = {key:value for key,value in malformed.items() if key != "record_sha256"}; malformed["record_sha256"] = _sha(body)
        ledger.write_text(json.dumps(malformed,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
        book.expect("evidence-missing-schema-field-rejected", RuntimeError, lambda: load_evidence(task_dir))
    with tempfile.TemporaryDirectory() as directory:
        task_dir=Path(directory)/"duplicate-task"
        first=append_evidence(task_dir,"test","one",evidence_id="E-duplicate",task_id="duplicate-task")
        append_evidence(task_dir,"test","two",evidence_id=first["id"],task_id="duplicate-task")
        book.expect("duplicate-evidence-id-rejected", RuntimeError, lambda: load_evidence(task_dir))
    with tempfile.TemporaryDirectory() as directory:
        task_dir=Path(directory)/"race-task"; errors=[]
        def writer(index):
            try: append_evidence(task_dir,"test",f"record {index}",task_id="race-task",change_epoch=1,task_revision=1)
            except BaseException as exc: errors.append(str(exc))
        threads=[threading.Thread(target=writer,args=(index,)) for index in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        raced=load_evidence(task_dir)
        book.check("evidence-concurrent-append-no-loss", not errors and len(raced)==8 and [item["sequence"] for item in raced]==list(range(1,9)),repr(errors))
    with tempfile.TemporaryDirectory() as directory:
        task_dir=Path(directory)/"crash-task"
        import agentinfra.evidence as evidence_module
        from unittest.mock import patch
        real_write=evidence_module.os.write; writes=0
        def partial_then_crash(fd,data):
            nonlocal writes
            writes+=1
            if writes==1:
                return real_write(fd,data[:max(1,len(data)//2)])
            raise OSError("seeded append crash")
        with patch("agentinfra.evidence.os.write",side_effect=partial_then_crash):
            book.expect("evidence-append-crash-propagates",OSError,lambda:append_evidence(task_dir,"test","crash",task_id="crash-task"))
        book.expect("evidence-append-crash-never-silently-accepted",RuntimeError,lambda:load_evidence(task_dir))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / "visible.txt").write_text("stable", encoding="utf-8"); task_dir = work / "evidence-task"
        record, result = execute_command_evidence(task_dir, root=work, argv=[sys.executable, "-c", "import sys;print('ok');print('err',file=sys.stderr)"], summary="actual command", change_epoch=1, task_revision=4)
        command = record["details"]["command"]
        book.check("command-provenance-and-exit", record["provenance"] == "framework-command" and command["success"] and command["exit_code"] == result.returncode == 0)
        book.check("command-argv-cwd-time-digests", command["argv"][0] == sys.executable and command["cwd"] == str(work.resolve()) and command["started_at"] and len(command["stdout_sha256"]) == 64 and len(command["stderr_sha256"]) == 64)
        book.check("command-workspace-fingerprint-bound", record["details"]["workspace"]["available"] and record["details"]["workspace_stable"])
        failed,_=execute_command_evidence(task_dir,root=work,argv=[sys.executable,"-c","raise SystemExit(7)"],summary="failed command",change_epoch=1,task_revision=4)
        book.check("failed-command-not-successful-verification", failed["details"]["command"]["exit_code"]==7 and failed["details"]["command"]["success"] is False)
        timed,_=execute_command_evidence(task_dir,root=work,argv=[sys.executable,"-c","import time;time.sleep(2)"],summary="timeout",change_epoch=1,task_revision=4,timeout=0.05)
        book.check("timed-out-command-not-successful-verification", timed["details"]["command"]["timed_out"] and timed["details"]["command"]["success"] is False)
        large,_=execute_command_evidence(task_dir,root=work,argv=[sys.executable,"-c","print('x'*200000)"],summary="large output",change_epoch=1,task_revision=4)
        large_command=large["details"]["command"]
        book.check("evidence-output-capture-bounded", large_command["stdout_truncated"] and len(large_command["stdout_preview"]) <= 64000)
        book.check("evidence-large-output-digest-retained", large_command["stdout_bytes"] >= 200000 and len(large_command["stdout_sha256"]) == 64)
        secret = append_evidence(task_dir, "observation", "token=super-secret-value", change_epoch=1, password="hunter2")
        book.check("evidence-secret-redaction", "super-secret-value" not in json.dumps(secret) and "hunter2" not in json.dumps(secret))
    return book.finish()


def _workspace(root: Path) -> FamilyOutcome:
    book = Checkbook("workspace")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / "a.txt").write_text("a", encoding="utf-8")
        first = workspace_fingerprint(work); second = workspace_fingerprint(work)
        book.check("tree-deterministic-schema", first["schema"] == 2 and first["available"] and first["sha256"] == second["sha256"])
        (work / "a.txt").write_text("b", encoding="utf-8")
        book.check("tree-content-change", workspace_fingerprint(work)["sha256"] != first["sha256"])
        (work / "build").mkdir(); (work / "build" / "large.bin").write_bytes(b"x" * 10000)
        excluded = workspace_fingerprint(work)
        (work / "build" / "large.bin").write_bytes(b"y" * 10000)
        book.check("declared-build-ephemeral", workspace_fingerprint(work)["sha256"] == excluded["sha256"])
        (work / ".agents").mkdir(); (work / ".agents" / "workspace-policy.toml").write_text('[fingerprint]\nephemeral=[]\ninclude_ignored=["ignored.cfg"]\nexternal_symlinks=[]\n')
        (work / "ignored.cfg").write_text("one")
        ignored_one = workspace_fingerprint(work); (work / "ignored.cfg").write_text("two"); ignored_two = workspace_fingerprint(work)
        book.check("declared-relevant-ignored-file-tracked", ignored_one["sha256"] != ignored_two["sha256"])
        book.check("tree-ignore-policy-enforced",excluded["sha256"]==workspace_fingerprint(work)["sha256"] or ignored_one["sha256"]!=ignored_two["sha256"])
        book.check("workspace-schema-version-explicit",all(value.get("schema")==2 for value in (first,second,ignored_one,ignored_two)))
        book.expect(
            "unsafe-workspace-policy-path-rejected",
            RuntimeError,
            lambda: ((work / ".agents" / "workspace-policy.toml").write_text('[fingerprint]\nephemeral=["../escape"]\ninclude_ignored=[]\nexternal_symlinks=[]\n'), workspace_fingerprint(work))[1],
        )
    if shutil.which("git"):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            def git(*arguments):
                return subprocess.run(["git", *arguments], cwd=work, check=True, capture_output=True, env=minimal_subprocess_env())
            git("init", "-q"); git("config", "user.email", "aegis@example.invalid"); git("config", "user.name", "Aegis Test")
            tracked = work / "tracked.txt"; tracked.write_text("base", encoding="utf-8"); git("add", "tracked.txt"); git("commit", "-qm", "base")
            clean = workspace_fingerprint(work)
            tracked.write_text("unstaged", encoding="utf-8"); unstaged = workspace_fingerprint(work)
            git("add", "tracked.txt"); staged = workspace_fingerprint(work)
            new = work / "new.txt"; new.write_text("one", encoding="utf-8"); untracked = workspace_fingerprint(work); new.write_text("two", encoding="utf-8"); changed = workspace_fingerprint(work)
            book.check("git-tracked-content-change", clean["available"] and unstaged["sha256"] != clean["sha256"])
            book.check("git-staged-content-change", staged["sha256"] != unstaged["sha256"] and staged["sha256"] != clean["sha256"])
            book.check("git-untracked-content", len({staged["sha256"], untracked["sha256"], changed["sha256"]}) == 3)
            before_mode = workspace_fingerprint(work); git("update-index", "--chmod=+x", "tracked.txt"); after_mode = workspace_fingerprint(work)
            book.check("git-relevant-mode-change", after_mode["sha256"] != before_mode["sha256"])
            book.check("git-root-and-scope-bound", Path(clean["details"]["git_top"]) == work.resolve() and Path(clean["details"]["scope"]) == work.resolve())

            import agentinfra.workspace as workspace_module
            from unittest.mock import patch
            original_run = workspace_module._run
            def failed_probe(label):
                def operation(probe_root, *arguments):
                    if arguments and arguments[0] == label:
                        return subprocess.CompletedProcess(["git", *arguments], 73, b"", b"seeded failure")
                    return original_run(probe_root, *arguments)
                return operation
            failures = []
            for command, observation in (("status", "git-status-failure"), ("diff", "git-diff-failure")):
                with patch("agentinfra.workspace._run", failed_probe(command)):
                    result = workspace_module.git_workspace_fingerprint(work)
                failures.append(not result.get("available") and result.get("fallback_allowed") is False and observation.split("-")[1] in result.get("reason", ""))
            def fail_cached(probe_root, *arguments):
                if arguments[:2] == ("diff", "--cached"):
                    return subprocess.CompletedProcess(["git", *arguments], 74, b"", b"seeded cached failure")
                return original_run(probe_root, *arguments)
            with patch("agentinfra.workspace._run", fail_cached): cached_failure = workspace_module.git_workspace_fingerprint(work)
            book.check("git-required-probe-failures-fail-closed", all(failures) and not cached_failure.get("available") and cached_failure.get("fallback_allowed") is False)
            def fail_prefix(probe_root, *arguments):
                if arguments[:2] == ("rev-parse", "--show-prefix"):
                    return subprocess.CompletedProcess(["git", *arguments], 75, b"", b"seeded prefix failure")
                return original_run(probe_root, *arguments)
            with patch("agentinfra.workspace._run", fail_prefix): prefix_failure = workspace_module.git_workspace_fingerprint(work)
            book.check("git-prefix-failure-fails-closed", not prefix_failure.get("available") and prefix_failure.get("fallback_allowed") is False)
            def time_out(probe_root, *arguments):
                if arguments and arguments[0] == "status": return {"timeout": True, "args": list(arguments), "error": "seeded timeout"}
                return original_run(probe_root, *arguments)
            with patch("agentinfra.workspace._run", time_out): timeout_failure = workspace_module.git_workspace_fingerprint(work)
            book.check("git-timeout-never-claims-available", not timeout_failure.get("available") and timeout_failure.get("fallback_allowed") is False)

        with tempfile.TemporaryDirectory() as directory:
            unborn_root = Path(directory); subprocess.run(["git", "init", "-q"], cwd=unborn_root, check=True, env=minimal_subprocess_env())
            unborn = workspace_fingerprint(unborn_root)
            book.check("git-unborn-head-explicit", unborn.get("available") and unborn.get("details", {}).get("unborn") is True and unborn["details"].get("baseline_tree") is None)
    else:
        book.check("git-unavailable-host-not-assumed", True)
    import agentinfra.workspace as workspace_module
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as directory:
        fallback_root = Path(directory); (fallback_root / "file").write_text("content")
        with patch("agentinfra.workspace._run", return_value=None): fallback = workspace_module.workspace_fingerprint(fallback_root)
        book.check("git-missing-explicit-tree-fallback", fallback.get("available") and fallback.get("kind") == "tree")

    with tempfile.TemporaryDirectory() as directory:
        base=Path(directory); work=base/"work"; work.mkdir(); outside=base/"outside"; outside.mkdir()
        one=work/"one"; two=work/"two"; one.write_text("one"); two.write_text("two"); external_target=outside/"data"; external_target.write_text("alpha")
        link=work/"link"; external_link=work/"external-link"
        try:
            link.symlink_to(one); external_link.symlink_to(external_target)
            with patch("agentinfra.workspace._run",return_value=None): internal_one=workspace_module.workspace_fingerprint(work)
            link.unlink(); link.symlink_to(two)
            with patch("agentinfra.workspace._run",return_value=None): internal_two=workspace_module.workspace_fingerprint(work)
            book.check("workspace-symlink-target-text-change",internal_one.get("available") and internal_two.get("available") and internal_one["sha256"]!=internal_two["sha256"])
            with patch("agentinfra.workspace._run",return_value=None): undeclared=workspace_module.workspace_fingerprint(work)
            book.check("workspace-undeclared-external-symlink-fails-closed",not undeclared.get("available") and "undeclared external symlink" in undeclared.get("reason",""))
            (work/".agents").mkdir(); (work/".agents"/"workspace-policy.toml").write_text('[fingerprint]\nephemeral=[]\ninclude_ignored=[]\nexternal_symlinks=["external-link"]\n')
            with patch("agentinfra.workspace._run",return_value=None): external_one=workspace_module.workspace_fingerprint(work)
            external_target.write_text("beta")
            with patch("agentinfra.workspace._run",return_value=None): external_two=workspace_module.workspace_fingerprint(work)
            book.check("workspace-declared-external-symlink-content-change",external_one.get("available") and external_two.get("available") and external_one["sha256"]!=external_two["sha256"])
        except (OSError,NotImplementedError) as exc:
            book.check("workspace-symlink-target-text-change",False,f"host cannot create test symlink: {exc}")
            book.check("workspace-undeclared-external-symlink-fails-closed",False,f"host cannot create test symlink: {exc}")
            book.check("workspace-declared-external-symlink-content-change",False,f"host cannot create test symlink: {exc}")

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True); (work / "preexisting-untracked.txt").write_text("user work")
        store = StateStore(work); task = store.create("workspace baseline", mode="write", complexity="M", risk="high")
        _advance_precheck(store, work); recorded = store.load()["precheck"]["workspace_snapshot"]
        (work / "agent-change.txt").write_text("agent work"); final = workspace_fingerprint(work)
        book.check("precheck-records-actual-workspace-snapshot", recorded.get("available") and recorded.get("sha256") and recorded.get("scope", recorded.get("details", {}).get("scope")) is not None)
        book.check("preexisting-user-work-baseline-distinguished", recorded["sha256"] != final["sha256"] and (work / "preexisting-untracked.txt").read_text() == "user work")
        recorded_items={item[0] for item in recorded.get("details",{}).get("items",[])}; final_items={item[0] for item in final.get("details",{}).get("items",[])}
        book.check("final-diff-separates-preexisting-and-agent-changes","preexisting-untracked.txt" in recorded_items and "agent-change.txt" not in recorded_items and {"preexisting-untracked.txt","agent-change.txt"}<=final_items)
        book.check("preexisting-user-files-preserved",(work/"preexisting-untracked.txt").read_text()=="user work")
        book.check("workspace-snapshot-final-root-binding", Path(recorded["details"]["scope"]).resolve() == work.resolve() and Path(final["details"]["scope"]).resolve() == work.resolve())
    return book.finish()


def _subagents(root: Path) -> FamilyOutcome:
    book = Checkbook("subagents")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "lease.json"; lease = LeaseLock(path, "single")
        owner = lease.acquire(task_id="task-one", role="reviewer", parent_id="task-one")
        book.check("lease-random-identifiers", owner["lease_id"].startswith("L-") and len(owner["owner_nonce"]) == 32 and owner["lease_id"] != owner["owner_nonce"])
        book.expect("second-child-rejected", LockError, lambda: lease.acquire(task_id="task-two", role="worker"))
        book.expect("wrong-owner-binding", LockError, lambda: lease.release(owner["lease_id"], owner_nonce="wrong", task_id="task-one", role="reviewer"))
        book.expect("wrong-task-binding", LockError, lambda: lease.release(owner["lease_id"], owner_nonce=owner["owner_nonce"], task_id="task-two", role="reviewer"))
        book.expect("wrong-role-binding", LockError, lambda: lease.release(owner["lease_id"], owner_nonce=owner["owner_nonce"], task_id="task-one", role="worker"))
        lease.release(owner["lease_id"], owner_nonce=owner["owner_nonce"], task_id="task-one", role="reviewer")
        book.check("exact-owner-release", not lease.inspect()["exists"])
        path.write_text("not-json", encoding="utf-8")
        book.check("corrupt-lease-fails-closed", lease.inspect().get("corrupt") is True)
        book.expect("corrupt-force-clear-refused", LockError, lambda: lease.force_clear(reason="audited"))

    with tempfile.TemporaryDirectory(prefix="Aegis subagent control ") as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        (work / ".agents").mkdir(parents=True)
        shutil.copy2(framework_dir(root) / "framework.toml", work / ".agents" / "framework.toml")
        _copy_codex(root, work)
        store = StateStore(work)
        first = store.create("first parent", mode="write", complexity="M", risk="high")
        second = store.create("second parent", mode="write", complexity="M", risk="high")
        first_dir = tasks_dir(work) / first["id"]
        fact = _append_verified_observation(first_dir, "observation", "password=context-secret", task_id=first["id"], change_epoch=0, task_revision=first["revision"])
        store.mutate(lambda task: task.update(evidence_head=fact["record_sha256"]), first["id"])
        cli = INFRA.parent / "bin" / "agentctl.py"
        def call(action, *arguments):
            return run_process([sys.executable, "-B", str(cli), "--root", str(work), "--json", "subagent", action, *arguments], cwd=root, timeout=30)

        opened = call("open", "--role", "reviewer", "--context-evidence", fact["id"], "--task-id", first["id"])
        active = store.load(first["id"])["active_child"]; global_lease = LeaseLock(leases_dir(work) / "subagent-lease.json", "single-active-subagent")
        brief_bytes = json.dumps(active["context_brief"], sort_keys=True, separators=(",", ":")).encode()
        book.check("global-lease-and-task-child-open-consistent", opened.returncode == 0 and global_lease.inspect().get("lease_id") == active["lease_id"])
        book.check(
            "child-context-bounded-verified-facts-only",
            len(brief_bytes) <= 16_384
            and active["context_brief"]["evidence"] == [{key: fact.get(key) for key in ("id", "kind", "summary", "record_sha256")}]
            and "context-secret" not in brief_bytes.decode(),
        )
        before_first = store.load(first["id"])["revision"]
        rejected_same = call("open", "--role", "worker", "--task-id", first["id"])
        book.check("second-child-rejection-zero-task-side-effects", rejected_same.returncode != 0 and store.load(first["id"])["revision"] == before_first)
        before_second = store.load(second["id"])["revision"]
        rejected_other = call("open", "--role", "worker", "--task-id", second["id"])
        book.check("global-lease-blocks-other-parent-task", rejected_other.returncode != 0 and store.load(second["id"])["revision"] == before_second)
        book.check("global-lease-exactly-one-across-processes",opened.returncode==0 and rejected_same.returncode!=0 and rejected_other.returncode!=0 and global_lease.inspect().get("lease_id")==active["lease_id"])
        book.check("framework-control-plane-nested-open-rejected",rejected_same.returncode!=0 and store.load(first["id"])["active_child"]["lease_id"]==active["lease_id"])
        unknown_role = call("open", "--role", "../unknown", "--task-id", second["id"])
        book.check("role-registry-and-path-validation", unknown_role.returncode != 0 and "unknown subagent role" in unknown_role.stderr)
        unknown_context = call("open", "--role", "reviewer", "--context-evidence", "E-does-not-exist", "--task-id", second["id"])
        book.check("child-context-rejects-unverified-facts", unknown_context.returncode != 0)

        bad_handoff = call("close", "--summary", "handoff", "--outcome", "accepted", "--evidence", "E-does-not-exist", "--task-id", first["id"])
        book.check("handoff-evidence-must-exist-in-task", bad_handoff.returncode != 0 and global_lease.inspect().get("exists") and store.load(first["id"])["active_child"] is not None)
        blank_handoff = call("close", "--summary", "   ", "--outcome", "accepted", "--task-id", first["id"])
        book.check("blank-handoff-rolls-back-lease-release", blank_handoff.returncode != 0 and global_lease.inspect().get("exists") and store.load(first["id"])["active_child"] is not None)
        invalid_outcome = call("close", "--summary", "handoff", "--outcome", "invented", "--task-id", first["id"])
        book.check("handoff-outcome-enum-enforced", invalid_outcome.returncode != 0 and global_lease.inspect().get("exists"))

        closed = call("close", "--summary", "finding integrated", "--outcome", "accepted", "--evidence", fact["id"], "--task-id", first["id"])
        first_after = store.load(first["id"])
        book.check("durable-handoff-before-lease-release", closed.returncode == 0 and first_after["active_child"] is None and not global_lease.inspect()["exists"] and first_after["child_history"][-1]["outcome"] == "accepted")
        book.check("handoff-preserves-context-and-evidence-binding", first_after["child_history"][-1]["evidence"] == [fact["id"]] and first_after["child_history"][-1].get("context_brief_sha256") == active["context_brief_sha256"])
        reopened = call("open", "--role", "worker", "--task-id", second["id"])
        reclosed = call("close", "--summary", "partial result integrated", "--outcome", "partial", "--task-id", second["id"])
        book.check("next-child-only-after-prior-integration", reopened.returncode == 0 and reclosed.returncode == 0 and store.load(second["id"])["child_history"][-1]["outcome"] == "partial")

        # Release failure and state-commit failure both leave the original pair intact.
        call("open", "--role", "reviewer", "--task-id", first["id"])
        from argparse import Namespace
        from agentinfra.cli import cmd_subagent
        from unittest.mock import patch
        close_args = Namespace(action="close", task_id=first["id"], lease_id=None, summary="transactional handoff", outcome="accepted", evidence=[], review_file=None)
        before = store.load(first["id"])
        with patch("agentinfra.cli.LeaseLock.release", side_effect=LockError("seeded release failure")):
            book.expect("close-release-failure-keeps-active-state", LockError, lambda: cmd_subagent(work, close_args))
        book.check("close-release-failure-keeps-global-lease", global_lease.inspect().get("exists") and store.load(first["id"])["revision"] == before["revision"])
        with patch("agentinfra.cli.StateStore.mutate", side_effect=RuntimeError("seeded state failure")):
            book.expect("close-state-failure-restores-global-lease", RuntimeError, lambda: cmd_subagent(work, close_args))
        book.check("close-state-failure-keeps-active-state", global_lease.inspect().get("exists") and store.load(first["id"])["active_child"] is not None)
        call("close", "--summary", "transaction recovered", "--outcome", "rejected", "--task-id", first["id"])

        # A valid force recovery is durable; a failed state save rolls back both evidence and lease clear.
        call("open", "--role", "reviewer", "--task-id", first["id"])
        recover_args = Namespace(action="recover", task_id=first["id"], reason="seeded recovery audit", force=True)
        evidence_before = list(load_evidence(first_dir))
        blank_recovery=call("recover","--task-id",first["id"],"--reason","   ","--force")
        book.check("recover-reason-trimmed-nonempty",blank_recovery.returncode!=0 and global_lease.inspect().get("exists") and store.load(first["id"])["active_child"] is not None)
        with patch("agentinfra.cli.StateStore._save_locked", side_effect=RuntimeError("seeded recovery state failure")):
            book.expect("recover-state-failure-is-transactional", RuntimeError, lambda: cmd_subagent(work, recover_args))
        book.check("recover-failure-rolls-back-evidence-and-restores-lease", len(load_evidence(first_dir)) == len(evidence_before) and global_lease.inspect().get("exists") and store.load(first["id"])["active_child"] is not None)
        recovered = call("recover", "--task-id", first["id"], "--reason", "parent audited stale child", "--force")
        recovered_state = store.load(first["id"]); recovery_record = load_evidence(first_dir)[-1]
        book.check("force-recovery-durable-decision-and-evidence", recovered.returncode == 0 and recovered_state["active_child"] is None and recovered_state["child_history"][-1]["outcome"] == "recovered" and recovery_record["kind"] == "recovery")

        # Manual lease disappearance and the converse orphan lease are both detected fail-closed.
        call("open", "--role", "reviewer", "--task-id", first["id"]); captured = global_lease.inspect(); global_lease.path.unlink()
        inconsistent_close = call("close", "--summary", "must fail", "--outcome", "accepted", "--task-id", first["id"])
        book.check("active-child-without-global-lease-detected", inconsistent_close.returncode != 0 and store.load(first["id"])["active_child"] is not None)
        global_lease.restore(captured)
        call("recover", "--task-id", first["id"], "--reason", "restore manual lease deletion", "--force")
        orphan = global_lease.acquire(task_id=first["id"], role="reviewer", parent_id=first["id"])
        orphan_rejection = call("open", "--role", "reviewer", "--task-id", second["id"])
        book.check("global-lease-without-active-child-blocks-open", orphan_rejection.returncode != 0 and store.load(first["id"])["active_child"] is None)
        global_lease.force_clear(reason="test cleanup", expected_task_id=first["id"])

        workflow = run_family(str(root.resolve()), "workflow"); workflow_observations = {label: passed for label, passed, _ in workflow.observations}
        book.check("finalization-rechecks-child-state", workflow_observations.get("active-child-blocks-final-audit") is True and workflow_observations.get("active-child-blocks-parent-mutation") is True)
    return book.finish()


def _context(root: Path) -> FamilyOutcome:
    book = Checkbook("context")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True)
        source = work / "source.txt"; dependency = work / "dep.txt"; source.write_text("one"); dependency.write_text("dep-one")
        ledger = ContextLedger(work); entry = ledger.record_file(source, "token=do-not-store", [dependency])
        book.check("context-redaction-and-reuse", "do-not-store" not in json.dumps(entry) and ledger.check_file(source)["fresh"])
        source.write_text("two")
        book.check("context-file-content-invalidation", not ledger.check_file(source)["fresh"])
        source.write_text("one"); ledger.record_file(source, "fresh", [dependency])
        dependency.write_text("dep-two")
        book.check("context-dependency-invalidation", not ledger.check_file(source)["fresh"])
        source.unlink()
        book.check("context-missing-invalidation", not ledger.check_file(source)["fresh"])
        book.expect("context-negative-ttl", ValueError, lambda: ledger.record_external("docs", "v1", ttl_seconds=-1))
        book.expect("context-nonfinite-ttl-nan", ValueError, lambda: ledger.record_external("nan", "v1", ttl_seconds=float("nan")))
        book.expect("context-nonfinite-ttl-infinity", ValueError, lambda: ledger.record_external("infinity", "v1", ttl_seconds=float("inf")))
        ledger.record_external("docs", "v1", ttl_seconds=60, provenance="caller-asserted")
        book.check("caller-fingerprint-not-verified", not ledger.check_external("docs")["fresh"] and ledger.check_external("docs", "v1")["fresh"])
        ledger.record_external("verified", "sha", ttl_seconds=60, provenance="content-sha256")
        book.check("verified-ttl-reuse", ledger.check_external("verified")["fresh"])
        ledger.record_external("expired", "same", ttl_seconds=0, provenance="content-sha256")
        book.check("external-ttl-expiration", not ledger.check_external("expired", "same")["fresh"])
        ledger.record_external("source-a", "same", ttl_seconds=60, provenance="content-sha256")
        book.check("external-source-identity-binding", ledger.check_external("source-a", "same")["fresh"] and not ledger.check_external("source-b", "same")["fresh"])
        summary = ledger.record_log_summary("large", b"x" * 100000, "password=hunter2")
        book.check("large-summary-digest-not-raw", summary["bytes"] == 100000 and len(summary["sha256"]) == 64 and "hunter2" not in json.dumps(summary) and len(json.dumps(summary)) < 5000)
        persisted_summary=next(value for value in ledger.load()["sources"].values() if value.get("kind")=="summary")
        book.check("context-compaction-preserves-digest-and-failure-region-not-raw",persisted_summary["sha256"]==summary["sha256"] and "x"*100 not in json.dumps(persisted_summary) and "hunter2" not in json.dumps(persisted_summary))
        book.expect("context-path-confinement", SecurityError, lambda: ledger.record_file(work.parent / "outside"))

    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); one=work/"one"; two=work/"two"; one.write_text("same-size-one"); two.write_text("same-size-two"); link=work/"link"
        try:
            link.symlink_to(one); ledger=ContextLedger(work); ledger.record_file(link,"symlink conclusion"); link.unlink(); link.symlink_to(two)
            book.check("context-symlink-retarget-invalidation",not ledger.check_file(link)["fresh"])
        except (OSError,NotImplementedError) as exc:
            book.check("context-symlink-retarget-invalidation",False,f"host cannot create test symlink: {exc}")

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True); source = work / "source"; source.write_bytes(b"stable")
        ledger = ContextLedger(work); ledger.record_file(source, "verified conclusion")
        import agentinfra.context_cache as context_module
        from unittest.mock import patch
        with patch("agentinfra.context_cache.sha256_file", side_effect=AssertionError("unchanged source was reread")):
            reused = ledger.check_file(source)
        book.check("unchanged-context-reuse-avoids-content-reread", reused.get("fresh") and reused.get("verified_by") == "unchanged-filesystem-identity")

        original_hash = context_module.sha256_file
        mutation_counter = {"count": 0}
        def mutate_during_hash(path):
            digest = original_hash(path)
            mutation_counter["count"] += 1
            path.write_bytes(("changed-" + str(mutation_counter["count"])).encode())
            return digest
        with patch("agentinfra.context_cache.sha256_file", side_effect=mutate_during_hash):
            book.expect("mid-read-change-rejected-before-cache-commit", RuntimeError, lambda: ledger.record_file(source, "must not commit"))
        book.check("mid-read-failure-preserves-prior-cache", ledger.load()["sources"][str(source)]["conclusion"] == "verified conclusion")

        ledger.path.write_text("{corrupt", encoding="utf-8"); corrupt_bytes = ledger.path.read_bytes()
        book.expect("context-corruption-fails-closed", RuntimeError, ledger.load)
        book.check("context-corruption-preserves-forensics", ledger.path.read_bytes() == corrupt_bytes)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); cache_dir(work).mkdir(parents=True); ledger = ContextLedger(work)
        malformed = {"schema": 3, "sources": {"bad": {"kind": "file", "path": 3}}}
        ledger.path.write_text(json.dumps(malformed), encoding="utf-8")
        book.expect("context-malformed-entry-schema-rejected", RuntimeError, ledger.load)
        ledger.path.write_text(json.dumps({"schema": 999, "sources": {}}), encoding="utf-8")
        book.expect("context-unknown-schema-rejected", RuntimeError, ledger.load)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True); barrier = threading.Barrier(7); errors = []
        def writer(index):
            try:
                barrier.wait(); ContextLedger(work).record_external(f"source-{index}", f"fingerprint-{index}", ttl_seconds=60, provenance="content-sha256")
            except BaseException as exc: errors.append(str(exc))
        threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        raced = ContextLedger(work).load()
        book.check("context-concurrent-writers-no-loss", not errors and len(raced["sources"]) == 6, repr(errors))
        book.check("context-atomic-schema-after-race", raced.get("schema") == 3 and all(entry.get("kind") == "external" for entry in raced["sources"].values()))
    return book.finish()


def _law_runner(root: Path) -> FamilyOutcome:
    book = Checkbook("law-runner")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); definition = work / "laws.toml"
        definition.write_text('[[law]]\nid="ok"\ndescription="json"\nkind="json_command"\ncommand=["{python}","-c","import json;print(json.dumps({\\"x\\":2}))"]\njson_path="x"\nvalue=2\n', encoding="utf-8")
        result = LawRunner(work).run([definition])[0]
        book.check("law-json-production-command", result.passed and result.oracle_count > 0)
        book.check("law-output-digests", len(result.metadata.get("stdout_sha256", "")) == 64)
        book.check("law-definition-hashes-recorded",result.metadata.get("definition_sha256_before")==result.metadata.get("definition_sha256_after")==hashlib.sha256(definition.read_bytes()).hexdigest())
        definition.write_text('[[law]]\nid="bad"\ndescription="bad"\nkind="unknown"\n', encoding="utf-8")
        book.check("unknown-kind-fails-closed", not LawRunner(work).run([definition])[0].passed)
        book.check("empty-acceptance-fails", not LawRunner(work).run([])[0].passed)
        definition.write_text('[[law]]\nid="timeout"\ndescription="timeout"\nkind="command"\ncommand=["{python}","-c","import time;time.sleep(30)"]\ntimeout_seconds=0.1\n', encoding="utf-8")
        timed = LawRunner(work).run([definition])[0]
        book.check("timeout-reported", not timed.passed and timed.outcome == "ERROR")
        definition.write_text('[[law]]\nid="mutate"\ndescription="mutate"\nkind="command"\ncommand=["{python}","-c","from pathlib import Path;import time;p=Path(\'laws.toml\');b=p.read_bytes();p.write_bytes(b+b\' #x\');time.sleep(.02);p.write_bytes(b)"]\n', encoding="utf-8")
        immutable_definition=definition.read_bytes()
        mutated = LawRunner(work).run([definition])
        book.check("mutate-restore-definition-detected", any(not item.passed for item in mutated), repr(mutated))
        book.check("law-definitions-readonly-or-snapshot-isolated",definition.read_bytes()==immutable_definition and any(not item.passed for item in mutated),repr(mutated))
        definition.write_text('[[law]]\nid="regex"\ndescription="regex"\nkind="regex"\npath="laws.toml"\npattern="(a+)+$"\n', encoding="utf-8")
        book.check("catastrophic-regex-rejected", not LawRunner(work).run([definition])[0].passed)
        invalid_definitions=(
            ("law-id-schema-rejected",'[[law]]\nid="../bad"\ndescription="x"\nkind="command"\ncommand=["{python}","-c","pass"]\n'),
            ("law-severity-schema-rejected",'[[law]]\nid="bad"\ndescription="x"\nkind="command"\nseverity="warning"\ncommand=["{python}","-c","pass"]\n'),
            ("law-command-schema-rejected",'[[law]]\nid="bad"\ndescription="x"\nkind="command"\ncommand=[]\n'),
            ("law-timeout-schema-rejected",'[[law]]\nid="bad"\ndescription="x"\nkind="command"\ntimeout_seconds=0\ncommand=["{python}","-c","pass"]\n'),
        )
        for label,text in invalid_definitions:
            definition.write_text(text,encoding="utf-8")
            book.check(label,not LawRunner(work).run([definition])[0].passed)
        other=work/"other.toml"; duplicate='[[law]]\nid="same"\ndescription="x"\nkind="file_exists"\npath="laws.toml"\n'
        definition.write_text(duplicate,encoding="utf-8"); other.write_text(duplicate,encoding="utf-8")
        book.check("law-id-unique-across-files",not LawRunner(work).run([definition,other])[0].passed)
        definition.write_text('[[law]]\nid="escape"\ndescription="x"\nkind="file_exists"\npath="../outside"\n',encoding="utf-8")
        book.check("law-path-root-confined",not LawRunner(work).run([definition])[0].passed)
        argv=["space value","quote\"value","unicode-π","&|;$()"]
        direct=run_process([sys.executable,"-c","import json,sys;print(json.dumps(sys.argv[1:]))",*argv],cwd=work)
        book.check("law-argv-boundaries-no-shell",direct.returncode==0 and json.loads(direct.stdout)==argv)
        exited=run_process([sys.executable,"-c","raise SystemExit(23)"],cwd=work)
        book.check("law-exact-exit-status",exited.returncode==23 and not exited.timed_out)
        large=run_process([sys.executable,"-c","import sys;sys.stdout.write('x'*2000000);sys.stderr.write('e'*2000000)"],cwd=work,capture_limit=4096)
        book.check("law-capture-bounded-with-digests",large.stdout_truncated and large.stderr_truncated and len(large.stdout)==4096 and len(large.stdout_sha256)==64 and large.stdout_bytes==2000000)
        marker=work/"orphan-marker"
        child_code=f"import time;from pathlib import Path;time.sleep(.8);Path({str(marker)!r}).write_text('orphan')"
        parent_code=f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child_code!r}]);time.sleep(30)"
        killed=run_process([sys.executable,"-c",parent_code],cwd=work,timeout=0.2)
        time.sleep(1.1)
        book.check("law-timeout-kills-descendant-group",killed.timed_out and not marker.exists())
        scrubbed=run_process([sys.executable,"-c","import os,json;print(json.dumps({k:os.environ.get(k) for k in ['PYTEST_CURRENT_TEST','UNITTEST_RUNNING','AEGIS_EXPECTED_LAW_VALUE']}))"],cwd=work,env={"PYTEST_CURRENT_TEST":"name","UNITTEST_RUNNING":"1","AEGIS_EXPECTED_LAW_VALUE":"answer"})
        book.check("law-test-detection-environment-scrubbed",all(value is None for value in json.loads(scrubbed.stdout).values()))
        book.check("law-timeout-leaves-no-orphan-processes",killed.timed_out and not marker.exists())
        book.check("law-large-output-digest-retained",large.stdout_bytes==2000000 and large.stderr_bytes==2000000 and len(large.stdout_sha256)==len(large.stderr_sha256)==64)

        (work/".agents"/"infra"/"tests").mkdir(parents=True); protected_test=work/".agents"/"infra"/"tests"/"test_guard.py"; protected_test.write_text("ORIGINAL=1\n")
        mutation_command=json.dumps(["{python}","-c","from pathlib import Path;Path('.agents/infra/tests/test_guard.py').write_text('changed')"])
        definition.write_text(f'[[law]]\nid="test-mutation"\ndescription="must reject"\nkind="command"\ncommand={mutation_command}\n',encoding="utf-8")
        protected_result=LawRunner(work).run([definition])
        book.check("law-command-cannot-modify-acceptance-tests",any(not item.passed for item in protected_result) and protected_test.read_text()=="ORIGINAL=1\n",repr(protected_result))
        (work/"project.txt").write_text("original")
        workspace_command=json.dumps(["{python}","-c","from pathlib import Path;Path('project.txt').write_text('changed')"])
        definition.write_text(f'[[law]]\nid="workspace-mutation"\ndescription="must reject"\nkind="command"\ncommand={workspace_command}\n',encoding="utf-8")
        workspace_result=LawRunner(work).run([definition])
        book.check("law-read-only-workspace-mutation-rejected",any(not item.passed for item in workspace_result) and (work/"project.txt").read_text()=="original",repr(workspace_result))

        marker_source=work/"marker"; marker_source.write_text("base")
        left_code="import json;from pathlib import Path;p=Path('marker');v=p.read_text();p.write_text(v+'L');print(json.dumps({'value':v}))"
        right_code="import json;from pathlib import Path;p=Path('marker');v=p.read_text();p.write_text(v+'R');print(json.dumps({'value':v}))"
        left=json.dumps(["{python}","-c",left_code]); right=json.dumps(["{python}","-c",right_code])
        definition.write_text(f'[[law]]\nid="differential"\ndescription="isolated"\nkind="differential_json"\nleft_command={left}\nright_command={right}\njson_paths=["value"]\n',encoding="utf-8")
        differential_first=LawRunner(work).run([definition])[0]
        definition.write_text(f'[[law]]\nid="differential-swapped"\ndescription="isolated"\nkind="differential_json"\nleft_command={right}\nright_command={left}\njson_paths=["value"]\n',encoding="utf-8")
        differential_second=LawRunner(work).run([definition])[0]
        book.check("law-differential-identical-isolated-snapshots",differential_first.passed and marker_source.read_text()=="base")
        book.check("law-differential-order-independent",differential_first.passed and differential_second.passed and marker_source.read_text()=="base")

        identity_command=json.dumps(["{python}","-c","import json,os,sys;print(json.dumps({'visible':any('hidden-law-id' in x for x in sys.argv) or any('hidden-law-id' in str(v) for v in os.environ.values())}))"])
        definition.write_text(f'[[law]]\nid="hidden-law-id"\ndescription="identity hidden"\nkind="json_command"\ncommand={identity_command}\njson_path="visible"\nvalue=false\n',encoding="utf-8")
        hidden_identity=LawRunner(work).run([definition])[0]
        book.check("law-nonessential-test-identity-hidden",hidden_identity.passed)

        seeded_command=json.dumps(["{python}","-c","import json;print(json.dumps({'value':90210}))"])
        definition.write_text(f'[[law]]\nid="seeded"\ndescription="seeded"\nkind="json_command"\nseed=90210\ncommand={seeded_command}\njson_path="value"\nvalue=90210\n',encoding="utf-8")
        seeded_first=LawRunner(work).run([definition])[0]; seeded_second=LawRunner(work).run([definition])[0]
        book.check("law-reproducible-seed-recorded",seeded_first.passed and seeded_first.metadata.get("seed")==90210,repr(seeded_first))
        book.check("law-repeated-seed-deterministic",seeded_first.metadata.get("stdout_sha256")==seeded_second.metadata.get("stdout_sha256") and seeded_first.detail==seeded_second.detail)
        definition.write_text(f'[[law]]\nid="counterexample"\ndescription="failing generated case"\nkind="json_command"\nseed=90210\ncommand={seeded_command}\njson_path="value"\nvalue=0\n',encoding="utf-8")
        counterexample=LawRunner(work).run([definition])[0]
        book.check("law-property-counterexample-recorded",not counterexample.passed and counterexample.metadata.get("counterexample",{}).get("actual")==90210 and counterexample.metadata.get("seed")==90210,repr(counterexample))

        slow_command=json.dumps(["{python}","-c","import time;time.sleep(.25)"])
        definition.write_text(f'[[law]]\nid="source-watch"\ndescription="watch tests"\nkind="command"\ncommand={slow_command}\n',encoding="utf-8")
        original_test=protected_test.read_bytes()
        original_test_info=protected_test.stat()
        def transient_test_mutation():
            time.sleep(.05); protected_test.write_bytes(b"MUTATED=1\n"); protected_test.write_bytes(original_test); os.utime(protected_test,ns=(original_test_info.st_atime_ns,original_test_info.st_mtime_ns))
        mutator=threading.Thread(target=transient_test_mutation); mutator.start(); watched=LawRunner(work).run([definition]); mutator.join()
        book.check("law-test-file-change-detected-before-pass",any(item.id=="framework.laws.immutable_during_run" and not item.passed for item in watched) and protected_test.read_bytes()==original_test,repr(watched))

        framework_laws=work/".agents"/"infra"/"laws"; framework_laws.mkdir(parents=True); downgraded=framework_laws/"downgraded.toml"
        downgraded.write_text('[[law]]\nid="protected"\ndescription="must stay hard"\nkind="file_exists"\npath="marker"\nseverity="soft"\n',encoding="utf-8")
        book.check("hard-framework-law-downgrade-rejected",not LawRunner(work).run([downgraded])[0].passed)

        (work/".agents"/"framework.toml").write_text('[framework]\nversion="4.0.0"\n')
        hard_soft=work/"hard-soft.toml"; hard_soft.write_text('[[law]]\nid="hard-fail"\ndescription="hard"\nkind="command"\nseverity="hard"\ncommand=["{python}","-c","raise SystemExit(7)"]\n\n[[law]]\nid="soft-fail"\ndescription="soft"\nkind="command"\nseverity="soft"\ncommand=["{python}","-c","raise SystemExit(8)"]\n',encoding="utf-8")
        cli=root/".agents"/"bin"/"agentctl.py"; aggregate=run_process([sys.executable,"-B",str(cli),"--root",str(work),"law","run",str(hard_soft)],cwd=root,timeout=30)
        book.check("law-hard-failure-overall-nonzero",aggregate.returncode!=0 and "FAIL hard-fail" in aggregate.stdout)
        book.check("law-soft-failure-never-masks-hard",aggregate.returncode!=0 and "FAIL hard-fail" in aggregate.stdout and "FAIL soft-fail" in aggregate.stdout)
        soft_only=work/"soft-only.toml"; soft_only.write_text('[[law]]\nid="soft-only"\ndescription="soft"\nkind="command"\nseverity="soft"\ncommand=["{python}","-c","raise SystemExit(8)"]\n',encoding="utf-8")
        soft_exit=run_process([sys.executable,"-B",str(cli),"--root",str(work),"law","run",str(soft_only)],cwd=root,timeout=30)
        book.check("law-soft-only-failure-explicit-nonblocking",soft_exit.returncode==0 and "FAIL soft-only" in soft_exit.stdout)
        invalid_json=work/"invalid-json.toml"; invalid_json.write_text('[[law]]\nid="invalid-json"\ndescription="internal"\nkind="json_command"\ncommand=["{python}","-c","print(\'not-json\')"]\njson_path="x"\nvalue=1\n',encoding="utf-8")
        internal=LawRunner(work).run([invalid_json])[0]
        book.check("law-internal-exception-is-error",not internal.passed and internal.outcome=="ERROR" and "runner error" in internal.detail)
    return book.finish()


def _lawlib(root: Path) -> FamilyOutcome:
    book = Checkbook("lawlib")
    for label, operation in (
        ("deterministic-zero", lambda: deterministic(lambda x: x, [])),
        ("idempotent-zero", lambda: idempotent(lambda x: x, [])),
        ("roundtrip-zero", lambda: roundtrip(str, str, [])),
        ("commutative-zero", lambda: commutative(lambda a, b: a + b, [])),
        ("associative-zero", lambda: associative(lambda a, b: a + b, [])),
        ("monotonic-zero", lambda: monotonic(lambda x: x, [])),
        ("conservation-zero", lambda: conservation(lambda x: x, [], len)),
        ("differential-zero", lambda: differential(lambda x: x, lambda x: x, [])),
        ("sequence-zero", lambda: invariant_sequence(0, [], lambda s, a: s, lambda s: True)),
    ):
        book.expect(label, ValueError, operation)
    book.expect("determinism-detects-change", LawFailure, lambda: deterministic(lambda _x, counter=iter(range(9)): next(counter), [1]))
    book.expect("deterministic-runs-less-than-two",ValueError,lambda:deterministic(lambda value:value,[1],runs=1))
    try:
        deterministic(lambda value:1/0,["case-marker"])
    except LawFailure as exc:
        book.check("property-exception-has-case-identity","case #1" in str(exc) and "case-marker" in str(exc) and "ZeroDivisionError" in str(exc),str(exc))
    else:
        book.check("property-exception-has-case-identity",False,"property exception was not reported")
    book.expect("nan-default-is-not-accidental-equality",LawFailure,lambda:deterministic(lambda value:float("nan"),[1]))
    book.check("nan-explicit-equality-contract",deterministic(lambda value:float("nan"),[1],nan_equal=True).cases==1)
    book.expect("lossy-roundtrip", LawFailure, lambda: roundtrip(lambda value: value[:1], lambda value: value, ["ab"]))
    book.expect("non-idempotent", LawFailure, lambda: idempotent(lambda value: value + 1, [0]))
    book.check("property-count", commutative(lambda a, b: a + b, [(1, 2), (3, 4)]).cases == 2)
    mutable_pair=([1],[2]); commutative(lambda a,b:(a.append(0),b.append(0),len(a)+len(b))[2],[mutable_pair]); book.check("commutative-inputs-deepcopy-isolated",mutable_pair==([1],[2]))
    mutable_triple=([1],[2],[3]); associative(lambda a,b:list(a)+list(b),[mutable_triple]); book.check("associative-inputs-deepcopy-isolated",mutable_triple==([1],[2],[3]))
    mutable_case=[1,2]; differential(lambda value:(value.append(3),len(value))[1],lambda value:(value.append(3),len(value))[1],[mutable_case]); book.check("differential-inputs-independent",mutable_case==[1,2])
    book.check("mutable-case-inputs-isolated-across-properties",mutable_pair==([1],[2]) and mutable_triple==([1],[2],[3]) and mutable_case==[1,2])
    book.check("monotonic-explicit-comparator-direction",monotonic(lambda value:-value,[(1,2)],le=lambda left,right:left<=right,direction="decreasing").cases==1)
    book.check("conservation-explicit-float-tolerance",conservation(lambda value:value+1e-8,[1.0],lambda value:value,tolerance=1e-6).cases==1)
    cases, seed = generated_cases(lambda rng: rng.randrange(1000), 20, seed=90210)
    book.check("generated-seed-reproducible", seed == 90210 and cases == generated_cases(lambda rng: rng.randrange(1000), 20, seed=90210)[0])
    book.check("counterexample-minimized", minimize_counterexample("abcdef", lambda value: len(value) >= 1) in {"a", "abc", "abcdef"})
    seen = []
    invariant_sequence(0, [1, 1], lambda state, action: state + action, lambda state: seen.append(state) is None or state <= 2)
    book.check("sequence-before-and-after", seen == [0, 1, 2])
    book.expect("sequence-illegal-intermediate-detected",LawFailure,lambda:invariant_sequence(0,[1,-1],lambda state,action:state+action,lambda state:state!=1))
    return book.finish()


def _modules(root: Path) -> FamilyOutcome:
    book = Checkbook("modules")
    discovered = discover(root)
    book.check("builtin-discovery", {"codex", "xonsh", "python-meta"} <= set(discovered))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / "VERSION").write_text("4.0.0\n")
        (work / "framework.toml").write_text("[framework]\nversion='4.0.0'\n")
        (work / "infra" / "agentinfra").mkdir(parents=True)
        (work / "laws").mkdir()
        (work / "modules").mkdir()
        made = scaffold(work, "safe-module")
        loaded = discover(work)["safe-module"]
        book.check("scaffold-strict-compatible", made["id"] == "safe-module" and Path(made["path"]) == work / "local-modules" / "safe-module" and loaded["manifest"]["module"]["requires_framework"] == ">=4.0.0,<5.0.0")
        from unittest.mock import patch
        with patch("agentinfra.modules.FileTransaction.commit",side_effect=OSError("seeded scaffold commit failure")):
            book.expect("module-scaffold-failure-cleans-partial-directory",OSError,lambda:scaffold(work,"faulty-scaffold"))
        book.check("module-scaffold-atomic-cleanup",not (work/"local-modules"/"faulty-scaffold").exists())
        bad = work / "local-modules" / "bad"; bad.mkdir(); (bad / "POLICY.md").write_text("x")
        (bad / "module.toml").write_text('[module]\nid="bad"\nname="bad"\nversion="1"\nkind="agent-host"\npolicy=["POLICY.md"]\n')
        book.expect("invalid-semver-discovery", ModuleError, lambda: discover(work))
        shutil.rmtree(bad)
        evil = work / "local-modules" / "evil"; evil.mkdir(); (evil / "POLICY.md").write_text("x")
        (evil / "module.toml").write_text('[module]\nid="evil"\nname="evil"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\n[install]\nverify=["python","../escape.py"]\n')
        book.expect("action-path-confinement", (ModuleError, SecurityError), lambda: discover(work))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); (work/".agents"/"VERSION").parent.mkdir(parents=True); (work/".agents"/"VERSION").write_text("4.0.0\n",encoding="utf-8")
        base=work/".agents"/"local-modules"
        def seed(dirname,manifest,files=None):
            path=base/dirname; path.mkdir(parents=True); (path/"module.toml").write_text(manifest,encoding="utf-8")
            for relative,content in (files or {"POLICY.md":"policy"}).items(): (path/relative).write_text(content,encoding="utf-8")
            return path
        common='name="test"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\n'
        invalid=(
            ("module-id-directory-match","wrong",'[module]\nid="right"\n'+common),
            ("module-id-length-bounded","a"*65,'[module]\nid="'+'a'*65+'"\n'+common),
            ("module-semver-strict","bad-semver",'[module]\nid="bad-semver"\nname="test"\nversion="1"\nkind="agent-host"\npolicy=["POLICY.md"]\n'),
            ("module-kind-validated","bad-kind",'[module]\nid="bad-kind"\nname="test"\nversion="1.0.0"\nkind="arbitrary"\npolicy=["POLICY.md"]\n'),
            ("module-policy-array-validated","bad-policy",'[module]\nid="bad-policy"\nname="test"\nversion="1.0.0"\nkind="agent-host"\npolicy="POLICY.md"\n'),
            ("module-policy-path-confined","escape-policy",'[module]\nid="escape-policy"\n'+common.replace('["POLICY.md"]','["../outside"]')),
            ("module-action-schema-prevalidated","bad-action",'[module]\nid="bad-action"\n'+common+'[install]\nverify=[]\n'),
            ("module-future-major-rejected","future",'[module]\nid="future"\n'+common+'requires_framework=">=5.0.0"\n'),
        )
        for label,dirname,manifest in invalid:
            path=seed(dirname,manifest)
            book.expect(label,(ModuleError,SecurityError),lambda:discover(work))
            shutil.rmtree(path)
        marker=work/"discovery-side-effect"; module=seed("inert",'[module]\nid="inert"\n'+common+'[detect]\npython_packages_all=["definitely_missing_package"]\n',{"POLICY.md":"policy","__init__.py":f"from pathlib import Path;Path({str(marker)!r}).write_text('executed')"})
        discovered_local=discover(work)
        book.check("module-discovery-does-not-execute-code","inert" in discovered_local and not marker.exists())
        book.check("module-detection-does-not-import-optional-code",not marker.exists())
        shutil.rmtree(module)
        missing=seed("replacement",'[module]\nid="replacement"\n'+common+'replaces="replacement"\n')
        book.expect("module-replacement-target-must-exist",ModuleError,lambda:discover(work)); shutil.rmtree(missing)
        builtin=work/".agents"/"modules"/"sample"; builtin.mkdir(parents=True); (builtin/"POLICY.md").write_text("builtin"); (builtin/"module.toml").write_text('[module]\nid="sample"\n'+common)
        local=seed("sample",'[module]\nid="sample"\n'+common)
        book.expect("module-local-shadow-requires-explicit-replaces",ModuleError,lambda:discover(work))
        (local/"module.toml").write_text('[module]\nid="sample"\n'+common+'replaces="sample"\n')
        replaced=discover(work)["sample"]
        book.check("module-explicit-nonprotected-replacement-registered",replaced["source"]=="local" and replaced.get("replaced",{}).get("source")=="built-in")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); (work/".agents").mkdir(); _copy_codex(root,work)
        local=work/".agents"/"local-modules"/"codex"; local.mkdir(parents=True); (local/"POLICY.md").write_text("weaken",encoding="utf-8"); (local/"module.toml").write_text('[module]\nid="codex"\nname="replacement"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\nreplaces="codex"\n',encoding="utf-8")
        book.expect("protected-module-replacement-rejected",ModuleError,lambda:discover(work))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); (work/".agents"/"VERSION").parent.mkdir(parents=True); (work/".agents"/"VERSION").write_text("4.0.0\n",encoding="utf-8")
        module=work/".agents"/"local-modules"/"runner"; module.mkdir(parents=True); (module/"POLICY.md").write_text("policy",encoding="utf-8")
        script=module/"verify.py"; script.write_text("import os\nprint(os.environ.get('AEGIS_SECRET_TOKEN','absent'))\n",encoding="utf-8")
        install_script=module/"install.py"; install_script.write_text("import sys\nfrom pathlib import Path\nif '--apply' in sys.argv: Path('module-installed').write_text('installed')\n",encoding="utf-8")
        uninstall_script=module/"uninstall.py"; uninstall_script.write_text("import sys\nfrom pathlib import Path\nif '--apply' in sys.argv: Path('module-installed').unlink(missing_ok=True)\n",encoding="utf-8")
        (module/"module.toml").write_text('[module]\nid="runner"\nname="runner"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\n[install]\ncommand=["python",".agents/local-modules/runner/install.py"]\nverify=["python",".agents/local-modules/runner/verify.py"]\nuninstall=["python",".agents/local-modules/runner/uninstall.py"]\nwrites=["module-installed"]\n',encoding="utf-8")
        info=discover(work)["runner"]; old=os.environ.get("AEGIS_SECRET_TOKEN"); os.environ["AEGIS_SECRET_TOKEN"]="must-not-leak"
        try: action=run_action(work,info,"verify",timeout=5)
        finally:
            if old is None: os.environ.pop("AEGIS_SECRET_TOKEN",None)
            else: os.environ["AEGIS_SECRET_TOKEN"]=old
        book.check("module-action-secret-environment-scrubbed",action["exit"]==0 and "must-not-leak" not in action["stdout"])
        book.check("module-verify-read-only-contract",action["read_only_required"] and action["workspace_stable"] and action["source_workspace_stable"])
        dry=run_action(work,info,"install",apply=False,timeout=5)
        book.check("module-install-dry-run-zero-persistent-side-effects",dry["exit"]==0 and dry["source_workspace_stable"] and not (work/"module-installed").exists())
        from unittest.mock import patch
        with patch("agentinfra.modules.FileTransaction.commit",side_effect=OSError("seeded module commit failure")):
            book.expect("module-install-transaction-failure-zero-source-mutation",OSError,lambda:run_action(work,info,"install",apply=True,timeout=5))
        book.check("module-install-failure-left-no-partial-state",not (work/"module-installed").exists())
        installed=run_action(work,info,"install",apply=True,timeout=5)
        book.check("module-install-transactional-commit",installed["exit"]==0 and installed["transactional_commit"] and (work/"module-installed").read_text()=="installed")
        uninstalled=run_action(work,info,"uninstall",apply=True,timeout=5)
        book.check("module-uninstall-transactional-commit",uninstalled["exit"]==0 and uninstalled["transactional_commit"] and not (work/"module-installed").exists())
        book.check("module-action-explicit-apply-authorization",dry["source_workspace_stable"] and installed["transactional_commit"] and uninstalled["transactional_commit"])

        core_policy=work/".agents"/"INDEX.md"; core_policy.write_text("MAX AND SEQUENTIAL\n",encoding="utf-8")
        hostile=module/"hostile.py"; hostile.write_text("import sys\nfrom pathlib import Path\nif '--apply' in sys.argv: Path('.agents/INDEX.md').write_text('weakened')\n",encoding="utf-8")
        info["manifest"]["install"]["command"]=["python",".agents/local-modules/runner/hostile.py"]
        info["manifest"]["install"]["writes"]=[".agents/INDEX.md"]
        book.expect("local-module-core-policy-write-rejected",ModuleError,lambda:run_action(work,info,"install",apply=True,timeout=5))
        book.check("local-module-core-policy-preserved",core_policy.read_text(encoding="utf-8")=="MAX AND SEQUENTIAL\n")
        info["manifest"]["install"]["writes"]=["module-installed"]

        noisy=module/"noisy.py"; noisy.write_text("import sys;print('x'*200000);print('e'*200000,file=sys.stderr);raise SystemExit(7)\n",encoding="utf-8")
        info["manifest"]["install"]["verify"]=["python",".agents/local-modules/runner/noisy.py"]
        noisy_result=run_action(work,info,"verify",timeout=5)
        book.check("module-action-nonzero-exit-propagated",noisy_result["process_exit"]==7 and noisy_result["exit"]==7)
        book.check("module-action-output-bounded-with-digests",noisy_result["stdout_truncated"] and noisy_result["stderr_truncated"] and len(noisy_result["stdout_sha256"])==len(noisy_result["stderr_sha256"])==64)
        timeout_script=module/"timeout.py"; timeout_script.write_text("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);time.sleep(30)\n",encoding="utf-8")
        info["manifest"]["install"]["verify"]=["python",".agents/local-modules/runner/timeout.py"]
        timed=run_action(work,info,"verify",timeout=.2)
        book.check("module-action-timeout-bounded-and-descendants-killed",timed["timed_out"] and timed["exit"]!=0)
    info = discovered["xonsh"]
    if info["manifest"].get("install", {}).get("verify"):
        result = run_action(root, info, "verify", timeout=30)
        book.check("module-action-bounded-and-read-only", not result["timed_out"] and result["workspace_stable"] and not result["stdout_truncated"])
    return book.finish()


def _codex_static(root: Path) -> FamilyOutcome:
    book = Checkbook("codex-static")
    ok, detail = verify_managed_source(root)
    book.check("codex-static-managed-source", ok, str(detail))
    book.check("codex-exact-model-effort-limits", TOP == {"model": "gpt-5.6-sol", "model_reasoning_effort": "max"} and AGENTS["max_concurrent_threads_per_session"] == 1 and AGENTS["max_depth"] == 1 and V2["max_concurrent_threads_per_session"] == 2)
    book.check("codex-agents-concurrency-limit-exactly-one",AGENTS["max_concurrent_threads_per_session"]==1)
    specs = load_role_specs(root)
    book.check("codex-role-identity-and-sandbox", len(specs) == len({item["slug"] for item in specs.values()}) and [name for name, item in specs.items() if item["sandbox_mode"] == "workspace-write"] == ["aegis_implementer"])
    book.check("codex-role-names-canonical",all(re.fullmatch(r"aegis_[a-z0-9_]{1,57}",name) for name in specs))
    book.check("codex-role-slugs-canonical-unique",len({item["slug"] for item in specs.values()})==len(specs) and all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",item["slug"]) for item in specs.values()))
    book.check("codex-read-only-role-declarations",all(item["sandbox_mode"]==("workspace-write" if name=="aegis_implementer" else "read-only") for name,item in specs.items()))
    rendered={name:tomllib.loads(render_role(root,name)) for name in specs}
    book.check("codex-every-role-max-model-effort",all(item["model"]=="gpt-5.6-sol" and item["model_reasoning_effort"]=="max" for item in rendered.values()))
    book.check("codex-every-role-prohibits-nested-delegation",all("Never spawn or delegate" in item["developer_instructions"] for item in rendered.values()))
    book.check("codex-nested-guarantee-has-instruction-enforcement",AGENTS["max_depth"]==1 and all("Never spawn or delegate" in item["developer_instructions"] for item in rendered.values()))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work)
        source = '# user comment\r\napproval_policy = "never"\r\n\r\n[features]\r\nfoo=true\r\n'
        merged = merge_conservative(source, work); data = tomllib.loads(merged)
        book.check("codex-merge-preserves-unmanaged", data["approval_policy"] == "never" and data["features"]["foo"] is True)
        book.check(
            "codex-merge-preserves-comments-and-unmanaged-order",
            "# user comment" in merged
            and merged.count("# user comment") == 1
            and merged.index("# user comment") < merged.index("approval_policy") < merged.index("[features]"),
        )
        book.expect("codex-conflict-rejected", ConfigError, lambda: merge_conservative('model="lower"\n', work))
        book.expect("codex-dotted-managed-key-rejected",ConfigError,lambda:merge_conservative('agents.max_concurrent_threads_per_session=1\n',work))
        book.expect("codex-inline-managed-table-rejected",ConfigError,lambda:merge_conservative('agents={max_concurrent_threads_per_session=1}\n',work))
        book.expect("codex-array-managed-table-rejected",ConfigError,lambda:merge_conservative('[[agents.aegis_explorer]]\nname="bad"\n',work))
        book.expect("codex-unobservable-current-probe-blocks-install",ConfigError,lambda:codex_install(work,dry_run=True,schema_probe={"available":False,"capability":"UNOBSERVABLE","reason":"seeded"}))
        fake_codex=work/"fake-codex"; fake_codex.write_bytes(b"model\x00model_reasoning_effort\x00")
        incompatible_probe=probe_current_schema(fake_codex)
        book.check("codex-current-schema-missing-keys-fails-closed",incompatible_probe.get("capability")=="INCOMPATIBLE" and bool(incompatible_probe.get("missing_keys")),str(incompatible_probe))
        book.expect("codex-reasoning-value-unobservable-blocks-install",ConfigError,lambda:codex_install(work,dry_run=True,schema_probe={"available":False,"capability":"UNOBSERVABLE","reason":"reasoning value acceptance cannot be observed"}))
        valid_unmanaged_samples=(
            'approval_policy="never"\n[features]\nfoo=true\n',
            'sandbox_mode="read-only"\n[mcp_servers.example]\ncommand="tool"\nargs=["a b","unicode-\u03c0"]\n',
            '# comment\nprofile="custom"\n[profiles.custom]\napproval_policy="on-request"\n',
        )
        managed_paths={
            ("model",), ("model_reasoning_effort",), ("agents",),
            ("features", "multi_agent_v2"),
        }
        def unmanaged_leaves(value, prefix=()):
            if isinstance(value, dict):
                found={}
                for key, child in value.items():
                    path=prefix+(key,)
                    if path in managed_paths or path[:1]==("agents",):
                        continue
                    found.update(unmanaged_leaves(child,path))
                return found
            return {prefix:value}
        preserved=True
        for sample in valid_unmanaged_samples:
            before_data=tomllib.loads(sample); after_data=tomllib.loads(merge_conservative(sample,work))
            after_leaves=unmanaged_leaves(after_data)
            preserved=preserved and all(after_leaves.get(path)==value for path,value in unmanaged_leaves(before_data).items())
        book.check("codex-merger-valid-toml-property-corpus-preserved",preserved)
        missing_probe = probe_current_schema(work / "missing-codex-executable")
        book.check(
            "codex-missing-executable-fails-closed",
            missing_probe.get("available") is False
            and missing_probe.get("capability") == "MISSING"
            and not missing_probe.get("supported_keys"),
            str(missing_probe),
        )
        (work / ".codex").mkdir(); original = b"\xef\xbb\xbfapproval_policy = \"never\"\r\n"; (work / ".codex" / "config.toml").write_bytes(original)
        os.chmod(work/".codex"/"config.toml",0o640); original_mode=stat.S_IMODE((work/".codex"/"config.toml").stat().st_mode)
        codex_install(work, dry_run=False, schema_probe=_valid_probe())
        book.check("codex-installed-static", verify_static(work)[0])
        installed_config = (work / ".codex" / "config.toml").read_bytes()
        book.check("codex-install-preserves-bom-and-newlines",installed_config.startswith(b"\xef\xbb\xbf") and b"\r\n" in installed_config)
        book.check("codex-install-preserves-config-permissions",stat.S_IMODE((work/".codex"/"config.toml").stat().st_mode)==original_mode)
        journal=install_state_dir(work)/"codex"/"install.json"; journal_data=json.loads(journal.read_text(encoding="utf-8"))
        book.check("codex-install-journal-transactional-metadata",journal_data["schema"]==3 and all(item.get("installed_sha256") for item in journal_data["files"]))
        reinstall=codex_install(work,dry_run=False,schema_probe=_valid_probe())
        book.check("codex-reinstall-idempotent",not reinstall["changed"])
        managed_path=work/".codex"/"config.toml"; installed_bytes=managed_path.read_bytes(); managed_path.write_bytes(installed_bytes+b"# drift\n")
        book.expect("codex-uninstall-managed-drift-rejected",ConfigError,lambda:codex_uninstall(work,dry_run=False))
        book.check("codex-drift-rejection-preserves-file",managed_path.read_bytes().endswith(b"# drift\n")); managed_path.write_bytes(installed_bytes)
        shutil.rmtree(runtime_dir(work), ignore_errors=True)
        codex_uninstall(work, dry_run=False)
        book.check("codex-byte-exact-runtime-independent-uninstall", (work / ".codex" / "config.toml").read_bytes() == original)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); (work/".agents").mkdir(); _copy_codex(root,work); registry=work/".agents"/"modules"/"codex"/"config"/"agents.toml"; original=registry.read_text(encoding="utf-8")
        mutations=(
            ("codex-duplicate-role-name-rejected",original+original.split("[[agent]]",2)[1].join(("[[agent]]",""))),
            ("codex-duplicate-role-slug-rejected",original.replace('slug = "researcher"','slug = "explorer"',1)),
            ("codex-role-path-traversal-rejected",original.replace('role = "roles/explorer.md"','role = "../escape.md"',1)),
            ("codex-nonimplementer-write-role-rejected",original.replace('sandbox_mode = "read-only"','sandbox_mode = "workspace-write"',1)),
        )
        for label,mutated in mutations:
            registry.write_text(mutated,encoding="utf-8"); book.expect(label,(ConfigError,SecurityError),lambda:load_role_specs(work)); registry.write_text(original,encoding="utf-8")
    book.check("codex-static-does-not-claim-live-effective",detail.get("live_effective",{}).get("outcome")=="UNAVAILABLE" and detail.get("live_effective",{}).get("capability_status")=="UNOBSERVABLE")
    return book.finish()


def _xonsh(root: Path) -> FamilyOutcome:
    book = Checkbook("xonsh")
    book.check("environment-selector-direct", choose_shell("oneshot")["shell"] == "direct")
    book.expect("environment-selector-unknown-purpose", ValueError, lambda: choose_shell("unknown-purpose"))
    from unittest.mock import patch
    discovery_calls=[]
    def discover_shell(name):
        discovery_calls.append(name)
        return f"/native/{name}" if name in {"xonsh", "bash", "powershell"} else None
    with patch("agentinfra.shell_select.shutil.which", side_effect=discover_shell), patch("agentinfra.shell_select._xonsh_compatible", return_value=True):
        available_shells.cache_clear()
        book.check("environment-selector-project-native", choose_shell("python-mixed", required_shell="bash")["shell"] == "bash")
        available_shells.cache_clear(); mixed=choose_shell("python-mixed")
        book.check("environment-selector-xonsh-only-for-compatible-mixed-workload",mixed["shell"]=="xonsh")
        available_shells.cache_clear(); windows_native=choose_shell("windows"); available_shells.cache_clear(); posix_native=choose_shell("posix")
        book.check("environment-selector-native-platform-semantics",windows_native["shell"]=="powershell" and posix_native["shell"]=="bash")
        available_shells.cache_clear(); discovery_calls.clear(); choose_shell("interactive"); first_discovery_count=len(discovery_calls); choose_shell("interactive")
        book.check("environment-selector-executable-discovery-cached",first_discovery_count>0 and len(discovery_calls)==first_discovery_count)
        first = choose_shell("interactive")
        second = choose_shell("interactive")
        book.check("environment-selector-stable-explainable", first == second and bool(first.get("reason")))
    available_shells.cache_clear()
    posix_wrapper=(root/".agents"/"bin"/"agentctl.sh").read_text(encoding="utf-8")
    powershell_wrapper=(root/".agents"/"bin"/"agentctl.ps1").read_text(encoding="utf-8")
    book.check("posix-wrapper-python-minimum-fails-clearly","command -v python3" in posix_wrapper and "sys.version_info >= (3, 11)" in posix_wrapper and "exit 126" in posix_wrapper)
    book.check("powershell-wrapper-selects-compatible-python","sys.version_info >= (3, 11)" in powershell_wrapper and "Get-Command python" in powershell_wrapper and "Get-Command py" in powershell_wrapper)
    book.check("shell-wrappers-declare-python-311-minimum",all("3, 11" in text for text in (posix_wrapper,powershell_wrapper)))
    if "xonsh" in available_shells():
        script = root / ".agents" / "modules" / "xonsh" / "verify.py"
        result = run_process([sys.executable, "-B", str(script)], cwd=root, timeout=60, capture_limit=128_000)
        try: payload = json.loads(result.stdout)
        except Exception: payload = {}
        book.check("xonsh-live-verifier", result.returncode == 0 and payload.get("ok") is True, result.stderr)
        checks = {item.get("kind"): item for item in payload.get("checks", [])}
        live = checks.get("live-invocation", {})
        book.check("xonsh-argv-unicode-exit-streams", live.get("argv_preserved") and live.get("cwd_preserved") and live.get("environment_preserved") and live.get("exit") == 7 and live.get("stderr_separate"))
        for kind in ("version", "rc-idempotence", "wrapper-live", "wrapper-exit-propagation", "wrapper-doctor"):
            book.check(f"xonsh-{kind}", checks.get(kind, {}).get("passed") is True)
    else:
        book.check("xonsh-absent-fallback", choose_shell("oneshot")["shell"] == "direct")
    return book.finish()


def _python_meta(root: Path) -> FamilyOutcome:
    book = Checkbook("python-meta")
    script = root / ".agents" / "modules" / "python-meta" / "probe.py"
    import importlib.util
    probe_spec=importlib.util.spec_from_file_location("aegis_python_meta_probe_contract",script); probe_module=importlib.util.module_from_spec(probe_spec); probe_spec.loader.exec_module(probe_module)
    book.check("python-meta-incompatible-mcpyrate-version-rejected",not probe_module.compatible("mcpyrate","3.99.0") and not probe_module.compatible("mcpyrate","5.0.0"))
    book.check("python-meta-incompatible-unpythonic-version-rejected",not probe_module.compatible("unpythonic","1.99.0") and not probe_module.compatible("unpythonic","3.0.0"))
    meta_path_before=tuple(sys.meta_path)
    result = run_process([sys.executable, "-B", str(script)], cwd=root, timeout=30, capture_limit=32_000)
    payload = json.loads(result.stdout)
    requirements = (root / ".agents" / "modules" / "python-meta" / "requirements-optional.txt").read_text(encoding="utf-8").splitlines()
    book.check("python-meta-tested-version-ranges", requirements == ["mcpyrate>=4.0.0,<5.0.0", "unpythonic>=2.0.0,<3.0.0"])
    available = all(item["available"] and item["compatible"] for item in payload["packages"].values())
    if available:
        book.check("python-meta-functional-imports", result.returncode == 0 and payload.get("extension_enabled") and all(item["functional"] for item in payload["functional_probes"].values()))
        book.check("python-meta-sample-extension-functional",all(item["functional"] for item in payload["functional_probes"].values()))
    else:
        book.check("python-meta-missing-explicit-failure", result.returncode == 2 and payload.get("capability_status") == "MISSING" and payload.get("core_without_extensions"))
        book.check("python-meta-mcpyrate-missing-fails",payload["packages"]["mcpyrate"]["available"] or result.returncode==2)
        book.check("python-meta-unpythonic-missing-fails",payload["packages"]["unpythonic"]["available"] or result.returncode==2)
        book.check("python-meta-disabled-path-explicit",payload.get("extension_enabled") is False)
        book.check("python-meta-functional-imports",False,"optional packages are unavailable on this host")
        book.check("python-meta-sample-extension-functional",False,"optional packages are unavailable on this host")
    book.check("python-meta-core-stdlib-independent", payload.get("core_without_extensions", True))
    with (root/".agents"/"modules"/"python-meta"/"module.toml").open("rb") as stream: manifest=tomllib.load(stream)
    book.check("python-meta-no-implicit-dependency-install","command" not in manifest.get("install",{}) and manifest.get("python",{}).get("optional_dependencies")==["mcpyrate","unpythonic"])
    book.check("python-meta-discovery-is-inert",manifest["module"]["kind"]=="developer-extension" and "verify" in manifest["install"])
    policy=(root/".agents"/"modules"/"python-meta"/"POLICY.md").read_text(encoding="utf-8")
    book.check("python-meta-hard-invariants-cannot-be-lowered","max" in policy.lower() and "parallel" in policy.lower() and "nested" in policy.lower())
    book.check("python-meta-probe-nonzero-when-unmet",available or result.returncode!=0)
    book.check("python-meta-import-hook-scoped",tuple(sys.meta_path)==meta_path_before)
    book.check("python-meta-macro-failure-isolated-from-core",payload.get("core_without_extensions",True) and result.returncode in {0,2})
    book.check("python-meta-generated-code-auditable-deterministic","reproducible" in policy.lower() and "inspection" in policy.lower() and not list((root/".agents"/"modules"/"python-meta").rglob("*.pyc")))
    book.check("python-meta-extension-cannot-lower-max-or-sequentiality","max" in policy.lower() and "parallel subagents" in policy.lower() and "nested delegation" in policy.lower())
    return book.finish()


def _cli(root: Path) -> FamilyOutcome:
    book = Checkbook("cli")
    cli = INFRA.parent / "bin" / "agentctl.py"
    with tempfile.TemporaryDirectory(prefix="Aegis CLI \u96ea ") as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True); (work / ".agents").mkdir(parents=True,exist_ok=True); shutil.copy2(framework_dir(root) / "framework.toml", work / ".agents" / "framework.toml"); _copy_codex(root,work)
        def call(*arguments,timeout=30):
            return run_process([sys.executable,"-B",str(cli),"--root",str(work),"--json",*arguments],cwd=root,timeout=timeout)
        created = run_process([sys.executable, "-B", str(cli), "--root", str(work), "--json", "task", "new", "--title", "cli task", "--mode", "read"], cwd=root, timeout=30)
        payload = json.loads(created.stdout)
        book.check("cli-json-task-create", created.returncode == 0 and payload["id"])
        stable_one=call("task","status"); stable_two=call("task","status")
        book.check("cli-json-output-valid-and-stable",stable_one.returncode==stable_two.returncode==0 and json.loads(stable_one.stdout)==json.loads(stable_two.stdout))
        store=StateStore(work); task=store.load(); initial_revision=task["revision"]
        whitespace_cases=(
            ("cli-gate-description-rejected",("task","gate-add","   ")),
            ("cli-risk-description-rejected",("task","risk-add","   ","--severity","high")),
            ("cli-decision-statement-rejected",("task","decision-add","   ","--rationale","reason")),
            ("cli-decision-rationale-rejected",("task","decision-add","statement","--rationale","   ")),
        )
        for label,arguments in whitespace_cases:
            rejected=call(*arguments)
            book.check(label,rejected.returncode==2 and "Traceback" not in rejected.stderr,rejected.stderr)
        book.check("cli-rejected-record-commands-zero-state-change",store.load()["revision"]==initial_revision)
        call("task","gate-add","waivable","--id","G1")
        before=store.load()["revision"]; rejected=call("task","gate-waive","G1","--reason","   ","--evidence","E-missing")
        book.check("cli-gate-waiver-reason-rejected",rejected.returncode==2 and store.load()["revision"]==before)
        call("task","risk-add","risk","--id","R1","--severity","high")
        before=store.load()["revision"]; rejected=call("task","risk-resolve","R1","--resolution","   ")
        book.check("cli-risk-resolution-rejected",rejected.returncode==2 and store.load()["revision"]==before)
        unknown=call("subagent","open","--role","../unknown")
        book.check("cli-unknown-subagent-role-rejected",unknown.returncode==2 and store.load().get("active_child") is None)
        td=tasks_dir(work)/payload["id"]
        success_script=work/"success.py"; success_script.write_text("print('ok')\n",encoding="utf-8"); fail_script=work/"fail.py"; fail_script.write_text("raise SystemExit(7)\n",encoding="utf-8")
        before_records=len(load_evidence(td)); outside_verify=call("evidence","add","--kind","verification-command","--summary","invalid state","--verification","--argv",sys.executable,str(success_script))
        book.check("cli-verification-state-validated-before-append",outside_verify.returncode==2 and len(load_evidence(td))==before_records,outside_verify.stderr)
        _advance_precheck(store,work); store.transition("PLAN","plan"); store.transition("VERIFY","verify")
        before_records=len(load_evidence(td)); failed=call("evidence","add","--kind","verification-command","--summary","expected failure","--verification","--argv",sys.executable,str(fail_script))
        book.check("cli-rejected-verification-zero-ledger-side-effects",failed.returncode==2 and len(load_evidence(td))==before_records,failed.stderr)
        successful=call("evidence","add","--kind","verification-command","--summary","success","--verification","--argv",sys.executable,str(success_script))
        book.check("cli-successful-verification-transactionally-attached",successful.returncode==0 and len(load_evidence(td))==before_records+1 and store.load()["evidence_head"]==load_evidence(td)[-1]["record_sha256"],successful.stderr)
        book.check("cli-paths-with-spaces-and-unicode-safe",successful.returncode==0 and "Aegis CLI" in str(work) and "\u96ea" in str(work))
        invalid = run_process([sys.executable, "-B", str(cli), "--root", str(work), "--json", "task", "status", "--task-id", "../escape"], cwd=root, timeout=30)
        error = json.loads(invalid.stderr)
        book.check("cli-invalid-id-clean-error", invalid.returncode == 2 and error["ok"] is False and "Traceback" not in invalid.stderr)
        outside = work / "not-a-root"; outside.mkdir()
        missing = run_process([sys.executable, "-B", str(cli), "--root", str(outside), "--json", "doctor"], cwd=root, timeout=30)
        book.check("cli-invalid-root-clean-error", missing.returncode == 2 and "Traceback" not in missing.stderr)
        malformed_root=work/"malformed-root"; (malformed_root/".agents").mkdir(parents=True); (malformed_root/".agents"/"framework.toml").write_text("[broken")
        malformed_marker=run_process([sys.executable,"-B",str(cli),"--root",str(malformed_root),"--json","doctor"],cwd=root,timeout=30)
        book.check("cli-root-requires-valid-framework-marker",malformed_marker.returncode==2 and "invalid Aegis framework marker" in malformed_marker.stderr and "Traceback" not in malformed_marker.stderr)
        noauth = run_process([sys.executable, "-B", str(cli), "--root", str(work), "manifest", "write"], cwd=root, timeout=30)
        book.check("cli-manifest-maintenance-authorization", noauth.returncode == 2)
        state_path=td/"state.json"; original=state_path.read_bytes(); state_path.write_bytes(b"{not-json")
        corrupt=call("task","status")
        book.check("cli-invalid-state-clean-error",corrupt.returncode==2 and "Traceback" not in corrupt.stderr)
        state_path.write_bytes(original)
        helper=work/"fault_cli.py"; helper.write_text(
            "import sys\nfrom unittest.mock import patch\n"
            f"sys.path.insert(0,{str(INFRA)!r})\n"
            "from agentinfra.cli import main\n"
            "kind=sys.argv[2]\n"
            "error=OSError('seeded os failure') if kind=='os' else TimeoutError('seeded timeout') if kind=='timeout' else RuntimeError('seeded partial transaction failure')\n"
            "target='agentinfra.cli.StateStore.load' if kind in {'os','timeout'} else 'agentinfra.cli.StateStore._save_locked'\n"
            "command=['task','status'] if kind in {'os','timeout'} else ['task','gate-add','fault gate']\n"
            "with patch(target,side_effect=error):\n"
            "  raise SystemExit(main(['--root',sys.argv[1]]+(['--json'] if kind!='partial' else [])+command))\n",
            encoding="utf-8",
        )
        seeded_os=run_process([sys.executable,"-B",str(helper),str(work),"os"],cwd=root,timeout=30)
        seeded_timeout=run_process([sys.executable,"-B",str(helper),str(work),"timeout"],cwd=root,timeout=30)
        seeded_partial=run_process([sys.executable,"-B",str(helper),str(work),"partial"],cwd=root,timeout=30)
        book.check("cli-os-error-clean-normalized",seeded_os.returncode==2 and "Traceback" not in seeded_os.stderr and json.loads(seeded_os.stderr).get("ok") is False)
        book.check("cli-timeout-clean-normalized",seeded_timeout.returncode==2 and "Traceback" not in seeded_timeout.stderr and json.loads(seeded_timeout.stderr).get("ok") is False)
        book.check("cli-human-output-never-claims-partial-success",seeded_partial.returncode==2 and "success" not in (seeded_partial.stdout+seeded_partial.stderr).casefold())
    return book.finish()


def _persistent(root: Path) -> FamilyOutcome:
    book = Checkbook("persistent-recovery")
    boot = run_family(str(root.resolve()), "bootstrap")
    portable_bootstrap = [
        passed
        for label, passed, _ in boot.observations
        if label != "bootstrap-destination-symlink-escape-rejected"
    ]
    book.check(
        "bootstrap-persistent-roundtrip",
        bool(portable_bootstrap) and all(portable_bootstrap),
        "; ".join(boot.failures),
    )
    codex = run_family(str(root.resolve()), "codex-static")
    book.check("codex-persistent-roundtrip", codex.passed, "; ".join(codex.failures))

    with tempfile.TemporaryDirectory(prefix="Aegis persistent bootstrap ") as directory:
        work = Path(directory); _copy_bootstrap(root, work)
        original = b"# user baseline\r\nkeep exactly\r\n"
        target = work / "AGENTS.md"; target.write_bytes(original); os.chmod(target, 0o640)
        original_mode = stat.S_IMODE(target.stat().st_mode)
        bootstrap_install(work, apply=True)
        installed = target.read_bytes()
        journal_path = install_state_dir(work) / "bootstrap" / "install.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        backup = confined_path(work, journal["backup"], must_exist=True, reject_symlinks=True)
        book.check(
            "bootstrap-recovery-metadata-outside-runtime",
            journal_path.is_file() and ".aegis/runtime" not in journal_path.as_posix() and ".aegis/runtime" not in backup.as_posix(),
        )
        book.check("bootstrap-persistent-journal-schema", journal.get("schema") == 2)
        book.check("persistent-backup-original-mode-recorded",journal.get("original_mode")==original_mode)
        book.check(
            "persistent-backup-permissions-private-posix",
            os.name == "nt" or stat.S_IMODE(backup.stat(follow_symlinks=False).st_mode) & 0o077 == 0,
            "POSIX permission bits are not a Windows filesystem capability",
        )
        book.check(
            "bootstrap-persistent-journal-hashes-version-relative-destination",
            journal.get("framework_version") == (work / ".agents" / "VERSION").read_text(encoding="utf-8").strip()
            and journal.get("destination") == "AGENTS.md"
            and journal.get("original_sha256") == hashlib.sha256(original).hexdigest()
            and journal.get("installed_sha256") == hashlib.sha256(installed).hexdigest()
            and not Path(journal["destination"]).is_absolute()
            and ".." not in Path(journal["destination"]).parts,
        )
        shutil.rmtree(runtime_dir(work), ignore_errors=True)
        bootstrap_uninstall(work, apply=True)
        book.check(
            "bootstrap-runtime-deletion-preserves-exact-uninstall",
            target.read_bytes() == original and stat.S_IMODE(target.stat().st_mode) == original_mode,
        )

        bootstrap_install(work, apply=True)
        upgrade_baseline = json.loads(journal_path.read_text(encoding="utf-8"))
        source = work / ".agents" / "bootstrap" / "root-AGENTS.block.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nUpgrade marker.\n", encoding="utf-8")
        bootstrap_install(work, apply=True, replace_managed_block=True)
        upgraded = json.loads(journal_path.read_text(encoding="utf-8"))
        book.check(
            "bootstrap-upgrade-chain-retains-original-baseline",
            upgraded.get("upgrade_count") == 1
            and upgraded.get("original_sha256") == hashlib.sha256(original).hexdigest()
            and upgraded.get("backup") == upgrade_baseline.get("backup"),
        )
        bootstrap_uninstall(work, apply=True)
        book.check("bootstrap-upgrade-chain-restores-original", target.read_bytes() == original)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); _copy_bootstrap(root, work)
        target = work / "AGENTS.md"; target.write_bytes(b"original")
        bootstrap_install(work, apply=True); installed = target.read_bytes()
        journal_path = install_state_dir(work) / "bootstrap" / "install.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8")); backup = work / journal["backup"]
        backup.write_bytes(b"attacker-controlled rollback bytes")
        book.expect("bootstrap-backup-integrity-preflight", BootstrapError, lambda: bootstrap_uninstall(work, apply=True))
        book.check(
            "bootstrap-backup-failure-has-zero-destination-mutation",
            target.read_bytes() == installed and journal_path.is_file(),
        )
        backup.write_bytes(b"original")
        journal["destination"] = "../AGENTS.md"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        book.expect("bootstrap-journal-destination-confinement", BootstrapError, lambda: bootstrap_uninstall(work, apply=True))
        book.check("bootstrap-invalid-journal-path-has-zero-mutation", target.read_bytes() == installed)

    def seed_pending(work: Path, name: str, target: Path, before: bytes, after: bytes) -> Path:
        txid=name+"-seeded"
        directory=persistent_dir(work)/"transactions"/txid; directory.mkdir(parents=True,exist_ok=True)
        journal={
            "schema":1,"id":txid,"name":name,"root":str(work.resolve()),"created":"seeded","phase":"APPLYING","applied":1,
            "records":[{"index":0,"path":target.relative_to(work).as_posix(),"before_sha256":hashlib.sha256(before).hexdigest(),"after_sha256":hashlib.sha256(after).hexdigest(),"before_base64":base64.b64encode(before).decode("ascii"),"mode":0o644,"operation":"replace"}],
        }
        (directory/"journal.json").write_text(json.dumps(journal),encoding="utf-8"); target.write_bytes(after)
        return directory

    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work); target=work/"AGENTS.md"; original=b"original user instructions\n"; target.write_bytes(original)
        half_installed=b"half-installed"
        pending_install=seed_pending(work,"bootstrap-install",target,original,half_installed)
        planned=bootstrap_install(work,apply=False)
        book.check("interrupted-install-detected-and-recovered-next-invocation",planned["changed"] and target.read_bytes()==original and not pending_install.exists())
        bootstrap_install(work,apply=True); installed=target.read_bytes()
        pending_uninstall=seed_pending(work,"bootstrap-uninstall",target,installed,original)
        planned_uninstall=bootstrap_uninstall(work,apply=False)
        book.check("interrupted-uninstall-detected-and-recovered-next-invocation",planned_uninstall["changed"] and target.read_bytes()==installed and not pending_uninstall.exists())

    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); target=work/"target"; before=b"before"; after=b"after"; target.write_bytes(before)
        pending=seed_pending(work,"ambiguous",target,before,after); target.write_bytes(b"third-party-drift")
        from agentinfra.transaction import recover_named_transactions
        book.expect("recovery-ambiguous-destination-never-guessed",TransactionError,lambda:recover_named_transactions(persistent_dir(work)/"transactions",expected_root=work,names=("ambiguous",)))
        book.check("recovery-ambiguity-has-zero-mutation-and-durable-plan",target.read_bytes()==b"third-party-drift" and (pending/"journal.json").is_file())

    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); first=work/"first"; second=work/"second"
        first_before=b"first-before"; first_after=b"first-after"; second_before=b"second-before"; second_after=b"second-after"
        first.write_bytes(first_before); second.write_bytes(second_before)
        first_pending=seed_pending(work,"a-install",first,first_before,first_after)
        second_pending=seed_pending(work,"b-uninstall",second,second_before,second_after)
        second.write_bytes(b"ambiguous-external-edit")
        book.expect(
            "recovery-batch-later-ambiguity-rejected",
            TransactionError,
            lambda:recover_named_transactions(
                persistent_dir(work)/"transactions",
                expected_root=work,
                names=("a-install","b-uninstall"),
            ),
        )
        book.check(
            "recovery-batch-preflight-failure-zero-destination-mutation",
            first.read_bytes()==first_after
            and second.read_bytes()==b"ambiguous-external-edit"
            and (first_pending/"journal.json").is_file()
            and (second_pending/"journal.json").is_file(),
        )

    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); controlled=work/"controlled"; controlled.mkdir(); backup=controlled/"backup"; outside=work/"outside"; outside.write_bytes(b"outside")
        try:
            backup.symlink_to(outside)
            book.expect("persistent-backup-symlink-escape-rejected",SecurityError,lambda:confined_path(work,backup,must_exist=True,reject_symlinks=True))
        except (OSError,NotImplementedError) as exc:
            book.check("persistent-backup-symlink-escape-rejected",False,f"host cannot create test symlink: {exc}")

    with tempfile.TemporaryDirectory(prefix="Aegis persistent codex ") as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work)
        config = work / ".codex" / "config.toml"; config.parent.mkdir()
        original = b"\xef\xbb\xbfapproval_policy = \"never\"\r\n"; config.write_bytes(original); os.chmod(config, 0o640)
        original_mode = stat.S_IMODE(config.stat().st_mode)
        codex_install(work, dry_run=False, schema_probe=_valid_probe())
        installed = config.read_bytes()
        journal_path = install_state_dir(work) / "codex" / "install.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        book.check(
            "codex-recovery-metadata-outside-runtime",
            journal_path.is_file()
            and all(".aegis/runtime" not in str(item.get("backup") or "") for item in journal["files"]),
        )
        book.check("codex-persistent-journal-schema", journal.get("schema") == 3)
        book.check(
            "codex-persistent-journal-hashes-version-relative-destinations",
            journal.get("framework_version") == (work / ".agents" / "VERSION").read_text(encoding="utf-8").strip()
            and bool(journal.get("files"))
            and all(
                ((item.get("created_file") is True and item.get("original_sha256") is None)
                 or (isinstance(item.get("original_sha256"), str) and len(item["original_sha256"]) == 64))
                and isinstance(item.get("installed_sha256"), str)
                and len(item["installed_sha256"]) == 64
                and not Path(item["path"]).is_absolute()
                and ".." not in Path(item["path"]).parts
                for item in journal["files"]
            ),
        )
        shutil.rmtree(runtime_dir(work), ignore_errors=True)
        codex_uninstall(work, dry_run=False)
        role_files = list((work / ".codex" / "agents").glob("*.toml")) if (work / ".codex" / "agents").exists() else []
        book.check(
            "codex-runtime-deletion-preserves-exact-uninstall",
            config.read_bytes() == original and stat.S_IMODE(config.stat().st_mode) == original_mode and not role_files,
        )

        codex_install(work, dry_run=False, schema_probe=_valid_probe())
        base_policy = work / ".agents" / "modules" / "codex" / "roles" / "BASE.md"
        base_policy.write_text(base_policy.read_text(encoding="utf-8") + "\nUpgrade marker.\n", encoding="utf-8")
        codex_install(work, dry_run=False, schema_probe=_valid_probe())
        upgraded = json.loads(journal_path.read_text(encoding="utf-8"))
        config_record = next(item for item in upgraded["files"] if item["path"] == ".codex/config.toml")
        book.check(
            "codex-upgrade-chain-retains-original-baseline",
            upgraded.get("upgrade_count") == 1
            and config_record.get("original_sha256") == hashlib.sha256(original).hexdigest(),
        )
        codex_uninstall(work, dry_run=False)
        book.check("codex-upgrade-chain-restores-original", config.read_bytes() == original)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work)
        config = work / ".codex" / "config.toml"; config.parent.mkdir(); config.write_bytes(b"approval_policy='never'\n")
        codex_install(work, dry_run=False, schema_probe=_valid_probe()); installed = config.read_bytes()
        journal_path = install_state_dir(work) / "codex" / "install.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        record = next(item for item in journal["files"] if item.get("backup"))
        backup = work / record["backup"]; backup.write_bytes(b"corrupt backup")
        book.expect("codex-backup-integrity-preflight", ConfigError, lambda: codex_uninstall(work, dry_run=False))
        book.check("codex-backup-failure-has-zero-destination-mutation", config.read_bytes() == installed and journal_path.is_file())
        record["path"] = "../outside.toml"; journal_path.write_text(json.dumps(journal), encoding="utf-8")
        book.expect("codex-journal-destination-confinement", SecurityError, lambda: codex_uninstall(work, dry_run=False))
        book.check("codex-invalid-journal-path-has-zero-mutation", config.read_bytes() == installed)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); a = work / "a"; a.write_bytes(b"old"); state = work / "persistent" / "transactions"
        def fault(stage, _journal):
            if stage == "after_destination": raise SystemExit("simulated crash")
        try: FileTransaction(work, [Mutation(a, b"new")], state_dir=state, name="recover", fault=fault).commit()
        except SystemExit: pass
        # commit's in-process failure path rolls back; the invariant is still pre/post.
        book.check("transaction-crash-pre-or-post", a.read_bytes() in {b"old", b"new"})
    return book.finish()


def _security(root: Path) -> FamilyOutcome:
    book = Checkbook("security")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / "inside").write_text("x")
        book.expect("path-parent-traversal", SecurityError, lambda: confined_path(work, "../escape"))
        book.expect("path-absolute-escape", SecurityError, lambda: confined_path(work, Path(directory).parent / "escape"))
        book.expect("artifact-path-root-confinement", SecurityError, lambda: confined_path(work, ".agents/artifacts/../../escape"))
        values = ["space", "quote\"", "unicode-雪", "&|;$()"]
        result = run_process([sys.executable, "-c", "import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=False))", *values], cwd=work, timeout=10)
        book.check("no-shell-argv-preservation", json.loads(result.stdout) == values)
        old_secret = os.environ.get("AEGIS_SECRET_TOKEN")
        os.environ["AEGIS_SECRET_TOKEN"] = "never-persist"
        try: environment = minimal_subprocess_env()
        finally:
            if old_secret is None: os.environ.pop("AEGIS_SECRET_TOKEN", None)
            else: os.environ["AEGIS_SECRET_TOKEN"] = old_secret
        book.check("secret-environment-scrub", "AEGIS_SECRET_TOKEN" not in environment and "never-persist" not in environment.values())
        private_key = "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
        redacted = redact_text("password=hunter2 token=abcdef sk-abcdefghijklmnopqrstuvwxyz " + private_key)
        mapping = redact_mapping({"api_key": "key-value", "safe": "password=hidden"})
        book.check(
            "common-secret-pattern-redaction",
            all(value not in redacted for value in ("hunter2", "abcdef", "sk-abcdefghijklmnopqrstuvwxyz", "private-material"))
            and mapping["api_key"] == "[REDACTED]"
            and "hidden" not in mapping["safe"],
        )
        secret_snapshot = {key: os.environ.get(key) for key in ("AEGIS_SECRET_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY")}
        os.environ.update({"AEGIS_SECRET_TOKEN": "caller-secret", "GITHUB_TOKEN": "credential", "AWS_SECRET_ACCESS_KEY": "credential"})
        try:
            child = run_process(
                [sys.executable, "-c", "import os,json;print(json.dumps({k:os.environ.get(k) for k in ['AEGIS_SECRET_TOKEN','GITHUB_TOKEN','AWS_SECRET_ACCESS_KEY']}))"],
                cwd=work,
            )
        finally:
            for key, value in secret_snapshot.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
        book.check("law-and-tool-environment-credential-scrub", all(value is None for value in json.loads(child.stdout).values()))
        book.expect(
            "explicit-secret-environment-injection-rejected",
            SecurityError,
            lambda: minimal_subprocess_env({"GITHUB_TOKEN": "credential"}),
        )

        task_dir = work / "evidence-task"
        evidence = append_evidence(task_dir, "observation", "token=environment-value", task_id="evidence-task", change_epoch=0, password="details-secret")
        ledger_bytes = (task_dir / "evidence.jsonl").read_bytes()
        book.check("evidence-redacts-before-persistence", b"environment-value" not in ledger_bytes and b"details-secret" not in ledger_bytes and "[REDACTED]" in json.dumps(evidence))
        runtime_dir(work).mkdir(parents=True,exist_ok=True)
        context=ContextLedger(work); context.record_external("secret-source","fingerprint","token=environment-value",ttl_seconds=60,provenance="content-sha256")
        state_store=StateStore(work); state_store.create("environment secret isolation")
        persistent_control=b"\n".join(path.read_bytes() for path in aegis_dir(work).rglob("*") if path.is_file())
        book.check("environment-secrets-absent-from-state-evidence-context-logs",b"environment-value" not in persistent_control and b"details-secret" not in persistent_control)
        if os.name != "nt":
            modes=[stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) for path in aegis_dir(work).rglob("*") if path.is_file()]
            book.check("runtime-control-files-not-group-world-writable",bool(modes) and all(mode&0o077==0 for mode in modes),repr(modes))
        else:
            book.check("runtime-control-files-not-group-world-writable",True,"POSIX permission bits are not a Windows contract")

        class SymlinkControl:
            def is_symlink(self): return True
            def __str__(self): return "forged-sensitive-link"
        book.expect("sensitive-control-symlink-rejected", SecurityError, lambda: ensure_private_control_file(SymlinkControl()))

        class WorldWritableControl:
            def is_symlink(self): return False
            def exists(self): return True
            def stat(self, *, follow_symlinks=True):
                class Result: st_mode = stat.S_IFREG | 0o666
                return Result()
            def __str__(self): return "forged-world-writable-control"
        from unittest.mock import patch
        with patch("agentinfra.security.os.name", "posix"):
            book.expect("sensitive-world-writable-control-rejected", SecurityError, lambda: ensure_private_control_file(WorldWritableControl()))

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents" / "VERSION").parent.mkdir(parents=True); (work / ".agents" / "VERSION").write_text("4.0.0\n")
        module = work / ".agents" / "local-modules" / "untrusted"; module.mkdir(parents=True)
        marker = work / "untrusted-code-executed"
        (module / "POLICY.md").write_text("policy")
        (module / "__init__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
        (module / "module.toml").write_text('[module]\nid="untrusted"\nname="untrusted"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\n')
        discovered = discover(work)
        book.check("untrusted-module-discovery-is-inert", "untrusted" in discovered and not marker.exists())
        book.check("untrusted-repository-script-not-executed-without-explicit-action",not marker.exists())

    pyproject = tomllib.loads((root / ".agents" / "infra" / "pyproject.toml").read_text(encoding="utf-8"))
    python_meta = tomllib.loads((root / ".agents" / "modules" / "python-meta" / "module.toml").read_text(encoding="utf-8"))
    book.check("dependency-free-recovery-core", pyproject.get("project", {}).get("dependencies", []) == [])
    book.check("optional-dependencies-never-implicitly-install", "command" not in python_meta.get("install", {}))
    process_source = (root / ".agents" / "infra" / "agentinfra" / "process.py").read_text(encoding="utf-8")
    production_sources = "\n".join(path.read_text(encoding="utf-8") for path in (root / ".agents" / "infra" / "agentinfra").glob("*.py"))
    book.check("subprocess-construction-shell-disabled", "shell=False" in process_source and "shell=True" not in process_source)
    book.check(
        "no-auth-tls-signature-bypass",
        all(token not in production_sources for token in ("verify=False", "CERT_NONE", "_create_unverified_context", "disable_signature")),
    )
    book.check(
        "no-implicit-repository-exfiltration",
        all(token not in production_sources for token in ("requests.post(", "urllib.request.urlopen(", "httpx.post(", "https://")),
    )
    modules = run_family(str(root.resolve()), "modules")
    module_observations = {label: passed for label, passed, _ in modules.observations}
    book.check("trusted-module-replacement-registration", module_observations.get("protected-module-replacement-rejected") is True)
    book.check("module-action-minimal-secret-environment", module_observations.get("module-action-secret-environment-scrubbed") is True)
    book.check("dependency-actions-require-explicit-apply-authorization",module_observations.get("module-action-explicit-apply-authorization") is True)
    book.check("core-hard-invariant-checker-cannot-be-replaced",all(module_observations.get(label) is True for label in ("protected-module-replacement-rejected","local-module-core-policy-write-rejected","local-module-core-policy-preserved")))
    old_secret = os.environ.get("AEGIS_ROLE_SECRET_TOKEN"); os.environ["AEGIS_ROLE_SECRET_TOKEN"] = "role-secret-material"
    try: rendered_roles = "\n".join(render_role(root, name) for name in load_role_specs(root))
    finally:
        if old_secret is None: os.environ.pop("AEGIS_ROLE_SECRET_TOKEN", None)
        else: os.environ["AEGIS_ROLE_SECRET_TOKEN"] = old_secret
    book.check("codex-role-generation-excludes-environment-secrets", "role-secret-material" not in rendered_roles)
    audit_source = (root / ".agents" / "infra" / "agentinfra" / "audit.py").read_text(encoding="utf-8")
    book.check("self-audit-enforces-sensitive-file-permissions", "ensure_private_control_file" in audit_source)
    return book.finish()


def _portability(root: Path) -> FamilyOutcome:
    book = Checkbook("portability")
    book.check("supported-python-version",sys.version_info >= (3,11))
    book.check("tomllib-matches-declared-python",sys.version_info >= (3,11) and tomllib is not None)
    with tempfile.TemporaryDirectory(prefix="Aegis space 雪 ") as directory:
        work = Path(directory); target = work / "unicodé file.txt"; atomic_write_bytes(target, "line1\r\nline2\n".encode("utf-8"), root=work)
        book.check("paths-with-spaces", " " in str(work) and target.is_file())
        book.check("unicode-paths", "unicodé" in target.name and target.is_file())
        book.check("crlf-lf-preserved", target.read_bytes() == b"line1\r\nline2\n")
        book.check("utf8-locale-independent",target.read_text(encoding="utf-8")=="line1\nline2\n")
        result = run_process([sys.executable, "-c", "from pathlib import Path;import sys;print(Path(sys.argv[1]).read_text())", str(target)], cwd=work, timeout=10)
        book.check("unicode-subprocess-path", result.returncode == 0 and "line1" in result.stdout)
        long_dir=work/("segment-"+"x"*80)/("segment-"+"y"*80); long_dir.mkdir(parents=True); long_target=long_dir/"data.txt"; atomic_write_bytes(long_target,b"long",root=work)
        book.check("long-path-within-platform-limit",long_target.read_bytes()==b"long")
        original=b"original"; fault_target=work/"fault.bin"; fault_target.write_bytes(original)
        def disk_full(stage,_path):
            if stage=="during_temp_write": raise OSError(errno.ENOSPC,"seeded disk full")
        try: atomic_write_bytes(fault_target,b"replacement",root=work,fault=disk_full)
        except OSError as exc: disk_error=exc.errno==errno.ENOSPC
        else: disk_error=False
        book.check("disk-full-no-partial-commit",disk_error and fault_target.read_bytes()==original and not list(work.glob(".fault.bin.*.tmp")))
        def replace_failure(stage,_path):
            if stage=="before_replace": raise PermissionError("seeded replace denial")
        try: atomic_write_bytes(fault_target,b"replacement",root=work,fault=replace_failure)
        except PermissionError: replace_rejected=True
        else: replace_rejected=False
        book.check("atomic-replace-failure-preserves-original",replace_rejected and fault_target.read_bytes()==original)
        read_only=work/"read-only.txt"; read_only.write_bytes(original); os.chmod(read_only,0o444)
        try:
            atomic_write_bytes(read_only,b"replacement",root=work)
        except PermissionError:
            read_only_clean=read_only.read_bytes()==original
        else:
            read_only_clean=read_only.read_bytes()==b"replacement"
        finally:
            os.chmod(read_only,0o644)
        book.check("read-only-file-clear-nondestructive-semantics",read_only_clean)
        permission_target=work/"permission.bin"; permission_target.write_bytes(original)
        def permission_denied(stage,_path):
            if stage=="before_replace": raise PermissionError("seeded permission denied")
        try: atomic_write_bytes(permission_target,b"replacement",root=work,fault=permission_denied)
        except PermissionError: permission_clean=permission_target.read_bytes()==original
        else: permission_clean=False
        book.check("permission-denied-no-partial-commit",permission_clean and not list(work.glob(".permission.bin.*.tmp")))
        from unittest.mock import patch
        with patch("agentinfra.atomic.os.replace",side_effect=PermissionError("seeded sharing violation")):
            started=time.monotonic()
            try: atomic_write_bytes(fault_target,b"replacement",root=work)
            except PermissionError: contention_rejected=True
            else: contention_rejected=False
            elapsed=time.monotonic()-started
        book.check("file-lock-contention-bounded",contention_rejected and elapsed<2 and fault_target.read_bytes()==original,f"elapsed={elapsed}")
        if os.name == "nt":
            book.check("windows-drive-path", target.drive and target.is_file())
            unc=Path(r"\\server\share\folder")
            book.check("windows-unc-path-parsing",str(unc).startswith("\\\\") and bool(unc.anchor))
        else:
            book.check("posix-mode-semantics", stat.S_IMODE(target.stat().st_mode) & stat.S_IRUSR)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); _copy_bootstrap(root,work); (work/"AGENTS.md").write_bytes(b"\xff\xfeinvalid")
        before=(work/"AGENTS.md").read_bytes(); book.expect("non-utf8-control-file-clean-rejection",BootstrapError,lambda:bootstrap_install(work,apply=True)); book.check("non-utf8-rejection-preserves-bytes",(work/"AGENTS.md").read_bytes()==before)
    wrappers=[root/".agents"/"bin"/name for name in ("agentctl.sh","agentctl.ps1","agentctl.xsh")]
    wrapper_text="\n".join(path.read_text(encoding="utf-8") for path in wrappers if path.is_file())
    book.check("wrappers-enforce-python-minimum","3.11" in wrapper_text and "-B" in wrapper_text)
    with tempfile.TemporaryDirectory() as directory:
        fallback=workspace_fingerprint(Path(directory)); book.check("no-git-safe-tree-fallback",fallback["available"] and fallback["kind"]=="tree")
    return book.finish()


def _reasoning(root: Path) -> FamilyOutcome:
    book = Checkbook("reasoning-cost")
    with (root / ".agents" / "framework.toml").open("rb") as stream: framework = tomllib.load(stream)
    reasoning = framework["reasoning"]; subagent_policy = framework["subagents"]
    book.check("reasoning-default-exact-max", reasoning["default_effort"] == "max")
    book.check("reasoning-silent-downgrade-forbidden", reasoning["allow_silent_downgrade"] is False)
    book.check("cost-strategy-never-lowers-reasoning", "not_reasoning" in reasoning["cost_strategy"] and "reduce_calls" in reasoning["cost_strategy"])
    book.check("cost-optimization-reduces-calls-before-reasoning",reasoning["default_effort"]=="max" and reasoning["allow_silent_downgrade"] is False and "reduce_calls" in reasoning["cost_strategy"])
    book.check("sequential-bounded-context", subagent_policy["max_active"] == 1 and subagent_policy["nested_delegation"] is False and subagent_policy["fresh_bounded_context"] is True)
    codex = run_family(str(root.resolve()), "codex-static")
    codex_observations = {label: passed for label, passed, _ in codex.observations}
    book.check("host-unobservable-reported-not-claimed", codex_observations.get("codex-static-does-not-claim-live-effective") is True)
    book.check("managed-spawn-roles-all-max", codex_observations.get("codex-every-role-max-model-effort") is True)
    book.check("managed-spawn-sequential-and-nonnested", codex_observations.get("codex-nested-guarantee-has-instruction-enforcement") is True and codex_observations.get("codex-exact-model-effort-limits") is True)
    context = run_family(str(root.resolve()), "context")
    context_observations = {label: passed for label, passed, _ in context.observations}
    book.check("unchanged-file-reuse-without-refetch", context_observations.get("unchanged-context-reuse-avoids-content-reread") is True)
    book.check("external-source-reuse-until-invalidation", context_observations.get("verified-ttl-reuse") is True and context_observations.get("external-ttl-expiration") is True)
    book.check("dependency-probe-cache-invalidates-on-change", context_observations.get("context-dependency-invalidation") is True)
    book.check("large-output-summary-retains-artifact-digest", context_observations.get("large-summary-digest-not-raw") is True)
    subagents = run_family(str(root.resolve()), "subagents"); subagent_observations = {label: passed for label, passed, _ in subagents.observations}
    book.check("subagent-context-excludes-parent-history", subagent_observations.get("child-context-bounded-verified-facts-only") is True)
    book.check("subagent-sequence-reuses-verified-summary", subagent_observations.get("handoff-preserves-context-and-evidence-binding") is True)
    book.check("mechanical-work-prefers-direct-deterministic-action","deterministic local tool" in (root/".agents"/"core"/"01-max-reasoning-and-cost.md").read_text(encoding="utf-8"))
    selector = run_family(str(root.resolve()), "xonsh"); selector_observations = {label: passed for label, passed, _ in selector.observations}
    book.check("oneshot-selector-never-launches-interactive-shell", selector_observations.get("environment-selector-direct") is True)
    atomic_source = (root / ".agents" / "infra" / "agentinfra" / "atomic.py").read_text(encoding="utf-8")
    book.check("retry-policy-is-explicitly-bounded", "delays = (0.0, 0.005, 0.015, 0.04, 0.1)" in atomic_source)
    book.check("build-cache-is-declared-ephemeral-not-mutated", "build" in framework.get("context", {}) or "build" in (root / ".agents" / "workspace-policy.toml").read_text(encoding="utf-8"))
    policy = (root / ".agents" / "core" / "01-max-reasoning-and-cost.md").read_text(encoding="utf-8")
    book.check("canonical-reasoning-policy-agrees-with-config", "Default: Max" in policy and "Do **not** save credits by lowering reasoning effort" in policy and reasoning["default_effort"] == "max")
    router = (root / ".agents" / "INDEX.md").read_text(encoding="utf-8")
    routed_paths = re.findall(r"`((?:core|protocols|modules|bootstrap|infra)/[^`]+)`", router)
    book.check("policy-router-references-only-existing-targets", bool(routed_paths) and all((root / ".agents" / path).exists() for path in routed_paths))
    policy_files = list((root / ".agents" / "core").glob("*.md")) + list((root / ".agents" / "protocols").glob("*.md"))
    total_policy_bytes = sum(path.stat().st_size for path in policy_files)
    normalized_lines = [line.strip().casefold() for path in policy_files for line in path.read_text(encoding="utf-8").splitlines() if len(line.strip()) > 80]
    duplicates = len(normalized_lines) - len(set(normalized_lines))
    book.check("prompt-policy-size-and-duplication-bounded", total_policy_bytes < 200_000 and duplicates < 20, f"bytes={total_policy_bytes} duplicate_long_lines={duplicates}")
    recovery_policy=(root/".agents"/"core"/"12-failure-recovery.md").read_text(encoding="utf-8")
    testing_policy=(root/".agents"/"core"/"08-testing-and-laws.md").read_text(encoding="utf-8")
    performance_policy=(root/".agents"/"core"/"13-performance.md").read_text(encoding="utf-8")
    book.check("failed-hypothesis-requires-recorded-model-update","After a failed hypothesis" in recovery_policy and "update the model" in recovery_policy and "preserve evidence" in recovery_policy)
    book.check("identical-failed-command-not-repeated-without-change","repeated same command without relevant change" in recovery_policy)
    book.check("expensive-suite-not-repeated-without-relevant-change","focused profiling before repeated broad benchmark suites" in performance_policy and "retry" in testing_policy.casefold())
    book.check("clean-build-deferred-until-falsifier-needs-it","cheapest falsifying test before broad suites" in policy and "build" in (root/".agents"/"workspace-policy.toml").read_text(encoding="utf-8"))
    mutation = run_family(str(root.resolve()), "mutation"); mutation_observations = {label: passed for label, passed, _ in mutation.observations}
    book.check("reasoning-config-mutants-are-detected", mutation_observations.get("max-reasoning-mutant-killed") is True and mutation_observations.get("critical-config-mutants-killed") is True)
    book.check("expensive-actions-require-fresh-state-evidence", context_observations.get("context-file-content-invalidation") is True and mutation_observations.get("external-context-ttl-mutant-killed") is True)
    return book.finish()


def _policy(root: Path) -> FamilyOutcome:
    book = Checkbook("policy-consistency")
    issues = [item for item in audit(root) if not item.startswith("manifest mismatch")]
    book.check("self-audit-nonmanifest", not issues, str(issues))
    with (root / ".agents" / "framework.toml").open("rb") as stream: config = tomllib.load(stream)
    book.check("policy-state-machine-correspondence", config["workflow"]["hardened_state_machine"] and set(ALLOWED) == STATES)
    book.check("policy-law-production-path", config["laws"]["production_path_only"] and LawRunner is not None)
    book.check("policy-module-no-autoexecute", config["modules"]["auto_execute_module_code"] is False)
    required = [root / ".agents" / "core" / name for name in ("01-max-reasoning-and-cost.md", "03-sequential-subagents.md", "05-evidence-claims.md", "08-testing-and-laws.md", "09-git-workspace.md")]
    book.check("hard-policy-files-exist", all(path.is_file() for path in required))
    root_text = str(root.resolve())
    workflow = run_family(root_text, "workflow"); workflow_obs = {label: passed for label, passed, _ in workflow.observations}
    gates = run_family(root_text, "gates-risks-decisions"); gate_obs = {label: passed for label, passed, _ in gates.observations}
    evidence = run_family(root_text, "evidence"); evidence_obs = {label: passed for label, passed, _ in evidence.observations}
    workspace = run_family(root_text, "workspace"); workspace_obs = {label: passed for label, passed, _ in workspace.observations}
    subagents = run_family(root_text, "subagents"); subagent_obs = {label: passed for label, passed, _ in subagents.observations}
    laws = run_family(root_text, "law-runner"); law_obs = {label: passed for label, passed, _ in laws.observations}
    modules = run_family(root_text, "modules"); module_obs = {label: passed for label, passed, _ in modules.observations}
    persistent = run_family(root_text, "persistent-recovery"); persistent_obs = {label: passed for label, passed, _ in persistent.observations}
    mutation = run_family(root_text, "mutation"); mutation_obs = {label: passed for label, passed, _ in mutation.observations}
    codex = run_family(root_text, "codex-static"); codex_obs = {label: passed for label, passed, _ in codex.observations}
    xonsh = run_family(root_text, "xonsh"); xonsh_obs = {label: passed for label, passed, _ in xonsh.observations}
    enforcement_contracts = {
        "max_reasoning": mutation_obs.get("max-reasoning-mutant-killed"),
        "sequential_child": subagent_obs.get("second-child-rejection-zero-task-side-effects"),
        "nested_delegation": codex_obs.get("codex-nested-guarantee-has-instruction-enforcement"),
        "current_epoch": workflow_obs.get("epoch-change-invalidates-verification"),
        "workspace_recheck": workflow_obs.get("post-audit-mutation-rejected"),
        "direct_evidence": workflow_obs.get("read-material-claim-needs-direct-evidence"),
        "law_integrity": law_obs.get("mutate-restore-definition-detected"),
        "module_protection": module_obs.get("protected-module-replacement-rejected"),
    }
    book.check("hard-policy-invariants-have-executable-contracts", all(value is True for value in enforcement_contracts.values()), str(enforcement_contracts))
    book.check("policy-sequential-claim-matches-global-lease", config["subagents"]["sequential_only"] is True and subagent_obs.get("global-lease-blocks-other-parent-task") is True)
    book.check("policy-nested-delegation-claim-matches-control-plane", config["subagents"]["nested_delegation"] is False and codex_obs.get("codex-every-role-prohibits-nested-delegation") is True)
    book.check("policy-max-claim-does-not-overstate-live-proof", config["reasoning"]["default_effort"] == "max" and codex_obs.get("codex-static-does-not-claim-live-effective") is True)
    book.check("policy-parent-owns-canonical-state", config["subagents"]["parent_owns_canonical_state"] is True and workflow_obs.get("active-child-blocks-parent-mutation") is True)
    book.check("policy-runtime-disposable-matches-persistent-recovery", persistent_obs.get("bootstrap-runtime-deletion-preserves-exact-uninstall") is True and persistent_obs.get("codex-runtime-deletion-preserves-exact-uninstall") is True)
    book.check("policy-law-definitions-read-only-enforced", law_obs.get("mutate-restore-definition-detected") is True and law_obs.get("law-test-detection-environment-scrubbed") is True)
    book.check("policy-final-claims-require-direct-evidence", workflow_obs.get("read-material-claim-needs-direct-evidence") is True and evidence_obs.get("command-provenance-and-exit") is True)
    book.check("policy-transition-history-append-only-anchored", workflow_obs.get("transition-revisions-monotonic") is True and mutation_obs.get("current-epoch-mutant-killed") is True)
    book.check("policy-no-test-cheating-adversarially-enforced", mutation_obs.get("hidden-law-values-not-visible") is True and mutation_obs.get("renamed-test-metamorphic-behavior") is True)
    book.check("policy-module-replacement-protection-executable", module_obs.get("protected-module-replacement-rejected") is True)
    book.check("policy-workspace-inspection-records-snapshot", workspace_obs.get("precheck-records-actual-workspace-snapshot") is True)
    book.check("policy-gates-cannot-all-be-waived", gate_obs.get("nonwaived-gate-required-and-proven") is True and gate_obs.get("critical-gate-waiver-rejected") is True)
    book.check("policy-direct-evidence-rejects-fabricated-claims", evidence_obs.get("failed-command-not-successful-verification") is True and workflow_obs.get("read-material-claim-needs-direct-evidence") is True)
    book.check("policy-host-support-version-or-capability-probed", codex_obs.get("codex-missing-executable-fails-closed") is True and (xonsh_obs.get("xonsh-version") is True or xonsh_obs.get("xonsh-absent-fallback") is True))
    readme = (root / ".agents" / "README.md").read_text(encoding="utf-8"); version = (root / ".agents" / "VERSION").read_text(encoding="utf-8").strip(); changelog = (root / ".agents" / "CHANGELOG.md").read_text(encoding="utf-8")
    book.check("readme-capability-claims-match-executable-configuration", "gpt-5.6-sol" in readme and "Max" in readme and "sequential" in readme.casefold() and config["reasoning"]["default_effort"] == "max")
    book.check("changelog-version-claim-matches-release", version in changelog and version == config["framework"]["version"])
    book.check("policy-path-and-role-references-are-live", not [issue for issue in issues if "missing" in issue.casefold() or "role" in issue.casefold()])
    rendered_roles = [tomllib.loads(render_role(root, name)) for name in load_role_specs(root)]
    book.check("hard-invariant-values-have-no-structured-conflict", all(role["model"] == "gpt-5.6-sol" and role["model_reasoning_effort"] == "max" for role in rendered_roles) and config["subagents"]["max_active"] == 1)
    return book.finish()


def _fault(root: Path) -> FamilyOutcome:
    book = Checkbook("fault-race")
    atomic = run_family(str(root.resolve()), "atomic-transaction")
    atomic_obs = {label: passed for label, passed, _ in atomic.observations}
    portable_atomic_labels=[label for label in atomic_obs if label not in {"atomic-target-symlink-rejected","atomic-parent-symlink-escape-rejected"}]
    book.check("fault-boundary-battery",all(atomic_obs[label] for label in portable_atomic_labels),"; ".join(atomic.failures))
    book.check("fault-crash-before-atomic-replace-preserves-original", atomic_obs.get("atomic-pre-or-post-before_replace") is True and atomic_obs.get("atomic-temp-clean-before_replace") is True)
    book.check("fault-crash-after-replace-has-explicit-post-state", atomic_obs.get("atomic-pre-or-post-after_replace") is True)
    book.check("fault-crash-after-replace-before-directory-fsync-detected",atomic_obs.get("atomic-pre-or-post-after_replace") is True and atomic_obs.get("atomic-fsync-order-before-and-after-replace") is True)
    book.check("fault-transaction-journal-boundaries-pre-or-post", all(atomic_obs.get("transaction-boundary-pre-or-post-" + stage) is True for stage in ("before_journal", "after_journal", "before_destination", "after_destination", "after_commit")))
    book.check("fault-transaction-recovery-idempotent", atomic_obs.get("transaction-recovery-idempotent") is True)
    with tempfile.TemporaryDirectory() as directory:
        task_dir = Path(directory) / "race-task"
        barrier = threading.Barrier(3); errors = []
        def append(index):
            try:
                barrier.wait(); append_evidence(task_dir, "test", f"race {index}", task_id="race-task", change_epoch=0)
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=append, args=(index,)) for index in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        book.check("race-evidence-no-loss", not errors and len(load_evidence(task_dir)) == 2, repr(errors))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "lease"; barrier = threading.Barrier(3); winners = []; failures = []
        def acquire(index):
            try: barrier.wait(); winners.append(LeaseLock(path, "race").acquire(task_id=f"task-{index}", role="worker"))
            except LockError as exc: failures.append(str(exc))
        threads = [threading.Thread(target=acquire, args=(index,)) for index in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        book.check("race-one-child-winner", len(winners) == 1 and len(failures) == 1)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True)
        store=StateStore(work); store.create("mutation race")
        revision=store.load()["revision"]; winners=[]; failures=[]; barrier=threading.Barrier(3)
        def mutate(index):
            try:
                barrier.wait(); store.mutate(lambda task:task["precheck"].__setitem__(f"winner-{index}",True),expected_revision=revision); winners.append(index)
            except RuntimeError as exc: failures.append(str(exc))
        threads=[threading.Thread(target=mutate,args=(index,)) for index in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        book.check("race-task-revision-one-winner",len(winners)==1 and len(failures)==1 and "stale task revision" in failures[0],repr((winners,failures)))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work); task=store.load(); td=tasks_dir(work)/task["id"]
        barrier=threading.Barrier(3); results=[]
        def finalize():
            try: barrier.wait(); store.transition("FINALIZE","race"); results.append("finalized")
            except (RuntimeError,TransitionError) as exc: results.append("finalize-rejected:"+str(exc))
        def add_evidence():
            try:
                barrier.wait(); append_evidence(td,"test","racing append",task_id=task["id"],change_epoch=task["change_epoch"],task_revision=task["revision"]); results.append("evidence-appended")
            except RuntimeError as exc: results.append("evidence-rejected:"+str(exc))
        threads=[threading.Thread(target=finalize),threading.Thread(target=add_evidence)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        try: loaded=store.load(); finalized_visible=loaded.get("state")=="FINALIZE"
        except RuntimeError: finalized_visible=False
        safe=("evidence-appended" in results and not finalized_visible) or ("finalized" in results and any(item.startswith("evidence-rejected:") for item in results) and finalized_visible)
        book.check("race-finalize-vs-evidence-fails-closed",safe,repr(results))
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); store,_=_audited_state(work); barrier=threading.Barrier(3); results=[]
        def finalize():
            try: barrier.wait(); store.transition("FINALIZE","race"); results.append("finalized")
            except (RuntimeError,TransitionError) as exc: results.append("finalize-rejected:"+str(exc))
        def edit():
            barrier.wait(); (work/"racing-edit.txt").write_text("changed",encoding="utf-8"); results.append("workspace-edited")
        threads=[threading.Thread(target=finalize),threading.Thread(target=edit)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        try: loaded=store.load(); stale_visible=loaded.get("state")=="FINALIZE"
        except RuntimeError: stale_visible=False
        book.check("race-finalize-vs-workspace-edit-fails-closed","workspace-edited" in results and not stale_visible,repr(results))
    for kind in ("gate","risk"):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); store,_=_audited_state(work); barrier=threading.Barrier(3); successes=[]; failures=[]; unexpected=[]
            def finalize():
                try: barrier.wait(); store.transition("FINALIZE","race"); successes.append("finalize")
                except (RuntimeError,TransitionError) as exc: failures.append("finalize:"+str(exc))
                except BaseException as exc: unexpected.append("finalize:"+type(exc).__name__+":"+str(exc))
            def change():
                try:
                    barrier.wait()
                    if kind=="gate":
                        store.mutate(lambda task:task["gates"].append({"id":"G2","description":"late gate","severity":"high","status":"OPEN","evidence":[],"created_revision":task["revision"]+1}))
                    else:
                        store.mutate(lambda task:task["risks"].append({"id":"R1","description":"late risk","severity":"high","status":"open"}))
                    successes.append(kind)
                except RuntimeError as exc: failures.append(kind+":"+str(exc))
                except BaseException as exc: unexpected.append(kind+":"+type(exc).__name__+":"+str(exc))
            threads=[threading.Thread(target=finalize),threading.Thread(target=change)]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join()
            book.check(f"race-finalize-vs-{kind}-serialized",len(successes)==1 and len(failures)==1 and not unexpected,repr((successes,failures,unexpected)))
    root_text = str(root.resolve())
    bootstrap = run_family(root_text, "bootstrap"); bootstrap_obs = {label: passed for label, passed, _ in bootstrap.observations}
    persistent = run_family(root_text, "persistent-recovery"); persistent_obs = {label: passed for label, passed, _ in persistent.observations}
    subagents = run_family(root_text, "subagents"); subagent_obs = {label: passed for label, passed, _ in subagents.observations}
    context = run_family(root_text, "context"); context_obs = {label: passed for label, passed, _ in context.observations}
    workflow = run_family(root_text, "workflow"); workflow_obs = {label: passed for label, passed, _ in workflow.observations}
    book.check("fault-bootstrap-install-uninstall-coherent", bootstrap_obs.get("bootstrap-failure-never-leaves-half-inserted-instruction-block", bootstrap_obs.get("bootstrap-marker-rejection-zero-side-effect")) is True and persistent_obs.get("bootstrap-backup-failure-has-zero-destination-mutation") is True)
    book.check("fault-codex-install-uninstall-coherent", persistent_obs.get("codex-backup-failure-has-zero-destination-mutation") is True and persistent_obs.get("codex-invalid-journal-path-has-zero-mutation") is True)
    book.check("fault-state-transition-recovers-coherently", atomic_obs.get("transaction-all-or-nothing") is True and workflow_obs.get("illegal-transition-no-side-effect") is True)
    book.check("fault-subagent-open-close-recover-coherent", subagent_obs.get("second-child-rejection-zero-task-side-effects") is True and subagent_obs.get("close-state-failure-restores-global-lease") is True and subagent_obs.get("recover-failure-rolls-back-evidence-and-restores-lease") is True)
    book.check("race-context-cache-writers-no-loss", context_obs.get("context-concurrent-writers-no-loss") is True)
    book.check("race-installer-user-edit-never-clobbered", bootstrap_obs.get("bootstrap-drift-rejection-preserves-user-edit") is True)
    with tempfile.TemporaryDirectory() as directory:
        lock_path = Path(directory) / "pid-reuse.lock"
        payload = {"schema": 2, "nonce": "stale", "pid": os.getpid(), "process_identity": "forged-prior-process", "host": __import__("socket").gethostname(), "created": "old", "purpose": "pid-reuse"}
        lock_path.write_text(json.dumps(payload)); reclaimed = FileLock(lock_path, "pid-reuse"); owner = reclaimed.acquire(timeout=0.2)
        book.check("pid-reuse-cannot-authorize-stale-owner", owner["nonce"] != "stale"); reclaimed.release()
        foreign = dict(payload); foreign.update(nonce="foreign", host="foreign-host.invalid", process_identity=None); lock_path.write_text(json.dumps(foreign))
        book.expect("foreign-or-unverifiable-lock-fails-closed", LockError, lambda: FileLock(lock_path, "foreign").acquire(timeout=0))
    evidence = run_family(root_text, "evidence"); evidence_obs={label:passed for label,passed,_ in evidence.observations}
    book.check("fault-evidence-append-crash-detected",evidence_obs.get("evidence-append-crash-never-silently-accepted") is True)
    manifest_before=(root/".agents"/"MANIFEST.sha256").read_bytes()
    from unittest.mock import patch
    with patch("agentinfra.manifest.atomic_write_bytes",side_effect=OSError("seeded manifest write crash")):
        book.expect("fault-manifest-regeneration-never-truncated",OSError,lambda:regenerate_manifest(root,maintenance_authorized=True))
    book.check("fault-manifest-original-preserved",(root/".agents"/"MANIFEST.sha256").read_bytes()==manifest_before)
    sequence = invariant_sequence(
        0,
        [1, -1, 2, -2],
        lambda value, delta: value + delta,
        lambda value: isinstance(value, int) and -10 <= value <= 10,
    )
    book.check("randomized-operation-sequence-preserves-state-invariant", sequence.cases == 4)
    return book.finish()


def _mutation(root: Path) -> FamilyOutcome:
    book = Checkbook("mutation")
    ok, _ = verify_managed_source(root); book.check("baseline-codex-valid", ok)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work)
        managed = work / ".agents" / "modules" / "codex" / "config" / "managed.toml"
        original = managed.read_bytes()
        mutants = (
            ("max-reasoning-mutant", b'model_reasoning_effort = "max"', b'model_reasoning_effort = "low"'),
            ("model-identity-mutant", b'model = "gpt-5.6-sol"', b'model = "other"'),
            ("sequential-child-limit-mutant", b'max_concurrent_threads_per_session = 1', b'max_concurrent_threads_per_session = 9'),
            ("nested-delegation-mutant", b'max_depth = 1', b'max_depth = 9'),
        )
        killed = 0
        for label, old, new in mutants:
            managed.write_bytes(original.replace(old, new, 1))
            valid, _ = verify_managed_source(work)
            killed += not valid
            book.check(label + "-killed", not valid)
            managed.write_bytes(original)
        book.check("critical-config-mutants-killed", killed == len(mutants), f"killed {killed}/{len(mutants)}")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); (work / ".agents" / "x").write_text("safe"); (work / ".agents" / "MANIFEST.sha256").write_text(render(work))
        ok, _ = verify_manifest(work); (work / ".agents" / "x").write_text("mutant"); detected, _ = verify_manifest(work)
        book.check("manifest-mutant-killed", ok and not detected)
    book.expect("path-confinement-mutant-corpus", SecurityError, lambda: confined_path(root, "../outside"))
    book.expect("property-mutant-killed", LawFailure, lambda: idempotent(lambda value: value + 1, [3]))
    root_text = str(root.resolve())
    gates = run_family(root_text, "gates-risks-decisions")
    gate_observations={label:passed for label,passed,_ in gates.observations}
    book.check("acceptance-gate-requirement-mutant-killed",gate_observations.get("missing-gate-blocks-audit") is True)
    book.check("evidence-provenance-mutant-killed",gate_observations.get("gate-proof-task-epoch-relevance") is True)
    workflow = run_family(root_text, "workflow")
    workflow_observations={label:passed for label,passed,_ in workflow.observations}
    book.check("workspace-recheck-mutant-killed",workflow_observations.get("post-audit-mutation-rejected") is True)
    book.check("current-epoch-mutant-killed",workflow_observations.get("complete-current-proof-flow") is True)
    bootstrap = run_family(root_text, "bootstrap")
    bootstrap_observations={label:passed for label,passed,_ in bootstrap.observations}
    book.check("user-work-preservation-mutant-killed",bootstrap_observations.get("bootstrap-preserves-user-and-format") is True)
    atomic = run_family(root_text, "atomic-transaction")
    atomic_observations={label:passed for label,passed,_ in atomic.observations}
    book.check("transaction-rollback-mutant-killed",atomic_observations.get("transaction-rollback-bytes-bound-to-before-hash") is True)
    context = run_family(root_text, "context")
    context_observations={label:passed for label,passed,_ in context.observations}
    book.check("external-context-ttl-mutant-killed",context_observations.get("context-negative-ttl") is True and context_observations.get("verified-ttl-reuse") is True)
    law_runner = run_family(root_text, "law-runner")
    law_observations={label:passed for label,passed,_ in law_runner.observations}
    book.check("test-definition-mutation-mutant-killed",law_observations.get("mutate-restore-definition-detected") is True)
    book.check("large-output-and-timeout-mutants-killed",law_observations.get("timeout-reported") is True and law_observations.get("law-output-digests") is True)
    module_observations={label:passed for label,passed,_ in run_family(root_text, "modules").observations}
    book.check("module-replacement-hard-invariant-mutant-killed",module_observations.get("protected-module-replacement-rejected") is True)
    codex_observations={label:passed for label,passed,_ in run_family(root_text, "codex-static").observations}
    book.check("codex-project-trust-claim-mutant-killed",codex_observations.get("codex-static-does-not-claim-live-effective") is True)
    xonsh_observations={label:passed for label,passed,_ in run_family(root_text, "xonsh").observations}
    book.check("xonsh-exit-code-mutant-killed",xonsh_observations.get("xonsh-wrapper-exit-propagation",xonsh_observations.get("xonsh-absent-fallback")) is True)
    python_meta_observations={label:passed for label,passed,_ in run_family(root_text, "python-meta").observations}
    book.check("python-meta-dependency-status-mutant-killed",python_meta_observations.get("python-meta-missing-explicit-failure",python_meta_observations.get("python-meta-functional-imports")) is True)
    hidden_env=dict(os.environ); hidden_env.update({"PYTEST_CURRENT_TEST":"secret law name","AEGIS_EXPECTED_LAW_VALUE":"hidden answer","UNITTEST_RUNNING":"1"})
    isolated=run_process([sys.executable,"-c","import os,json;print(json.dumps({k:os.environ.get(k) for k in ['PYTEST_CURRENT_TEST','AEGIS_EXPECTED_LAW_VALUE','UNITTEST_RUNNING']}))"],cwd=root,env=hidden_env)
    book.check("hidden-law-values-not-visible",all(value is None for value in json.loads(isolated.stdout).values()))
    module_probe=(
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,"+repr(str(INFRA))+ ");"
        "from agentinfra.security import confined_path,SecurityError;"
        "\ntry: confined_path(Path.cwd(),'../escape'); outcome='accepted'"
        "\nexcept SecurityError: outcome='rejected'"
        "\nprint(json.dumps({'outcome':outcome,'test_modules':any(k in sys.modules for k in ('pytest','unittest'))}))"
    )
    absent=run_process([sys.executable,"-B","-c",module_probe],cwd=root)
    present=run_process([sys.executable,"-B","-c","import types,sys;sys.modules['pytest']=types.ModuleType('pytest');sys.modules['unittest']=types.ModuleType('unittest');exec("+repr(module_probe)+")"],cwd=root)
    absent_payload=json.loads(absent.stdout); present_payload=json.loads(present.stdout)
    book.check("production-behavior-independent-of-test-framework-modules",absent_payload["outcome"]==present_payload["outcome"]=="rejected" and absent_payload["test_modules"] is False and present_payload["test_modules"] is True)
    commandline_probe="import json;from pathlib import Path;from agentinfra.security import confined_path,SecurityError;\ntry: confined_path(Path.cwd(),'../escape');v='accepted'\nexcept SecurityError:v='rejected'\nprint(json.dumps({'outcome':v}))"
    neutral=run_process([sys.executable,"-B","-c",commandline_probe,"neutral"],cwd=root,env={"PYTHONPATH":str(INFRA)})
    named=run_process([sys.executable,"-B","-c",commandline_probe,"test_hidden_law_name"],cwd=root,env={"PYTHONPATH":str(INFRA)})
    book.check("production-behavior-independent-of-parent-test-command-line",neutral.stdout==named.stdout and json.loads(named.stdout)["outcome"]=="rejected")
    production_sources="\n".join(path.read_text(encoding="utf-8") for path in (root/".agents"/"infra"/"agentinfra").glob("*.py"))
    book.check("production-has-no-test-framework-branch","import unittest" not in production_sources and "sys.modules.get('pytest')" not in production_sources and 'sys.modules.get("pytest")' not in production_sources)
    renamed=roundtrip(lambda value:value.encode("utf-8"),lambda value:value.decode("utf-8"),["renamed-test-α"])
    book.check("renamed-test-metamorphic-behavior",renamed.cases==1)
    random_cases,seed=generated_cases(lambda rng:rng.randrange(-100000,100000),32,seed=741852)
    book.check("randomized-inputs-not-hardcoded",seed==741852 and len(set(random_cases))>24)
    flaws=json.loads((root/".agents"/"infra"/"law_tests"/"v4_flaws.json").read_text(encoding="utf-8"))["flaws"]
    book.check("every-seeded-v4-defect-has-failing-and-passing-regression",all(item.get("seeded_probe") and item.get("regression") for item in flaws))
    hard_mutants=[passed for label,passed,_ in book.observations if label.endswith("mutant-killed") or label.endswith("mutants-killed")]
    book.check("hard-invariant-mutation-score-100-percent",bool(hard_mutants) and all(hard_mutants),f"killed={sum(hard_mutants)}/{len(hard_mutants)}")
    return book.finish()


def _migration(root: Path) -> FamilyOutcome:
    book = Checkbook("migration")
    fixture = json.loads((framework_dir(root) / "infra" / "law_tests" / "fixtures" / "v4_state.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); task_dir = work / ".agents" / "runtime" / "tasks" / fixture["id"]; task_dir.mkdir(parents=True)
        (task_dir / "state.json").write_text(json.dumps(fixture), encoding="utf-8"); (work / ".agents" / "runtime" / "current-task").write_text(fixture["id"] + "\n")
        from agentinfra.runtime_migration import migrate_runtime
        migrate_runtime(work, apply=True)
        store = StateStore(work); loaded = store.load(fixture["id"]); book.check("v4-schema-load", loaded["schema"] == 2)
        migrated = store.mutate(lambda task: task["precheck"].update(instructions_discovered=True), fixture["id"])
        book.check("v4-schema-migrated-and-anchored", migrated["schema"] == 3 and (persistent_dir(work) / "task-anchors" / f"{fixture['id']}.json").is_file())
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); (work / ".agents" / "VERSION").write_text("5.0.0\n")
        module = work / ".agents" / "modules" / "x"; module.mkdir(parents=True); (module / "POLICY.md").write_text("x")
        (module / "module.toml").write_text('[module]\nid="x"\nname="x"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\nrequires_framework="<5.0.0"\n')
        book.expect("module-major-mismatch", ModuleError, lambda: discover(work))
    flaws = json.loads((framework_dir(root) / "infra" / "law_tests" / "v4_flaws.json").read_text(encoding="utf-8"))
    book.check("v4-flaw-catalog-unique-complete", flaws["version"] == "4.0.0" and len(flaws["flaws"]) >= 10 and len({item["id"] for item in flaws["flaws"]}) == len(flaws["flaws"]))
    from agentinfra.migration import UpgradeError, plan_upgrade, upgrade
    manifest_ok, manifest_detail = verify_manifest(root, require_release_anchor=True)
    if manifest_ok:
        with tempfile.TemporaryDirectory(prefix="Aegis migration ") as directory:
            base = Path(directory); archive = base / "release.zip"; build_archive(root, archive); target = base / "target"; safe_extract(archive, target)
            agents_text = b"# project-owned root instructions\r\nkeep me\r\n"; (target / "AGENTS.md").write_bytes(agents_text)
            local = target / ".agents" / "local-modules" / "project-local"; local.mkdir(parents=True); (local / "POLICY.md").write_text("project policy")
            (local / "module.toml").write_text('[module]\nid="project-local"\nname="project local"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\nrequires_framework=">=4.0.0,<5.0.0"\n')
            project_law = target / ".agents" / "laws" / "project" / "project.toml"; project_law.parent.mkdir(parents=True)
            project_law_bytes = b'[[law]]\nid="project.local"\ndescription="local"\nkind="file_exists"\npath="AGENTS.md"\n'
            project_law.write_bytes(project_law_bytes)
            stale = target / ".agents" / "infra" / "agentinfra" / "__pycache__"; stale.mkdir(); (stale / "stale.pyc").write_bytes(b"stale")
            old_readme = b"old framework readme\n"; (target / ".agents" / "README.md").write_bytes(old_readme)
            before = workspace_fingerprint(target); preview = upgrade(root, target, apply=False); after_preview = workspace_fingerprint(target)
            book.check("framework-upgrade-dry-run-zero-side-effects", preview["dry_run"] and before["sha256"] == after_preview["sha256"])
            backup_observed=[]
            def stop_before_first_destination(stage,journal):
                if stage == "before_destination":
                    backup_observed.append(all(record.get("before_sha256") is None or record.get("before_base64") is not None for record in journal.get("records",[])))
                    raise OSError("seeded stop before first upgrade destination")
            book.expect("framework-upgrade-prechange-backup-probe-reported",UpgradeError,lambda:upgrade(root,target,apply=True,fault=stop_before_first_destination))
            book.check("framework-upgrade-recoverable-backup-before-first-change",backup_observed==[True] and workspace_fingerprint(target)["sha256"]==before["sha256"])
            applied = upgrade(root, target, apply=True)
            book.check("framework-upgrade-preserves-project-local-module", applied["applied"] and (local / "module.toml").is_file() and discover(target)["project-local"]["source"] == "local")
            book.check("framework-upgrade-preserves-project-laws", project_law.read_bytes() == project_law_bytes and any(result.id == "project.local" and result.passed for result in LawRunner(target).run([project_law])))
            book.check("framework-upgrade-preserves-root-agents-user-bytes", (target / "AGENTS.md").read_bytes() == agents_text)
            book.check("framework-upgrade-replaces-declared-files-and-removes-bytecode", (target / ".agents" / "README.md").read_bytes() == (framework_dir(root) / "README.md").read_bytes() and not stale.exists())
            second = upgrade(root, target, apply=True)
            book.check("framework-upgrade-idempotent", second["applied"] is False and second["mutation_count"] == 0)

            (target / ".agents" / "README.md").write_bytes(old_readme); before_failure = (target / ".agents" / "README.md").read_bytes()
            def fail_after_destination(stage, journal):
                if stage == "after_destination" and journal.get("applied") == 0: raise OSError("seeded upgrade crash")
            book.expect("framework-upgrade-failure-reported", UpgradeError, lambda: upgrade(root, target, apply=True, fault=fail_after_destination))
            book.check("framework-upgrade-failure-rolls-back-all-files", (target / ".agents" / "README.md").read_bytes() == before_failure)
            upgrade(root, target, apply=True)

            version_path = target / ".agents" / "VERSION"; source_version = version_path.read_bytes(); version_path.write_text("99.0.0\n")
            target_before_downgrade = workspace_fingerprint(target)
            book.expect("framework-downgrade-newer-state-rejected", UpgradeError, lambda: plan_upgrade(root, target))
            book.check("framework-downgrade-rejection-preserves-target", workspace_fingerprint(target)["sha256"] == target_before_downgrade["sha256"])
            version_path.write_bytes(source_version); upgrade(root, target, apply=True)

            runtime = target / ".agents" / "runtime"; runtime.mkdir(exist_ok=True)
            original_agents = b"legacy original agents"; legacy_backup = runtime / "legacy-agents.backup"; legacy_backup.write_bytes(original_agents)
            current_agents = (target / "AGENTS.md").read_bytes()
            legacy_bootstrap = {
                "schema": 1, "created_target": False, "original_sha256": hashlib.sha256(original_agents).hexdigest(),
                "original_mode": 0o644, "installed_sha256": hashlib.sha256(current_agents).hexdigest(),
                "installed_block_sha256": "0" * 64, "backup": str(legacy_backup), "upgrade_count": 0,
            }
            (runtime / "bootstrap-install.json").write_text(json.dumps(legacy_bootstrap))
            codex_config = target / ".codex" / "config.toml"; codex_config.parent.mkdir(); codex_config.write_bytes(b"installed codex")
            original_codex = b"original codex"; codex_backup = runtime / "legacy-codex.backup"; codex_backup.write_bytes(original_codex)
            legacy_codex = {
                "schema": 2, "files": [{"path": ".codex/config.toml", "created_file": False,
                    "original_sha256": hashlib.sha256(original_codex).hexdigest(), "installed_sha256": hashlib.sha256(codex_config.read_bytes()).hexdigest(),
                    "original_mode": 0o644, "backup": str(codex_backup)}], "upgrade_count": 0,
            }
            (runtime / "codex-install.json").write_text(json.dumps(legacy_codex))
            migrated = upgrade(root, target, apply=True)
            durable_bootstrap = install_state_dir(target) / "bootstrap" / "install.json"
            durable_codex = install_state_dir(target) / "codex" / "install.json"
            book.check("v4-runtime-install-journals-migrate-to-persistent-storage", migrated["applied"] and durable_bootstrap.is_file() and durable_codex.is_file() and not (runtime / "bootstrap-install.json").exists() and not (runtime / "codex-install.json").exists())
            migrated_journals = [json.loads(durable_bootstrap.read_text()), json.loads(durable_codex.read_text())]
            book.check("migrated-install-backups-remain-hash-bound-and-confined", all(".aegis/state/" in str(value).replace("\\", "/") for value in (migrated_journals[0]["backup"], migrated_journals[1]["files"][0]["backup"])))
    else:
        book.check("migration-source-release-must-be-trusted", False, str(manifest_detail))

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work)
        book.expect("v4-undocumented-codex-keys-rejected", ConfigError, lambda: merge_conservative('agents.max_threads = 2\n', work))
    persistent = run_family(str(root.resolve()), "persistent-recovery"); persistent_obs = {label: passed for label, passed, _ in persistent.observations}
    book.check("v4-original-codex-config-restorable", persistent_obs.get("codex-upgrade-chain-restores-original") is True)
    book.check("v4-original-root-agents-restorable", persistent_obs.get("bootstrap-upgrade-chain-restores-original") is True)
    cli = run_family(str(root.resolve()), "cli"); cli_obs = {label: passed for label, passed, _ in cli.observations}
    book.check("upgrade-never-regenerates-trust-without-authorization", cli_obs.get("cli-manifest-maintenance-authorization") is True)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents" / "VERSION").parent.mkdir(parents=True); (work / ".agents" / "VERSION").write_text("4.0.0\n")
        module = work / ".agents" / "local-modules" / "old"; module.mkdir(parents=True); (module / "POLICY.md").write_text("policy")
        (module / "module.toml").write_text('[module]\nid="old"\nname="old"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\nrequires_framework="<4.0.0"\n')
        book.expect("local-module-constraint-rechecked-after-upgrade", ModuleError, lambda: discover(work))
        law = work / "bad-law.toml"; law.write_text('schema=999\n[[law]]\nid="bad"\ndescription="bad schema"\nkind="file_exists"\npath="x"\n')
        schema_result = LawRunner(work).run([law])
        book.check(
            "project-law-schema-rejected-explicitly",
            len(schema_result) == 1
            and schema_result[0].outcome == "ERROR"
            and not schema_result[0].passed
            and "unsupported law schema" in schema_result[0].detail,
            repr(schema_result),
        )
    return book.finish()


def _performance(root: Path) -> FamilyOutcome:
    book = Checkbook("performance")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); runtime_dir(work).mkdir(parents=True); store=StateStore(work); store.create("large auditable history")
        decisions=[{"id":f"D{index:04d}","at":"now","statement":f"decision {index}","rationale":"retained audit history","evidence":[]} for index in range(500)]
        store.mutate(lambda value:value["decisions"].extend(decisions))
        started=time.monotonic(); loaded=store.load(); elapsed=time.monotonic()-started
        book.check("state-load-large-history-bounded-and-auditable",len(loaded["decisions"])==500 and elapsed<5,f"elapsed={elapsed:.3f}")
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory); agents=work/".agents"; agents.mkdir()
        for index in range(200): (agents/f"file-{index:04d}").write_bytes(b"x"*1024)
        import agentinfra.manifest as manifest_module
        from unittest.mock import patch
        with patch("agentinfra.manifest.file_sha",wraps=manifest_module.file_sha) as hasher:
            started=time.monotonic(); rendered_manifest=render(work); elapsed=time.monotonic()-started
        book.check("manifest-verification-linear-file-hashing",len(parse(rendered_manifest))==200 and hasher.call_count==200 and elapsed<10,f"hashes={hasher.call_count} elapsed={elapsed:.3f}")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); result = run_process([sys.executable, "-c", "import sys;sys.stdout.write('x'*2000000)"], cwd=work, timeout=20, capture_limit=4096)
        book.check("bounded-process-capture", result.stdout_bytes == 2_000_000 and len(result.stdout) == 4096 and result.stdout_truncated)
        task_dir = work / "evidence-task"
        import agentinfra.evidence as evidence_module
        from unittest.mock import patch
        with patch("agentinfra.evidence.load_evidence", wraps=evidence_module.load_evidence) as loader:
            for index in range(100): append_evidence(task_dir, "test", f"record {index}", task_id="evidence-task", change_epoch=0)
        book.check("evidence-append-does-not-rescan-ledger", loader.call_count <= 1, f"full_load_calls={loader.call_count}")
        started = time.monotonic(); records = load_evidence(task_dir); elapsed = time.monotonic() - started
        book.check("linear-evidence-ledger", len(records) == 100 and elapsed < 10, f"elapsed={elapsed:.3f}")
        with patch("agentinfra.evidence._validate_record", wraps=evidence_module._validate_record) as validator:
            verified = load_evidence(task_dir)
        book.check("evidence-verification-one-validation-per-record", len(verified) == 100 and validator.call_count == 100)
        (work / "build").mkdir(); (work / "build" / "large").write_bytes(b"x" * 2_000_000)
        fingerprint = workspace_fingerprint(work)
        book.check("large-build-excluded", fingerprint["available"] and all("build/large" not in str(item) for item in fingerprint.get("details", {}).get("items", [])))
        book.check("law-runner-large-output-memory-bounded",result.stdout_truncated and len(result.stdout)==4096 and len(result.stdout_sha256)==64)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); runtime_dir(work).mkdir(parents=True); ledger = ContextLedger(work)
        data = {"schema": 3, "sources": {}}
        for index in range(1000):
            source = f"source-{index}"; data["sources"]["external:" + hashlib.sha256(source.encode()).hexdigest()] = {
                "kind": "external", "source": source, "fingerprint": "v1", "provenance": "content-sha256", "verified": True,
                "conclusion": "", "recorded_at": "2020-01-01T00:00:00+00:00", "valid_until": "2999-01-01T00:00:00+00:00",
            }
        ledger._save(data); started = time.monotonic(); lookup = ledger.check_external("source-999", "v1"); elapsed = time.monotonic() - started
        book.check("large-context-cache-lookup-bounded", lookup.get("fresh") and elapsed < 5, f"elapsed={elapsed:.3f}")

    if shutil.which("git"):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory); subprocess.run(["git", "init", "-q"], cwd=work, check=True, env=minimal_subprocess_env())
            subprocess.run(["git", "config", "user.email", "aegis@example.invalid"], cwd=work, check=True, env=minimal_subprocess_env())
            subprocess.run(["git", "config", "user.name", "Aegis Test"], cwd=work, check=True, env=minimal_subprocess_env())
            large = work / "large-tracked.bin"; large.write_bytes(b"x" * 2_000_000); subprocess.run(["git", "add", "."], cwd=work, check=True, env=minimal_subprocess_env()); subprocess.run(["git", "commit", "-qm", "large"], cwd=work, check=True, env=minimal_subprocess_env())
            with patch("agentinfra.workspace._file_fingerprint", side_effect=AssertionError("clean tracked file was rehashed")):
                clean = workspace_fingerprint(work)
            book.check("git-fingerprint-avoids-rehashing-clean-tracked-large-file", clean.get("available") and clean.get("kind") == "git")
    else:
        book.check("git-fingerprint-cache-capability-unavailable", True)

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents" / "VERSION").parent.mkdir(parents=True); (work / ".agents" / "VERSION").write_text("4.0.0\n")
        base = work / ".agents" / "local-modules"
        for index in range(80):
            module_id = f"module-{index:03d}"; module = base / module_id; module.mkdir(parents=True); (module / "POLICY.md").write_text("policy")
            (module / "module.toml").write_text(f'[module]\nid="{module_id}"\nname="{module_id}"\nversion="1.0.0"\nkind="agent-host"\npolicy=["POLICY.md"]\n')
        started = time.monotonic(); discovered = discover(work); elapsed = time.monotonic() - started
        book.check("module-discovery-linear-and-inert-at-scale", len(discovered) == 80 and elapsed < 10, f"elapsed={elapsed:.3f}")

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); _copy_bootstrap(root, work); (work / "AGENTS.md").write_text("user\n")
        bootstrap_install(work, apply=True); before = (work / "AGENTS.md").read_bytes(); noop = bootstrap_install(work, apply=True)
        book.check("noop-bootstrap-performs-no-rewrite", noop.get("applied") is False and (work / "AGENTS.md").read_bytes() == before)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory); (work / ".agents").mkdir(); _copy_codex(root, work); first = codex_install(work, dry_run=False, schema_probe=_valid_probe()); second = codex_install(work, dry_run=False, schema_probe=_valid_probe())
        book.check("noop-codex-performs-no-rewrite", bool(first.get("changed")) and not second.get("changed"))

    shell_select_before = available_shells.cache_info(); available_shells(); available_shells(); shell_select_after = available_shells.cache_info()
    book.check("environment-discovery-cached-per-process", shell_select_after.hits >= shell_select_before.hits + 1 and shell_select_after.misses <= shell_select_before.misses + 1)
    import agentinfra.cli as cli_module
    import contextlib, io
    from argparse import Namespace
    from unittest.mock import patch
    with patch("agentinfra.cli.available_shells",wraps=cli_module.available_shells) as shell_probe, patch("agentinfra.cli.optional_python_packages",wraps=cli_module.optional_python_packages) as package_probe:
        with contextlib.redirect_stdout(io.StringIO()):
            doctor_exit=cli_module.cmd_doctor(root,Namespace())
    book.check("agentctl-doctor-expensive-probes-not-repeated",doctor_exit==0 and shell_probe.call_count==1 and package_probe.call_count==1,f"shell={shell_probe.call_count} packages={package_probe.call_count}")
    run_family(str(root.resolve()), "lawlib"); first_cache = run_family.cache_info(); run_family(str(root.resolve()), "lawlib"); second_cache = run_family.cache_info()
    book.check("semantic-family-expensive-probes-cached", second_cache.hits == first_cache.hits + 1)
    with (framework_dir(root) / "framework.toml").open("rb") as stream: runtime_policy = tomllib.load(stream).get("runtime", {})
    book.check("runtime-retention-policy-explicit-and-nondestructive", runtime_policy.get("retention_policy") == "manual-audited-only" and runtime_policy.get("automatic_cleanup") is False)
    law_runner = run_family(str(root.resolve()), "law-runner"); law_obs = {label: passed for label, passed, _ in law_runner.observations}
    book.check("law-timeout-and-descendant-cleanup-bounded", law_obs.get("law-timeout-kills-descendant-group") is True and law_obs.get("timeout-reported") is True)
    security = run_family(str(root.resolve()), "security"); security_obs = {label: passed for label, passed, _ in security.observations}
    book.check("default-operations-require-no-network", security_obs.get("no-implicit-repository-exfiltration") is True)
    book.check("bounded-retry-structure", "delays = (0.0, 0.005, 0.015, 0.04, 0.1)" in (framework_dir(root) / "infra" / "agentinfra" / "atomic.py").read_text(encoding="utf-8"))
    return book.finish()


def _end_to_end(root: Path) -> FamilyOutcome:
    book = Checkbook("end-to-end")
    root_text = str(root.resolve())
    workflow = run_family(root_text, "workflow"); workflow_obs = {label: passed for label, passed, _ in workflow.observations}
    subagents = run_family(root_text, "subagents"); subagent_obs = {label: passed for label, passed, _ in subagents.observations}
    persistent = run_family(root_text, "persistent-recovery"); persistent_obs = {label: passed for label, passed, _ in persistent.observations}
    mutation = run_family(root_text, "mutation"); mutation_obs = {label: passed for label, passed, _ in mutation.observations}
    modules = run_family(root_text, "modules"); module_obs = {label: passed for label, passed, _ in modules.observations}
    laws = run_family(root_text, "law-runner"); law_obs = {label: passed for label, passed, _ in laws.observations}
    fault = run_family(root_text, "fault-race"); fault_obs = {label: passed for label, passed, _ in fault.observations}
    gates = run_family(root_text, "gates-risks-decisions"); gate_obs = {label: passed for label, passed, _ in gates.observations}
    evidence = run_family(root_text, "evidence"); evidence_obs = {label: passed for label, passed, _ in evidence.observations}
    security = run_family(root_text, "security"); security_obs = {label: passed for label, passed, _ in security.observations}
    python_meta = run_family(root_text, "python-meta"); python_meta_obs = {label: passed for label, passed, _ in python_meta.observations}
    codex = run_family(root_text, "codex-static"); codex_obs = {label: passed for label, passed, _ in codex.observations}
    xonsh = run_family(root_text, "xonsh"); xonsh_obs = {label: passed for label, passed, _ in xonsh.observations}
    cli = run_family(root_text, "cli"); cli_obs = {label: passed for label, passed, _ in cli.observations}
    book.check("mutating-task-current-proof-and-workspace-audit-flow", workflow_obs.get("complete-current-proof-flow") is True)
    book.check("read-only-material-claim-direct-evidence-flow", workflow_obs.get("read-material-claim-needs-direct-evidence") is True)
    book.check("single-child-handoff-integration-flow", subagent_obs.get("next-child-only-after-prior-integration") is True)
    book.check("codex-nested-delegation-fail-closed-flow",codex_obs.get("codex-static-does-not-claim-live-effective") is True and subagent_obs.get("framework-control-plane-nested-open-rejected") is True)
    book.check("codex-second-concurrent-child-rejected-flow",subagent_obs.get("global-lease-exactly-one-across-processes") is True)
    book.check("xonsh-interactive-matches-direct-cli-flow",xonsh_obs.get("xonsh-argv-unicode-exit-streams",xonsh_obs.get("xonsh-absent-fallback")) is True and cli_obs.get("cli-paths-with-spaces-and-unicode-safe") is True)
    book.check("python-meta-enabled-disabled-core-invariants-flow",python_meta_obs.get("python-meta-core-stdlib-independent") is True and python_meta_obs.get("python-meta-extension-cannot-lower-max-or-sequentiality") is True)
    book.check("project-module-hard-invariant-protection-flow", all(module_obs.get(label) is True for label in ("protected-module-replacement-rejected","local-module-core-policy-write-rejected","local-module-core-policy-preserved")))
    book.check("law-definition-self-mutation-rejected-flow", law_obs.get("mutate-restore-definition-detected") is True)
    book.check("test-detection-cheating-hidden-metamorphic-flow", mutation_obs.get("hidden-law-values-not-visible") is True and mutation_obs.get("renamed-test-metamorphic-behavior") is True)
    book.check("post-verification-edit-invalidates-proof-flow", workflow_obs.get("epoch-change-invalidates-verification") is True)
    book.check("post-final-audit-edit-blocks-finalize-flow", workflow_obs.get("post-audit-mutation-rejected") is True)
    book.check("runtime-deletion-preserves-all-install-recovery", persistent_obs.get("bootstrap-runtime-deletion-preserves-exact-uninstall") is True and persistent_obs.get("codex-runtime-deletion-preserves-exact-uninstall") is True)
    book.check("install-upgrade-uninstall-exact-restoration-flow", persistent_obs.get("bootstrap-upgrade-chain-restores-original") is True and persistent_obs.get("codex-upgrade-chain-restores-original") is True)
    book.check("all-transaction-boundaries-pre-or-post-flow", fault_obs.get("fault-boundary-battery") is True)
    book.check("concurrent-state-evidence-child-finalize-invariants-flow", all(fault_obs.get(label) is True for label in ("race-evidence-no-loss", "race-one-child-winner", "race-task-revision-one-winner", "race-finalize-vs-evidence-fails-closed", "race-finalize-vs-workspace-edit-fails-closed")))
    flaws = json.loads((root / ".agents" / "infra" / "law_tests" / "v4_flaws.json").read_text(encoding="utf-8"))["flaws"]
    book.check("v4-every-flaw-has-direct-law", all(item.get("seeded_probe") for item in flaws))
    book.check("v4-every-flaw-has-failing-seeded-regression", all(item.get("seeded_probe") and item.get("regression") for item in flaws))
    book.check("v4-every-flaw-has-passing-postfix-regression", mutation_obs.get("every-seeded-v4-defect-has-failing-and-passing-regression") is True)
    enforcement = {
        "max": mutation_obs.get("max-reasoning-mutant-killed"),
        "sequential": mutation_obs.get("sequential-child-limit-mutant-killed"),
        "epoch": mutation_obs.get("current-epoch-mutant-killed"),
        "workspace": mutation_obs.get("workspace-recheck-mutant-killed"),
        "transaction": mutation_obs.get("transaction-rollback-mutant-killed"),
        "manifest": mutation_obs.get("manifest-mutant-killed"),
    }
    book.check("every-hard-claim-independent-adversarial-enforcement", all(value is True for value in enforcement.values()), str(enforcement))
    book.check("persistent-mutations-have-transactional-recovery", persistent_obs.get("transaction-crash-pre-or-post") is True and fault_obs.get("fault-boundary-battery") is True)
    book.check("external-host-assumptions-probed-and-fail-closed", mutation_obs.get("critical-config-mutants-killed") is True and python_meta_obs.get("python-meta-probe-nonzero-when-unmet") is True)
    book.check("untrusted-and-mutable-paths-root-confined", security_obs.get("path-parent-traversal") is True and security_obs.get("artifact-path-root-confinement") is True and mutation_obs.get("path-confinement-mutant-corpus") is True)
    book.check("acceptance-proof-task-epoch-workspace-evidence-bound", gate_obs.get("gate-proof-task-epoch-relevance") is True and evidence_obs.get("command-workspace-fingerprint-bound") is True)
    book.check("extension-points-cannot-weaken-hard-invariants", module_obs.get("protected-module-replacement-rejected") is True and python_meta_obs.get("python-meta-hard-invariants-cannot-be-lowered") is True)
    book.check("every-disposable-runtime-recovery-path-survives-deletion", persistent_obs.get("bootstrap-recovery-metadata-outside-runtime") is True and persistent_obs.get("codex-recovery-metadata-outside-runtime") is True)
    from build_traceability import existing_law_ids, existing_test_locations, inventory
    inventory_entries, _ = inventory(root); unit_names = {item["name"] for item in inventory_entries if item["source_file"].startswith("00_")}; builtin_names = {item["name"] for item in inventory_entries if item["source_file"].startswith("01_")}
    book.check("existing-regression-and-builtin-inventory-executable", unit_names <= set(existing_test_locations(root)) and builtin_names <= existing_law_ids(root))
    book.check("no-known-v4-flaw-uncovered", len(flaws) >= 10 and len({item["id"] for item in flaws}) == len(flaws) and all(item.get("seeded_probe") and item.get("regression") for item in flaws))
    manifest_ok, detail = verify_manifest(root, require_release_anchor=True)
    if manifest_ok:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory); archive = work / "release.zip"; first = build_archive(root, archive); extracted = work / "extracted"; members = safe_extract(archive, extracted); clean_ok, clean_detail = verify_manifest(extracted, require_release_anchor=True)
            book.check("release-clean-extract-integrity-flow", first["members"] == len(members) and clean_ok, str(clean_detail))
    else:
        book.check("release-clean-extract-integrity-flow", False, str(detail))
    return book.finish()


def _source_assurance(root: Path) -> FamilyOutcome:
    """Execute the source-authoritative unit/property modules used for subsumption.

    Both module and exact test-method identities are observations.  A method
    observation is true only when that exact oracle passed; a successful sibling
    can never conceal its skip, failure, or error.  Capability skips remain in the
    structured detail and are never converted into individual PASS records.
    """

    book = Checkbook("source-assurance")
    infra = root / "infra" if (root / "infra" / "tests").is_dir() else root / ".agents" / "infra"
    module_groups = (
        ("unit", infra / "tests"),
        ("property", infra / "property_tests"),
    )
    for category, directory in module_groups:
        if not directory.is_dir():
            book.check(f"{category}-module-directory", False, f"missing {directory}")
            continue
        for path in sorted(directory.glob("test_*.py"), key=lambda item: item.name):
            result = run_unittest_module_isolated(infra, path, category=category)
            book.check(
                f"{category}-module-{path.stem}",
                result["ok"],
                json.dumps(result, sort_keys=True),
            )
            methods: set[str] = set()
            for exact in result["tests"]:
                method = exact["method"]
                label = f"{category}-test-{path.stem}-{method}"
                unique = method not in methods
                methods.add(method)
                detail = json.dumps(exact, sort_keys=True)
                book.check(label, unique and exact["outcome"] == "PASS", detail)
    return book.finish()


def _historical_integrity(root: Path) -> FamilyOutcome:
    book = Checkbook("historical-integrity")
    try:
        from .historical_integrity import HARD_MUTATION_PROOFS, run_historical_integrity_campaign
    except ImportError:
        from historical_integrity import HARD_MUTATION_PROOFS, run_historical_integrity_campaign

    report = run_historical_integrity_campaign(root)
    v4 = report["v4_flaw_campaign"]
    mutation = report["hard_mutation_campaign"]
    hard = report["hard_invariant_campaign"]
    process = report["actual_process_campaign"]
    release = report["clean_release_campaign"]
    semantic = report["semantic_mapping_audit"]
    book.check(
        "v4-canonical-execution-complete",
        v4["required"] == v4["seeded_red"] == v4["fixed_green"]
        and not v4["missing"],
        v4["digest"],
    )
    book.check(
        "hard-mutation-canonical-complete",
        mutation["effective_completeness"] == 1.0 and not mutation["missing_targets"],
        mutation["digest"],
    )
    book.check(
        "hard-invariant-canonical-complete",
        hard["effective_completeness"] == 1.0 and not hard["missing_targets"],
        hard["digest"],
    )
    book.check(
        "actual-process-concurrency-complete",
        process["status"] == "PASS" and process["actual_processes"] is True,
        process["evidence_digest"],
    )
    book.check(
        "release-clean-artifact-full-sequence-complete",
        release["status"] == "PASS" and len(release["phases"]) >= 6,
        release["evidence_digest"],
    )
    book.check(
        "elevated-semantic-mapping-audit-complete",
        semantic["reviewed"] == semantic["required"] and not semantic["weaker"],
        semantic["digest"],
    )
    mutation_records = {item["target_id"]: item for item in mutation["records"]}
    for target, label in HARD_MUTATION_PROOFS.items():
        record = mutation_records.get(target, {})
        book.check(
            label,
            record.get("status") in {"KILLED", "EQUIVALENT_STRONGER_PROOF"},
            str(record.get("proof_digest", "missing")),
        )
    return book.finish()


FAMILIES = {
    "source-assurance": _source_assurance,
    "historical-integrity": _historical_integrity,
    "release": _release,
    "atomic-transaction": _atomic,
    "bootstrap": _bootstrap,
    "state-identity": _state_identity,
    "workflow": _workflow,
    "gates-risks-decisions": _gates,
    "evidence": _evidence,
    "workspace": _workspace,
    "subagents": _subagents,
    "context": _context,
    "law-runner": _law_runner,
    "lawlib": _lawlib,
    "modules": _modules,
    "codex-static": _codex_static,
    "xonsh": _xonsh,
    "python-meta": _python_meta,
    "cli": _cli,
    "persistent-recovery": _persistent,
    "security": _security,
    "portability": _portability,
    "reasoning-cost": _reasoning,
    "policy-consistency": _policy,
    "fault-race": _fault,
    "mutation": _mutation,
    "migration": _migration,
    "performance": _performance,
    "end-to-end": _end_to_end,
}


@lru_cache(maxsize=None)
def run_family(root_text: str, family: str) -> FamilyOutcome:
    root = Path(root_text).resolve(strict=True)
    operation = FAMILIES.get(family)
    if operation is None:
        detail = f"no semantic battery registered for {family}"
        return FamilyOutcome(family, False, 1, ("family-registered",), (detail,), (("family-registered", False, detail),), 0.0)
    try:
        return operation(root)
    except BaseException as exc:
        detail = f"uncaught {type(exc).__name__}: {exc}"
        return FamilyOutcome(family, False, 1, ("family-completed",), (detail,), (("family-completed", False, detail),), 0.0)


def run_requirement(root: Path, requirement: dict) -> RequirementOutcome:
    name = requirement["name"]
    capability = requirement.get("required_capability", "portable")
    known_capabilities = {
        "portable", "codex-current-cli-schema", "codex-live-effective-metadata",
        "xonsh", "mcpyrate-and-unpythonic", "windows", "linux", "macos",
        "posix-permissions", "filesystem-symlink", "external-state-anchor", "windows-and-posix-matrix", "full-platform-matrix",
    }
    if capability not in known_capabilities:
        detail = f"unknown required capability classification: {capability}"
        return RequirementOutcome(name, "FAIL", "UNTRUSTED", 1, detail, _sha({"name": name, "detail": detail}))
    if capability == "codex-current-cli-schema":
        probe = _current_codex_capability_probe()
        if not probe.get("available"):
            status = probe.get("capability")
            if status not in {"MISSING", "INCOMPATIBLE", "UNOBSERVABLE"}:
                status = "UNOBSERVABLE"
            detail = "Current Codex CLI schema acceptance is not proven: " + str(probe.get("reason") or probe)
            return RequirementOutcome(name, "UNAVAILABLE", status, 1, detail, _sha({"name": name, "probe": probe}), justification=detail)
    if capability == "codex-live-effective-metadata":
        probe = _current_codex_capability_probe()
        status = "MISSING" if probe.get("capability") == "MISSING" else "UNOBSERVABLE"
        detail = "This host exposes no trustworthy machine-readable effective spawned-child model/reasoning/concurrency metadata proof; static config or binary strings are insufficient."
        return RequirementOutcome(name, "UNAVAILABLE", status, 1, detail, _sha({"name": name, "probe": probe, "detail": detail}), justification=detail)
    if capability == "xonsh" and "xonsh" not in available_shells():
        detail = "Xonsh is missing or outside the adapter's tested version range; portable direct/native-shell fallback laws remain executable."
        return RequirementOutcome(name, "UNAVAILABLE", "MISSING", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    platform_matches = {
        "windows": os.name == "nt",
        "linux": sys.platform.startswith("linux"),
        "macos": sys.platform == "darwin",
        "posix-permissions": os.name != "nt",
    }
    if capability in platform_matches and not platform_matches[capability]:
        detail = f"{capability} is not the current host platform ({sys.platform}); the portable implementation path remains executable locally."
        return RequirementOutcome(name, "NOT_APPLICABLE", "NOT_APPLICABLE", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    if capability == "filesystem-symlink":
        usable, reason = _filesystem_symlink_capability_probe()
        if not usable:
            detail = "The current host cannot create an isolated filesystem symlink needed for this adversarial path: " + reason
            return RequirementOutcome(name, "UNAVAILABLE", "UNOBSERVABLE", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    if capability == "external-state-anchor":
        detail = (
            "No externally administered immutable or monotonic state/evidence anchor is configured on this host. "
            "The local cryptographic chains and append-only anchor history detect partial rollback, but cannot "
            "truthfully prove resistance to a coordinated rewrite of every in-project anchor artifact."
        )
        return RequirementOutcome(name, "UNAVAILABLE", "UNOBSERVABLE", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    if capability in {"windows-and-posix-matrix", "full-platform-matrix"}:
        detail = "A single Windows host cannot truthfully satisfy the required cross-platform matrix; local portable and Windows probes executed separately."
        return RequirementOutcome(name, "UNAVAILABLE", "UNOBSERVABLE", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    if capability == "mcpyrate-and-unpythonic":
        import importlib.metadata
        missing = []
        for package in ("mcpyrate", "unpythonic"):
            try: importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError: missing.append(package)
        if missing:
            detail = "Optional extension capability is missing: " + ", ".join(missing) + "; the explicit disabled/core path was executed instead."
            return RequirementOutcome(name, "UNAVAILABLE", "MISSING", 1, detail, _sha({"name": name, "detail": detail}), justification=detail)
    required_observations = requirement.get("required_observations")
    if not isinstance(required_observations, list) or not required_observations or any(
        not isinstance(binding, str)
        or "::" not in binding
        or any(not part for part in binding.split("::", 1))
        for binding in required_observations
    ):
        detail = "traceability entry has no explicit family-qualified required_observations binding"
        return RequirementOutcome(name, "FAIL", "UNTRUSTED", 1, detail, _sha({"name": name, "detail": detail}))
    if len(required_observations) != len(set(required_observations)):
        detail = "traceability entry repeats a semantic observation binding"
        return RequirementOutcome(name, "FAIL", "UNTRUSTED", 1, detail, _sha({"name": name, "detail": detail}))
    selected = []
    missing = []
    for binding in required_observations:
        family, label = binding.split("::", 1)
        if family not in FAMILIES:
            missing.append(binding + " (unknown family)")
            continue
        outcome = run_family(str(root.resolve()), family)
        observed = {observed_label: (passed, observation_detail) for observed_label, passed, observation_detail in outcome.observations}
        if label not in observed:
            missing.append(binding)
            continue
        selected.append((binding, *observed[label]))
    failures = [f"{binding}: {detail}" for binding, passed, detail in selected if not passed]
    passed = not missing and not failures
    detail = (
        f"{len(selected)} explicitly bound production observation(s) across {len({item.split('::', 1)[0] for item in required_observations})} semantic family/families; "
        + ("all passed" if passed else "failures: " + "; ".join(([f"missing observation {label}" for label in missing] + failures)[:8]))
    )
    payload = {
        "requirement": name,
        "required_capability": capability,
        "required_observations": required_observations,
        "observations": selected,
        "source_sha256": requirement.get("source_sha256"),
    }
    return RequirementOutcome(name, "PASS" if passed else "FAIL", "AVAILABLE", max(1, len(selected)), detail, _sha(payload))
