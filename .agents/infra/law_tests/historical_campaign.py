from __future__ import annotations

"""Disposable execution-backed campaigns for historical universal laws."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable
from unittest.mock import patch

from agentinfra.process import run_process
from agentinfra.release_source import build_deployment_tree
from agentinfra.security import minimal_subprocess_env


class CampaignError(RuntimeError):
    pass


def _replacement(path: str, old: str, new: str) -> dict:
    return {"path": path, "old": old, "new": new}


V4_MUTANTS: dict[str, tuple[dict, ...]] = {
    "V4-001": (
        _replacement(
            "infra/agentinfra/codex_config.py",
            "def _sha_path(path:Path):return _sha_bytes(path.read_bytes()) if path.exists() else None",
            "def _sha_path(path:Path):return _sha_bytes(path.read_text(encoding='utf-8').encode('utf-8')) if path.exists() else None",
        ),
    ),
    "V4-002": (
        _replacement(
            "infra/agentinfra/paths.py",
            "def persistent_dir(root: Path) -> Path: return aegis_dir(root) / \"state\"",
            "def persistent_dir(root: Path) -> Path: return runtime_dir(root)",
        ),
    ),
    "V4-003": (
        _replacement(
            "infra/agentinfra/state_store.py",
            """def validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("task id must be 1-64 canonical lowercase letters/digits-hyphens")
    if task_id in {".", ".."}:
        raise ValueError("dot task ids are forbidden")
    return task_id""".replace("digits-hyphens", "digits/hyphens"),
            """def validate_task_id(task_id: str) -> str:
    return task_id""",
        ),
    ),
    "V4-004": (
        _replacement(
            "infra/agentinfra/evidence.py",
            "if provenance != \"manual\" and _provenance_token is not _FRAMEWORK_PROVENANCE_TOKEN:",
            "if False:",
        ),
    ),
    "V4-005": (
        _replacement(
            "infra/agentinfra/laws.py",
            "return [LawResult(\"framework.laws.nonempty\", False, \"acceptance law collection is empty\", outcome=\"ERROR\", oracle_count=1)]",
            "return [LawResult(\"framework.laws.nonempty\", True, \"vacuous mutant\", outcome=\"PASS\", oracle_count=1)]",
        ),
    ),
    "V4-006": (
        _replacement(
            "infra/agentinfra/process.py",
            """def _kill_tree(proc: subprocess.Popen, job=None) -> None:
    if proc.poll() is not None:
        if job is not None and os.name == "nt":
            ctypes.windll.kernel32.TerminateJobObject(job, 1)
        return
    if os.name == "nt":
        if job is not None and ctypes.windll.kernel32.TerminateJobObject(job, 1):
            return
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=minimal_subprocess_env(),
        )
        if proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass""",
            """def _kill_tree(proc: subprocess.Popen, job=None) -> None:
    if proc.poll() is None:
        proc.kill()""",
        ),
        _replacement(
            "infra/agentinfra/process.py",
            "job = _assign_kill_job(proc)",
            "job = None  # seeded descendant-lifetime mutant",
        ),
    ),
    "V4-007": (
        _replacement(
            "infra/agentinfra/modules.py",
            "if script.is_absolute() or \"..\" in script.parts:",
            "if False:",
        ),
        _replacement(
            "infra/agentinfra/modules.py",
            "target = confined_path(info[\"root\"], script, must_exist=True, reject_symlinks=True)",
            "target = (module_root / script).resolve(strict=True)",
        ),
        _replacement(
            "infra/agentinfra/modules.py",
            """            except ValueError as exc:
                raise ModuleError(f"module action script escapes its module: {argv[1]}") from exc""",
            """            except ValueError:
                pass""",
        ),
    ),
    "V4-008": (
        _replacement(
            "infra/agentinfra/codex_config.py",
            '"live_effective":{"outcome":"UNAVAILABLE","capability_status":"UNOBSERVABLE","detail":"Codex executable/config schema inspection cannot expose effective spawned-child model, reasoning, depth, or live concurrency metadata."}',
            '"live_effective":{"outcome":"PASS","capability_status":"AVAILABLE","detail":"seeded static-equals-live mutant"}',
        ),
    ),
    "V4-009": (
        _replacement(
            "infra/agentinfra/manifest.py",
            "if require_release_anchor or anchor_path.exists():",
            "if False:",
        ),
    ),
    "V4-010": (
        _replacement(
            "infra/agentinfra/context_cache.py",
            "or ttl_seconds < 0",
            "or False  # seeded negative-TTL mutant",
        ),
    ),
    "V4-011": (
        _replacement(
            "infra/agentinfra/locks.py",
            "for key, expected in checks.items():",
            "for key, expected in ():",
        ),
    ),
    "V4-012": (
        _replacement(
            "infra/agentinfra/workspace.py",
            """    if result.returncode != 0:
        return {
            "schema": 2,
            "available": False,
            "kind": "git",
            "fallback_allowed": False,""",
            """    if result.returncode != 0:
        return {
            "schema": 2,
            "available": False,
            "kind": "git",
            "fallback_allowed": True,""",
        ),
    ),
    "V4-013": (
        _replacement(
            "infra/agentinfra/transaction.py",
            """    _preflight_recovery_batch([plan])
    return _apply_recovery(plan, root=root)""",
            """    result = _apply_recovery(plan, root=root)
    _preflight_recovery_batch([plan])
    return result""",
        ),
    ),
    "V4-014": (
        _replacement(
            "infra/agentinfra/state_store.py",
            """    def _validate_anchor_history(self, task_id: str, current: dict) -> None:
        directory = self._anchor_history_dir(task_id, create=False)""",
            """    def _validate_anchor_history(self, task_id: str, current: dict) -> None:
        return  # seeded coordinated-rollback mutant""",
        ),
    ),
    "V4-015": (
        _replacement(
            "infra/agentinfra/evidence.py",
            "if provenance != \"manual\" and _provenance_token is not _FRAMEWORK_PROVENANCE_TOKEN:",
            "if False:",
        ),
    ),
    "V4-016": (
        _replacement(
            "infra/agentinfra/modules.py",
            "if first in protected_top and not trusted_internal_state:",
            "if False:",
        ),
        _replacement(
            "infra/agentinfra/modules.py",
            """            if mutations:
                FileTransaction(
                    root,
                    mutations,
                    state_dir=persistent_dir(root) / "transactions",
                    name=f"module-{info['manifest']['module']['id']}-{action}",
                ).commit(retain=False)""",
            """            if mutations:
                for mutation in mutations:
                    mutation.path.parent.mkdir(parents=True, exist_ok=True)
                    if mutation.data is None:
                        mutation.path.unlink(missing_ok=True)
                    else:
                        mutation.path.write_bytes(mutation.data)""",
        ),
    ),
    "V4-017": (
        _replacement(
            "infra/agentinfra/state_store.py",
            """    def _control_dir(self, relative: str | Path) -> Path:
        path = confined_path(self.root, relative, reject_symlinks=True)
        path.mkdir(parents=True, exist_ok=True)
        return confined_path(self.root, path, must_exist=True, reject_symlinks=True)""",
            """    def _control_dir(self, relative: str | Path) -> Path:
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        return path""",
        ),
        _replacement(
            "infra/agentinfra/state_store.py",
            """    def _task_dir(self, task_id: str) -> Path:
        task_id = validate_task_id(task_id)
        base = self._control_dir(tasks_dir(self.root).relative_to(self.root))
        return confined_path(self.root, base / task_id, reject_symlinks=True)""",
            """    def _task_dir(self, task_id: str) -> Path:
        task_id = validate_task_id(task_id)
        return self._control_dir(tasks_dir(self.root).relative_to(self.root)) / task_id""",
        ),
        _replacement(
            "infra/agentinfra/state_store.py",
            """    def _path(self, task_id: str) -> Path:
        return confined_path(self.root, self._task_dir(task_id) / "state.json", reject_symlinks=True)""",
            '''    def _path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "state.json"''',
        ),
    ),
    "V4-018": (
        _replacement(
            "infra/agentinfra/laws.py",
            """    def __enter__(self):
        if os.name == "nt":
            try:
                self._start_windows_watchers()
            except OSError:
                self._stop_windows_watchers()""",
            """    def __enter__(self):
        if False:
            self._start_windows_watchers()""",
        ),
    ),
    "V4-019": (
        _replacement(
            "infra/law_tests/build_traceability.py",
            """def apply_ledger(requirements: list[dict], ledger_path: Path) -> None:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)""",
            """def apply_ledger(requirements: list[dict], ledger_path: Path) -> None:
    return  # seeded aggregate-ledger acceptance mutant""",
        ),
    ),
    "V4-020": (
        _replacement(
            "infra/agentinfra/transaction.py",
            """    _preflight_recovery_batch(plans)
    recovered = [_apply_recovery(plan, root=root) for plan in plans]""",
            """    recovered = []
    for plan in plans:
        _preflight_recovery_batch([plan])
        recovered.append(_apply_recovery(plan, root=root))""",
        ),
    ),
}


