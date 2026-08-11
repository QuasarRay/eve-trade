import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentinfra.evidence import _append_framework_evidence, _append_verified_observation, append_evidence, execute_command_evidence, load_evidence, rollback_last_evidence
from agentinfra.falsification_runtime import complete as complete_falsification
from agentinfra.falsification_runtime import run_attempt as run_falsification_attempt
from agentinfra.governance import capture_governance
from agentinfra.review_runtime import complete as complete_review
from agentinfra.state_machine import TransitionError
from agentinfra.state_store import StateStore
from agentinfra.tdd_runtime import baseline as observe_baseline
from agentinfra.tdd_runtime import design as design_tdd
from agentinfra.tdd_runtime import green as observe_green
from agentinfra.workspace import workspace_fingerprint


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LAYOUT = FRAMEWORK_ROOT.name == ".agents"
CLI = FRAMEWORK_ROOT / "bin" / "agentctl.py"


class TestState(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".agents").mkdir()
        (self.root / ".agents" / "framework.toml").write_text(
            "[framework]\nversion='4.0.0'\n", encoding="utf-8"
        )
        (self.root / "src").mkdir()
        (self.root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "state_probe.py").write_text("VALUE = 0\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_state_probe.py").write_text(
            "import unittest\n"
            "from src.state_probe import VALUE\n\n"
            "class StateProbeContract(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(VALUE, 1)\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "oracle.txt").write_text("VALUE must equal 1\n", encoding="utf-8")
        self.s = StateStore(self.root)

    def tearDown(self):
        self.td.cleanup()

    def _task_dir(self, task_id):
        return self.root / ".aegis" / "tasks" / task_id

    def _cli(self, *arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", str(CLI), "--root", str(self.root), "--json", *arguments],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return json.loads(completed.stdout)

    def _precheck(self):
        self.s.transition("DISCOVER", "repository discovery")
        self.s.transition("PRECHECK", "compile artifacts")
        governance = capture_governance(self.root)
        compiled = {
            "schema": 1,
            "task": {"tdd_mode": "RED_REQUIRED", "classes": ["BUG_FIX"]},
            "falsification": {"required_families": ["boundary"]},
            "gates": [{
                "id": "G1",
                "description": "state-machine acceptance contract",
                "severity": "HARD",
                "family": "fixture",
                "waivable": False,
            }],
            "commands": {"test": [["python", "-B", "-m", "unittest"]]},
        }
        compiled["digest"] = hashlib.sha256(
            json.dumps(compiled, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
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
        scope["digest"] = hashlib.sha256(
            json.dumps(scope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        artifacts = {
            "governance_snapshot": governance,
            "constitution": {"digest": SHA_B},
            "instruction_provenance": {"digest": SHA_C},
            "repository_discovery": {"digest": SHA_D},
            "workspace_snapshot": workspace_fingerprint(self.root),
            "test_law_baseline": {"digest": SHA_E},
            "tdd_plan": {"digest": SHA_F},
            "compiled_policy": compiled,
            "mandatory_gates": {"digest": SHA_B},
            "write_scope": scope,
            "budgets": {"digest": SHA_D},
            "command_matrix": {"digest": SHA_E},
            "review_requirements": {"digest": SHA_F},
        }
        self.s.mutate(lambda task: task["precheck"].update(artifacts))
        return self.s.transition("TRIAGE", "precheck current")

    def _baseline_cycle(self, *, remediation=False):
        design_tdd(
            self.root,
            adapter_kind="unittest",
            test_id="tests.test_state_probe.StateProbeContract.test_value",
            test_paths=["tests/test_state_probe.py"],
            oracle_paths=["tests/oracle.txt"],
        )
        result, authorized = observe_baseline(
            self.root,
            semantic_reason="the fixture value has not been implemented",
            timeout=10,
        )
        self.assertTrue(authorized, result)
        self.assertEqual(result["classification"], "EXPECTED_BEHAVIORAL_RED")

    def _implement(self):
        self._precheck()
        self.s.transition("PLAN", "bounded plan")
        self.s.transition("TEST_DESIGN", "test first")
        self._baseline_cycle()
        self.s.transition("BASELINE", "legitimate RED")
        return self.s.transition("IMPLEMENT", "implementation authorized")

    def _remediate(self):
        self.s.transition("TEST_DESIGN", "regression first")
        self._baseline_cycle(remediation=True)
        self.s.transition("BASELINE", "remediation RED")
        return self.s.transition("REMEDIATE", "remediation authorized")

    def _green(self, *, verify=False):
        (self.root / "src" / "state_probe.py").write_text("VALUE = 1\n", encoding="utf-8")
        observed, passed = observe_green(self.root, timeout=10)
        self.assertTrue(passed, observed)
        self.assertEqual(observed["classification"], "GREEN")
        result = self.s.transition("GREEN", "frozen contract green")
        return self.s.transition("VERIFY", "verification") if verify else result

    def _review_current(self):
        self.s.transition("FALSIFY", "seek counterexamples")
        attempt = run_falsification_attempt(
            self.root,
            family="boundary",
            adapter_kind="unittest",
            test_id="tests.test_state_probe.StateProbeContract.test_value",
            interpretation="the current candidate preserves the executable boundary contract",
            timeout=10,
        )
        self.assertEqual(attempt["capability_status"], "PROVEN")
        complete_falsification(self.root)
        self.s.transition("ADVERSARIAL_REVIEW", "independent review")
        opened = self._cli(
            "subagent", "open",
            "--role", "adversarial-reviewer",
            "--context-evidence", attempt["evidence_id"],
        )
        lease = opened["lease"]["lease_id"]
        review_path = self.root / ".aegis" / "state-machine-review.json"
        review_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "assumptions_tested": [{
                        "claim": "the candidate matches the frozen contract",
                        "observation": "the bounded executable evidence passed",
                        "evidence_ids": [attempt["evidence_id"]],
                    }],
                    "counterexamples_attempted": [{
                        "claim": "the boundary contract may fail",
                        "observation": "no counterexample was observed",
                        "evidence_ids": [attempt["evidence_id"]],
                    }],
                    "boundary_cases": [{
                        "claim": "the fixture value boundary",
                        "observation": "the exact contract passed",
                        "evidence_ids": [attempt["evidence_id"]],
                    }],
                    "potential_failures": [{
                        "claim": "post-review mutation",
                        "observation": "must invalidate the review",
                        "evidence_ids": [attempt["evidence_id"]],
                    }],
                    "unexpected_scope": [],
                    "findings": [],
                    "outcome": "ACCEPTED",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self._cli(
            "subagent", "close",
            "--lease-id", lease,
            "--outcome", "accepted",
            "--summary", "bounded state-machine review complete",
            "--evidence", attempt["evidence_id"],
            "--review-file", str(review_path),
        )
        return complete_review(self.root, lease_id=lease)

    def test_precheck_fails_closed(self):
        self.s.create("x", mode="write")
        self.s.transition("DISCOVER", "discover")
        self.s.transition("PRECHECK", "start")
        with self.assertRaises(TransitionError):
            self.s.transition("TRIAGE", "too soon")

    def test_valid_precheck(self):
        self.s.create("x", mode="write")
        self._precheck()
        self.assertEqual(self.s.load()["state"], "TRIAGE")

    def test_read_task_with_fresh_external_source_completes_exact_audit(self):
        task = self.s.create("fresh research claim", mode="read", risk="low")
        self._precheck()
        self.s.transition("PLAN", "bounded research plan")
        self.s.transition("VERIFY", "verify current external source")
        current = self.s.load()
        record = _append_framework_evidence(
            self._task_dir(task["id"]),
            "external-source",
            "fresh bound research source",
            provenance="external-source",
            task_id=task["id"],
            change_epoch=current["change_epoch"],
            task_revision=current["revision"],
            source_identity="https://example.invalid/research-source",
            source_fingerprint="sha256:verified-source",
            observed_at=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=3600,
            workspace=workspace_fingerprint(self.root),
        )
        self.s.mutate(lambda state: (
            state.__setitem__("verification_evidence", [record["id"]]),
            state.__setitem__("verification_epoch", state["change_epoch"]),
            state.__setitem__("evidence_head", record["record_sha256"]),
        ))
        self.s.transition("FINAL_AUDIT", "fresh direct research evidence")
        audited = None
        error = None
        try:
            audited = self.s.audit_complete()
        except Exception as exc:
            error = exc
        self.assertIsNone(error, f"exact read-task audit was rejected: {error}")
        self.assertIsNotNone(audited)
        observations = audited["final_audit_receipt"]["observations"]
        self.assertEqual(len(observations), 40)
        self.assertTrue(any(item["status"] == "NOT_APPLICABLE" for item in observations))
        self.assertEqual(self.s.transition("FINALIZE", "research proof complete")["state"], "FINALIZE")

    def test_transition_history_is_atomic_state(self):
        task = self.s.create("x")
        self.s.transition("DISCOVER", "start")
        current = self.s.load()
        self.assertEqual(current["transitions"][-1]["to"], "DISCOVER")
        self.assertFalse((self._task_dir(task["id"]) / "transitions.jsonl").exists())

    def test_blocked_only_resumes_previous_state(self):
        self.s.create("x")
        self.s.transition("DISCOVER", "start")
        self.s.transition("BLOCKED", "external")
        with self.assertRaises(TransitionError):
            self.s.transition("IMPLEMENT", "skip")
        self.assertEqual(self.s.transition("DISCOVER", "resume")["state"], "DISCOVER")

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep source assurance is covered by the deployed public-workflow smoke test")
    def test_implementation_invalidates_stale_proof(self):
        self.s.create("x")
        self._implement()

        def proven(task):
            task["verification_evidence"] = ["E-old"]
            task["verification_epoch"] = task["change_epoch"]
            task["gates"] = [
                {"id": "G1", "description": "proof gate", "status": "PROVEN", "evidence": ["E-old"]},
                {"id": "G2", "description": "waived gate", "status": "WAIVED", "evidence": [], "waiver_reason": "not applicable", "waiver_authority": "policy:test-policy"},
            ]

        self.s.mutate(proven)
        epoch = self.s.load()["change_epoch"]
        self.s.transition("DIAGNOSE", "issue")
        self._remediate()
        task = self.s.load()
        self.assertFalse(task["final_audit_complete"])
        self.assertNotIn("final_audit_workspace", task)
        self.assertEqual(task["verification_evidence"], [])
        self.assertIsNone(task["verification_epoch"])
        self.assertEqual(task["change_epoch"], epoch + 1)
        self.assertEqual(task["gates"][0]["status"], "OPEN")
        self.assertEqual(task["gates"][0]["evidence"], [])
        self.assertEqual(task["gates"][1]["status"], "WAIVED")

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep source assurance is covered by the deployed public-workflow smoke test")
    def test_final_audit_requires_resolved_gates_and_critical_risks(self):
        self.s.create("x")
        self._implement()
        self._green(verify=True)
        task = self.s.load()
        record = _append_verified_observation(
            self._task_dir(task["id"]), "observation", "current verified observation",
            task_id=task["id"], change_epoch=task["change_epoch"], task_revision=task["revision"],
            workspace=workspace_fingerprint(self.root), gate_ids=["G1"],
        )

        def setup(value):
            value["verification_evidence"] = [record["id"]]
            value["verification_epoch"] = value["change_epoch"]
            value["evidence_head"] = record["record_sha256"]
            value["gates"] = [{"id": "G1", "description": "current proof", "status": "OPEN", "evidence": []}]
            value["risks"] = [{"id": "R1", "description": "critical risk", "severity": "critical", "status": "open"}]

        self.s.mutate(setup)
        self._review_current()
        with self.assertRaises(TransitionError):
            self.s.transition("FINAL_AUDIT", "too soon")
        self.s.mutate(lambda value: value["gates"][0].update(status="PROVEN", evidence=[record["id"]]))
        with self.assertRaises(TransitionError):
            self.s.transition("FINAL_AUDIT", "risk still open")
        self.s.mutate(lambda value: value["risks"][0].update(status="resolved", resolution="verified mitigation"))
        self.assertEqual(self.s.transition("FINAL_AUDIT", "ready")["state"], "FINAL_AUDIT")

    def test_finalize_rejects_evidence_from_prior_epoch_even_if_state_is_tampered(self):
        task = self.s.create("x")
        task_dir = self._task_dir(task["id"])
        old = append_evidence(task_dir, "test", "old proof", change_epoch=0)
        path = task_dir / "state.json"
        forged = json.loads(path.read_text())
        forged["state"] = "FINAL_AUDIT"
        forged["change_epoch"] = 1
        forged["verification_epoch"] = 1
        forged["verification_evidence"] = [old["id"]]
        forged["gates"] = [{"id": "G1", "description": "gate", "status": "PROVEN", "evidence": [old["id"]]}]
        forged["final_audit_complete"] = True
        path.write_text(json.dumps(forged))
        with self.assertRaisesRegex(RuntimeError, "state (?:schema|integrity)"):
            self.s.transition("FINALIZE", "must reject forged stale proof")

    def test_failed_state_save_rolls_back_its_evidence_append(self):
        task = self.s.create("x")
        task_dir = self._task_dir(task["id"])
        attached = {}

        def mutate_with_bad_state(state):
            record = append_evidence(task_dir, "test", "must roll back", task_revision=state["revision"], lock_held=True)
            attached["record"] = record
            state["evidence_head"] = record["record_sha256"]
            state["title"] = "illegal immutable rewrite"

        def rollback():
            record = attached.get("record")
            if record:
                rollback_last_evidence(task_dir, record["record_sha256"], lock_held=True)

        with self.assertRaisesRegex(RuntimeError, "immutable task field changed"):
            self.s.mutate(mutate_with_bad_state, hold_evidence_lock=True, on_failure=rollback)
        self.assertEqual(load_evidence(task_dir), [])
        current = self.s.load()
        self.assertEqual(current["revision"], 0)
        self.assertIsNone(current["evidence_head"])

    def test_unbound_direct_append_blocks_canonical_state_mutation(self):
        task = self.s.create("x")
        task_dir = self._task_dir(task["id"])
        append_evidence(task_dir, "test", "orphan")
        with self.assertRaisesRegex(RuntimeError, "evidence head is not the verified ledger head"):
            self.s.mutate(lambda state: state["precheck"].__setitem__("x", True))

    def test_critical_gate_cannot_be_waived_by_a_caller_supplied_policy_string(self):
        self.s.create("x")
        self.s.mutate(lambda state: state["gates"].append({
            "id": "G1", "description": "critical proof", "severity": "critical",
            "status": "OPEN", "evidence": [], "created_revision": state["revision"] + 1,
        }))

        def forge_waiver(state):
            state["gates"][0].update({
                "status": "WAIVED", "waiver_reason": "caller says so",
                "waiver_authority": "policy:caller-controlled",
            })

        with self.assertRaisesRegex(RuntimeError, "critical acceptance gates cannot be waived"):
            self.s.mutate(forge_waiver)

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep source assurance is covered by the deployed public-workflow smoke test")
    def test_finalized_load_fails_closed_after_workspace_mutation(self):
        self.s.create("x", mode="write", risk="low")
        self._implement()
        self._green(verify=True)
        task = self.s.load()
        task_dir = self._task_dir(task["id"])
        record, command = execute_command_evidence(
            task_dir,
            root=self.root,
            argv=[sys.executable, "-B", "-m", "unittest", "tests.test_state_probe.StateProbeContract.test_value"],
            summary="current exact verification command",
            change_epoch=task["change_epoch"],
            task_revision=task["revision"],
            gate_ids=["G1"],
        )
        self.assertEqual(command.returncode, 0)

        def bind(state):
            state["evidence_head"] = record["record_sha256"]
            state["verification_evidence"] = [record["id"]]
            state["verification_epoch"] = state["change_epoch"]
            state["gates"] = [{
                "id": "G1", "description": "proof", "severity": "high", "gate_severity": "HARD", "status": "PROVEN",
                "evidence": [record["id"]], "created_revision": 0,
            }]

        self.s.mutate(bind)
        self._review_current()
        self.s.transition("FINAL_AUDIT", "ready")
        self.s.audit_complete()
        finalized = self.s.transition("FINALIZE", "done")
        self.assertEqual(finalized["state"], "FINALIZE")
        with self.assertRaisesRegex(RuntimeError, "terminal task state FINALIZE rejects evidence append"):
            append_evidence(task_dir, "observation", "must not append", task_id=task["id"], change_epoch=task["change_epoch"])
        self.assertEqual(self.s.load()["state"], "FINALIZE")
        (self.root / "post-finalize.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "workspace no longer matches"):
            self.s.load()

    def test_state_and_current_anchor_rollback_is_rejected_by_anchor_history(self):
        task = self.s.create("rollback")
        state_path = self._task_dir(task["id"]) / "state.json"
        anchor_path = self.root / ".aegis" / "state" / "task-anchors" / f"{task['id']}.json"
        old_state = state_path.read_bytes()
        old_anchor = anchor_path.read_bytes()
        self.s.mutate(lambda state: state["precheck"].__setitem__("newer", True))
        state_path.write_bytes(old_state)
        anchor_path.write_bytes(old_anchor)
        with self.assertRaisesRegex(RuntimeError, "anchor history"):
            self.s.load(task["id"])

    def test_gate_and_risk_history_cannot_be_deleted_or_replaced(self):
        self.s.create("append-only claims")
        self.s.mutate(lambda state: (
            state["gates"].append({"id": "G1", "description": "real gate", "severity": "high", "status": "OPEN", "evidence": [], "created_revision": state["revision"] + 1}),
            state["risks"].append({"id": "R1", "description": "real risk", "severity": "high", "status": "open"}),
        ))
        with self.assertRaisesRegex(RuntimeError, "gate history cannot be deleted"):
            self.s.mutate(lambda state: state.__setitem__("gates", [{"id": "Gfake", "description": "replacement", "severity": "low", "status": "OPEN", "evidence": [], "created_revision": state["revision"] + 1}]))
        with self.assertRaisesRegex(RuntimeError, "risk history cannot be deleted"):
            self.s.mutate(lambda state: state.__setitem__("risks", []))

    def test_proven_gate_requires_nonempty_evidence(self):
        self.s.create("empty proof")
        with self.assertRaisesRegex(RuntimeError, "proven acceptance gate requires evidence"):
            self.s.mutate(lambda state: state["gates"].append({"id": "G1", "description": "fake", "severity": "low", "status": "PROVEN", "evidence": [], "created_revision": state["revision"] + 1}))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_runtime_junction_cannot_redirect_state_reads_outside_project(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(td)
            outside = Path(outside_directory)
            (root / ".agents").mkdir()
            store = StateStore(root)
            task = store.create("junction")
            tasks = root / ".aegis" / "tasks"
            target = outside / "redirected-tasks"
            shutil.copytree(tasks, target)
            shutil.rmtree(tasks)
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(tasks), str(target)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("host cannot create a directory junction")
            try:
                with self.assertRaisesRegex(RuntimeError, "(?:escapes root|redirected control path)"):
                    StateStore(root).load(task["id"])
            finally:
                tasks.rmdir()