def _valid_probe() -> dict:
    return {
        "available": True,
        "capability": "AVAILABLE",
        "probe_kind": "historical-campaign",
        "supported_keys": [
            "model",
            "model_reasoning_effort",
            "max_concurrent_threads_per_session",
            "max_depth",
            "multi_agent_v2",
            "default_subagent_model",
            "default_subagent_reasoning_effort",
        ],
        "version": "historical-campaign",
    }


def _copy_codex(root: Path, work: Path) -> None:
    (work / ".agents" / "modules").mkdir(parents=True)
    shutil.copytree(root / ".agents" / "modules" / "codex", work / ".agents" / "modules" / "codex")
    shutil.copy2(root / ".agents" / "VERSION", work / ".agents" / "VERSION")


def _v4_001(root: Path) -> bool:
    from agentinfra.codex_config import install, uninstall

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        _copy_codex(root, work)
        config = work / ".codex" / "config.toml"
        config.parent.mkdir()
        original = b"\xef\xbb\xbfapproval_policy = \"never\"\r\n"
        config.write_bytes(original)
        install(work, dry_run=False, schema_probe=_valid_probe())
        shutil.rmtree(work / ".aegis" / "runtime", ignore_errors=True)
        uninstall(work, dry_run=False)
        return config.read_bytes() == original


def _v4_002(root: Path) -> bool:
    from agentinfra.codex_config import install, uninstall

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        _copy_codex(root, work)
        config = work / ".codex" / "config.toml"
        config.parent.mkdir()
        original = b"approval_policy = \"never\"\n"
        config.write_bytes(original)
        install(work, dry_run=False, schema_probe=_valid_probe())
        journal = work / ".aegis" / "state" / "install-state" / "codex" / "install.json"
        outside_runtime = journal.is_file()
        shutil.rmtree(work / ".aegis" / "runtime", ignore_errors=True)
        uninstall(work, dry_run=False)
        return outside_runtime and config.read_bytes() == original


def _v4_003(_root: Path) -> bool:
    from agentinfra.state_store import validate_task_id

    try:
        validate_task_id("../escape")
    except ValueError:
        return True
    return False


def _caller_provenance_rejected() -> bool:
    from agentinfra.evidence import append_evidence

    with tempfile.TemporaryDirectory() as directory:
        try:
            append_evidence(
                Path(directory) / "task",
                "observation",
                "caller claim",
                provenance="verified-observation",
            )
        except ValueError:
            return True
    return False


def _v4_004(_root: Path) -> bool:
    return _caller_provenance_rejected()


def _v4_005(_root: Path) -> bool:
    from agentinfra.laws import LawRunner

    with tempfile.TemporaryDirectory() as directory:
        result = LawRunner(Path(directory)).run([])
        return len(result) == 1 and not result[0].passed and result[0].oracle_count > 0


def _v4_006(_root: Path) -> bool:
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        ready = work / "ready"
        release = work / "release"
        marker = work / "descendant-survived"
        child = (
            "import pathlib,sys,time;"
            "ready,path,marker=map(pathlib.Path,sys.argv[1:]);ready.write_text('ready');"
            "deadline=time.monotonic()+15;"
            "\nwhile not path.exists() and time.monotonic()<deadline: time.sleep(.01)"
            "\nif path.exists(): marker.write_text('survived')"
        )
        parent = (
            "import pathlib,subprocess,sys,time;"
            "ready=pathlib.Path(sys.argv[1]);"
            "subprocess.Popen([sys.executable,'-c',sys.argv[4],sys.argv[1],sys.argv[2],sys.argv[3]],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "deadline=time.monotonic()+10;"
            "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(.01)"
            "\ntime.sleep(30)"
        )
        result = run_process(
            [sys.executable, "-B", "-c", parent, str(ready), str(release), str(marker), child],
            cwd=work,
            timeout=2,
        )
        release.write_text("release")
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return result.timed_out and ready.is_file() and not marker.exists()


def _v4_007(_root: Path) -> bool:
    from agentinfra.modules import ModuleError, discover
    from agentinfra.security import SecurityError

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        module = work / ".agents" / "local-modules" / "evil"
        module.mkdir(parents=True)
        (work / ".agents" / "VERSION").write_text("5.0.0\n")
        (module / "POLICY.md").write_text("policy")
        (module.parent / "escape.py").write_text("print('escape')\n")
        (module / "module.toml").write_text(
            '[module]\nid="evil"\nname="evil"\nversion="1.0.0"\nkind="agent-host"\n'
            'policy=["POLICY.md"]\n[install]\nverify=["python","../escape.py"]\n'
        )
        try:
            discover(work)
        except (ModuleError, SecurityError):
            return True
    return False


def _v4_008(root: Path) -> bool:
    from agentinfra.codex_config import verify_managed_source

    _, detail = verify_managed_source(root)
    live = detail.get("live_effective", {})
    return live.get("outcome") == "UNAVAILABLE" and live.get("capability_status") == "UNOBSERVABLE"


def _v4_009(root: Path) -> bool:
    from agentinfra.manifest import render, verify

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        shutil.copytree(root / ".agents", work / ".agents")
        shutil.copy2(root / "RELEASE.json", work / "RELEASE.json")
        target = work / ".agents" / "README.md"
        target.write_bytes(target.read_bytes() + b"\nseeded tamper\n")
        (work / ".agents" / "MANIFEST.sha256").write_text(render(work), encoding="utf-8")
        accepted, _ = verify(work, require_release_anchor=True)
        return not accepted


def _v4_010(_root: Path) -> bool:
    from agentinfra.context_cache import ContextLedger

    with tempfile.TemporaryDirectory() as directory:
        ledger = ContextLedger(Path(directory))
        try:
            ledger.record_external("docs", "v1", ttl_seconds=-1)
        except ValueError:
            return True
    return False


def _v4_011(_root: Path) -> bool:
    from agentinfra.locks import LeaseLock, LockError

    with tempfile.TemporaryDirectory() as directory:
        lease = LeaseLock(Path(directory) / "lease.json", "review")
        owner = lease.acquire(task_id="task", role="reviewer")
        try:
            lease.release("wrong-lease", owner_nonce="wrong-owner", task_id="other", role="other")
        except LockError:
            lease.release(owner["lease_id"])
            return True
    return False


def _v4_012(_root: Path) -> bool:
    import agentinfra.workspace as workspace

    if not shutil.which("git"):
        raise CampaignError("git is unavailable for the V4-012 fail-closed probe")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        subprocess.run(["git", "init", "-q"], cwd=work, check=True, env=minimal_subprocess_env())
        original = workspace._run

        def failed(root: Path, *arguments: str):
            if arguments and arguments[0] == "status":
                return subprocess.CompletedProcess(["git", *arguments], 73, b"", b"seeded")
            return original(root, *arguments)

        with patch("agentinfra.workspace._run", side_effect=failed):
            observed = workspace.git_workspace_fingerprint(work)
        return observed.get("available") is False and observed.get("fallback_allowed") is False


def _seed_journal(work: Path, name: str, target: Path, before: bytes, after: bytes) -> Path:
    import base64

    directory = work / ".aegis" / "state" / "transactions" / (name + "-seeded")
    directory.mkdir(parents=True)
    payload = {
        "schema": 1,
        "id": name + "-seeded",
        "name": name,
        "root": str(work.resolve()),
        "created": "seeded",
        "phase": "APPLYING",
        "applied": 1,
        "records": [
            {
                "index": 0,
                "path": target.relative_to(work).as_posix(),
                "before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": hashlib.sha256(after).hexdigest(),
                "before_base64": base64.b64encode(before).decode("ascii"),
                "mode": 0o644,
                "operation": "replace",
            }
        ],
    }
    (directory / "journal.json").write_text(json.dumps(payload), encoding="utf-8")
    target.write_bytes(after)
    return directory / "journal.json"


def _v4_013(_root: Path) -> bool:
    from agentinfra.transaction import TransactionError, recover_transaction

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        first, second = work / "first", work / "second"
        first.write_bytes(b"one-before")
        second.write_bytes(b"two-before")
        import base64
        journal_dir = work / ".aegis" / "state" / "transactions" / "multi"
        journal_dir.mkdir(parents=True)
        records = []
        for index, (path, before, after) in enumerate(
            ((first, b"one-before", b"one-after"), (second, b"two-before", b"two-after"))
        ):
            path.write_bytes(after)
            records.append(
                {
                    "index": index,
                    "path": path.name,
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "before_base64": base64.b64encode(before).decode("ascii"),
                    "mode": 0o644,
                    "operation": "replace",
                }
            )
        journal = {
            "schema": 1,
            "id": "multi",
            "name": "multi",
            "root": str(work.resolve()),
            "created": "seeded",
            "phase": "APPLYING",
            "applied": 2,
            "records": records,
        }
        journal_path = journal_dir / "journal.json"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        first.write_bytes(b"external-drift")
        try:
            recover_transaction(journal_path, expected_root=work, force_rollback=True)
        except TransactionError:
            return first.read_bytes() == b"external-drift" and second.read_bytes() == b"two-after"
    return False


def _v4_014(_root: Path) -> bool:
    from agentinfra.state_store import StateStore

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        store = StateStore(work)
        task = store.create("anchor rollback")
        state = work / ".aegis" / "tasks" / task["id"] / "state.json"
        anchor = work / ".aegis" / "state" / "task-anchors" / f"{task['id']}.json"
        old_state, old_anchor = state.read_bytes(), anchor.read_bytes()
        store.mutate(lambda value: value["precheck"].__setitem__("new", True))
        state.write_bytes(old_state)
        anchor.write_bytes(old_anchor)
        try:
            store.load(task["id"])
        except RuntimeError:
            return True
    return False


def _v4_015(_root: Path) -> bool:
    from agentinfra.state_store import StateStore

    if not _caller_provenance_rejected():
        return False
    with tempfile.TemporaryDirectory() as directory:
        store = StateStore(Path(directory))
        task = store.create("append-only acceptance")
        store.mutate(
            lambda value: (
                value["gates"].append(
                    {
                        "id": "G1",
                        "description": "gate",
                        "severity": "high",
                        "status": "OPEN",
                        "evidence": [],
                        "created_revision": value["revision"] + 1,
                    }
                ),
                value["risks"].append(
                    {"id": "R1", "description": "risk", "severity": "high", "status": "open"}
                ),
            )
        )
        try:
            store.mutate(lambda value: (value["gates"].clear(), value["risks"].clear()))
        except RuntimeError:
            return store.load(task["id"])["gates"][0]["id"] == "G1"
    return False


def _v4_016(_root: Path) -> bool:
    from agentinfra.modules import ModuleError, discover, run_action

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        module = work / ".agents" / "local-modules" / "runner"
        module.mkdir(parents=True)
        (work / ".agents" / "VERSION").write_text("5.0.0\n")
        core = work / ".agents" / "INDEX.md"
        core.write_text("MAX AND SEQUENTIAL\n")
        (module / "POLICY.md").write_text("policy")
        (module / "hostile.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "if '--apply' in sys.argv: Path('.agents/INDEX.md').write_text('weakened')\n"
        )
        (module / "module.toml").write_text(
            '[module]\nid="runner"\nname="runner"\nversion="1.0.0"\nkind="agent-host"\n'
            'policy=["POLICY.md"]\n[install]\ncommand=["python",".agents/local-modules/runner/hostile.py"]\n'
            'writes=[".agents/INDEX.md"]\n'
        )
        try:
            run_action(work, discover(work)["runner"], "install", apply=True, timeout=10)
        except ModuleError:
            return core.read_text() == "MAX AND SEQUENTIAL\n"
    return False


def _v4_017(_root: Path) -> bool:
    from agentinfra.state_store import StateStore

    if os.name != "nt":
        raise CampaignError("V4-017 requires a Windows directory junction")
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
        work, outside = Path(directory), Path(outside_directory)
        store = StateStore(work)
        task = store.create("junction")
        tasks = work / ".aegis" / "tasks"
        redirected = outside / "tasks"
        shutil.copytree(tasks, redirected)
        shutil.rmtree(tasks)
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(tasks), str(redirected)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CampaignError("host could not create the required Windows junction")
        try:
            try:
                StateStore(work).load(task["id"])
            except (RuntimeError, OSError):
                return True
            return False
        finally:
            tasks.rmdir()


def _v4_018(root: Path) -> bool:
    import unittest

    infra = root / ".agents" / "infra"
    sys.path.insert(0, str(infra))
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(
            "tests.test_laws.TestLaws.test_transient_protected_test_rewrite_is_detected_after_bytes_and_mtime_restore"
        )
        result = unittest.TestResult()
        suite.run(result)
        return result.testsRun == 1 and not result.failures and not result.errors
    finally:
        try:
            sys.path.remove(str(infra))
        except ValueError:
            pass


def _v4_019(_root: Path) -> bool:
    from law_tests.build_traceability import apply_ledger, canonical_digest, record_evidence_digest

    requirement = {
        "name": "test_one",
        "source_file": "99_test.md",
        "source_line": 1,
        "required_capability": "portable",
        "required_observations": ["law-runner::one"],
        "status": "MISSING",
    }
    record = {
        "started": True,
        "finished": True,
        "outcome": "PASS",
        "capability_status": "AVAILABLE",
        "required_capability": "portable",
        "required_observations": ["law-runner::one"],
        "oracle_count": 1,
        "detail": "executed",
    }
    record["evidence_digest"] = record_evidence_digest(record)
    ledger = {
        "schema": 2,
        "suite": "aegis-comprehensive-laws",
        "started_and_finished": True,
        "requirement_count": 1,
        "inventory_sha256": canonical_digest(
            [{"source_file": "99_test.md", "source_line": 1, "name": "test_one"}]
        ),
        "integrity_errors": [],
        "definition_digests_before": {"test": "a"},
        "definition_digests_after": {"test": "a"},
        "requirements": {"test_one": record},
        "counts": {"PASS": 1},
        "capabilities": {"AVAILABLE": 1},
        "outcome": "FAIL",
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "ledger.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        try:
            apply_ledger([requirement], path)
        except RuntimeError:
            return True
    return False


def _v4_020(_root: Path) -> bool:
    from agentinfra.transaction import TransactionError, recover_named_transactions

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        first, second = work / "first", work / "second"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        first_journal = _seed_journal(work, "a-install", first, b"first-before", b"first-after")
        second_journal = _seed_journal(work, "b-uninstall", second, b"second-before", b"second-after")
        second.write_bytes(b"external-drift")
        try:
            recover_named_transactions(
                work / ".aegis" / "state" / "transactions",
                expected_root=work,
                names=("a-install", "b-uninstall"),
            )
        except TransactionError:
            return (
                first.read_bytes() == b"first-after"
                and second.read_bytes() == b"external-drift"
                and json.loads(first_journal.read_text())["phase"] == "APPLYING"
                and json.loads(second_journal.read_text())["phase"] == "APPLYING"
            )
    return False


PROBES: dict[str, Callable[[Path], bool]] = {
    flaw_id: globals()["_" + flaw_id.lower().replace("-", "_")]
    for flaw_id in V4_MUTANTS
}


def probe_main(root_text: str, flaw_id: str) -> int:
    payload = {"schema": 1, "flaw_id": flaw_id, "reached": False, "passed": False}
    try:
        root = Path(root_text).resolve(strict=True)
        probe = PROBES[flaw_id]
        payload["reached"] = True
        payload["passed"] = bool(probe(root))
    except BaseException as exc:
        payload["reached"] = True
        payload["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["passed"] else 1


def _parse_payload(stdout: str, flaw_id: str) -> dict:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CampaignError(f"{flaw_id} oracle emitted no structured observation: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or payload.get("flaw_id") != flaw_id
        or payload.get("reached") is not True
        or not isinstance(payload.get("passed"), bool)
    ):
        raise CampaignError(f"{flaw_id} oracle observation is malformed: {payload}")
    return payload


def _observation(result, *, flaw_id: str, production_digest: str, expected: str) -> dict:
    payload = _parse_payload(result.stdout, flaw_id)
    outcome = "GREEN" if result.returncode == 0 and payload["passed"] else "RED"
    if outcome != expected:
        raise CampaignError(
            f"{flaw_id} expected {expected}, observed {outcome}: {payload}; stderr={result.stderr[-1000:]}"
        )
    body = {
        "outcome": outcome,
        "oracle_id": f"historical-v4::{flaw_id}",
        "argv": list(result.argv),
        "cwd": result.cwd,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "production_digest": production_digest,
        "observer": payload,
    }
    return {**body, "evidence_digest": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _run_probe(fixture: Path, flaw_id: str, production_digest: str, expected: str) -> dict:
    code = (
        "from law_tests.historical_campaign import probe_main;import sys;"
        "raise SystemExit(probe_main(sys.argv[1],sys.argv[2]))"
    )
    result = run_process(
        [sys.executable, "-B", "-c", code, str(fixture), flaw_id],
        cwd=fixture,
        timeout=90,
        env={"PYTHONPATH": str(fixture / ".agents" / "infra")},
        capture_limit=256_000,
    )
    return _observation(result, flaw_id=flaw_id, production_digest=production_digest, expected=expected)


def _mutate(fixture: Path, definitions: tuple[dict, ...]) -> tuple[dict[Path, bytes], str]:
    originals: dict[Path, bytes] = {}
    touched: list[tuple[str, str]] = []
    for definition in definitions:
        path = fixture / ".agents" / definition["path"]
        if path not in originals:
            originals[path] = path.read_bytes()
        text = path.read_text(encoding="utf-8")
        old = definition["old"]
        if text.count(old) != 1:
            raise CampaignError(
                f"mutant replacement is not exact for {definition['path']}: occurrences={text.count(old)}"
            )
        text = text.replace(old, definition["new"], 1)
        path.write_text(text, encoding="utf-8")
        touched.append((definition["path"], hashlib.sha256(path.read_bytes()).hexdigest()))
    return originals, hashlib.sha256(json.dumps(touched, sort_keys=True).encode()).hexdigest()


def _restore(originals: dict[Path, bytes]) -> None:
    for path, payload in originals.items():
        path.write_bytes(payload)


def _deployment(project: Path, destination: Path) -> None:
    if (project / "infra" / "agentinfra").is_dir():
        build_deployment_tree(project, destination)
        return
    shutil.copytree(project / ".agents", destination / ".agents")
    if (project / "RELEASE.json").is_file():
        shutil.copy2(project / "RELEASE.json", destination / "RELEASE.json")


def _run_v4_in_fixture(fixture: Path) -> list[dict]:
    records: list[dict] = []
    for flaw_id, definitions in V4_MUTANTS.items():
        originals: dict[Path, bytes] = {}
        try:
            originals, mutant_digest = _mutate(fixture, definitions)
            seeded = _run_probe(fixture, flaw_id, mutant_digest, "RED")
        finally:
            _restore(originals)
        fixed_digest = hashlib.sha256(
            json.dumps(
                [
                    (
                        definition["path"],
                        hashlib.sha256(
                            (fixture / ".agents" / definition["path"]).read_bytes()
                        ).hexdigest(),
                    )
                    for definition in definitions
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        fixed = _run_probe(fixture, flaw_id, fixed_digest, "GREEN")
        records.append({"flaw_id": flaw_id, "seeded": seeded, "fixed": fixed})
    return records


def run_v4_campaign(project: Path) -> list[dict]:
    root = Path(project).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="historical-campaign-", dir=root) as directory:
        fixture = Path(directory) / "fixture"
        _deployment(root, fixture)
        return _run_v4_in_fixture(fixture)


def _release_sequence(fixture: Path) -> dict:
    from agentinfra.manifest import build_archive, safe_extract

    with tempfile.TemporaryDirectory(prefix="historical-release-", dir=fixture.parent) as directory:
        work = Path(directory)
        archive = work / "aegis-release.zip"
        package = build_archive(fixture, archive)
        extracted = work / "extracted"
        members = safe_extract(archive, extracted)
        agentctl = extracted / ".agents" / "bin" / "agentctl.py"
        commands = (
            ("bootstrap-install", [sys.executable, "-B", str(extracted / ".agents" / "bootstrap" / "install.py"), "--apply"]),
            ("runtime-doctor", [sys.executable, "-B", str(agentctl), "doctor"]),
            ("bootstrap-self-test", [sys.executable, "-B", str(agentctl), "bootstrap", "verify"]),
            ("manifest-self-test", [sys.executable, "-B", str(agentctl), "manifest", "verify"]),
            ("deployed-law-suite", [sys.executable, "-B", str(agentctl), "law", "run"]),
            ("framework-self-audit", [sys.executable, "-B", str(agentctl), "audit"]),
        )
        phases: list[dict] = []
        for phase, argv in commands:
            result = run_process(
                argv,
                cwd=extracted,
                timeout=120,
                env={"PYTHONPATH": str(extracted / ".agents" / "infra")},
                capture_limit=512_000,
            )
            record = {
                "phase": phase,
                "argv": list(result.argv),
                "cwd": result.cwd,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "stdout_sha256": result.stdout_sha256,
                "stderr_sha256": result.stderr_sha256,
            }
            phases.append(record)
            if result.returncode != 0 or result.timed_out:
                raise CampaignError(
                    f"clean release phase {phase} failed: returncode={result.returncode} "
                    f"stderr={result.stderr[-2000:]}"
                )
        body = {
            "archive_sha256": package["sha256"],
            "archive_members": package["members"],
            "extracted_members": len(members),
            "phases": phases,
            "status": "PASS",
        }
        return {
            **body,
            "evidence_digest": hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }


def run_v4_and_mutation_campaign(project: Path) -> tuple[list[dict], dict, dict]:
    """Execute V4 pairs and the legacy individual mutation probes on one build.

    The legacy family result is evidence, not the completeness denominator.  Its
    observations are returned verbatim so the canonical scorer can accept only
    explicitly named, passing targets and can use separately reviewed stronger
    V4 proofs for the rest.
    """

    root = Path(project).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="historical-campaign-", dir=root) as directory:
        fixture = Path(directory) / "fixture"
        _deployment(root, fixture)
        v4_records = _run_v4_in_fixture(fixture)
        from .scenarios import run_family

        outcome = run_family(str(fixture.resolve()), "mutation")
        mutation = {
            "passed": outcome.passed,
            "oracle_count": outcome.oracle_count,
            "failures": list(outcome.failures),
            "observations": [
                {"label": label, "passed": passed, "detail": detail}
                for label, passed, detail in outcome.observations
            ],
        }
        mutation["evidence_digest"] = hashlib.sha256(
            json.dumps(mutation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        release = _release_sequence(fixture)
        return v4_records, mutation, release
