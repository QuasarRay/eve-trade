from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LAYOUT = ROOT.name == ".agents"
CLI = ROOT / "bin" / "agentctl.py"
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

from agentinfra.assurance import new_tdd_cycle, record_baseline
from agentinfra.governance import capture_governance
from agentinfra.state_store import StateStore
from agentinfra.workspace import workspace_fingerprint


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def seal(value: dict) -> dict:
    body = dict(value)
    body["digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


@unittest.skipIf(
    DEPLOYMENT_LAYOUT,
    "the complete deployed public-workflow smoke test exercises trusted TDD; the deep attack matrix runs from source",
)
class TrustedTDDRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aegis trusted tdd ")
        self.root = Path(self.temporary.name)
        (self.root / ".agents").mkdir()
        (self.root / ".agents" / "framework.toml").write_text(
            "[framework]\nversion='5.1.2'\n", encoding="utf-8"
        )
        (self.root / "src").mkdir()
        (self.root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "calc.py").write_text(
            "def add(left, right):\n    return 0\n", encoding="utf-8"
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_calc.py").write_text(
            "import unittest\n"
            "from src.calc import add\n\n"
            "class CalcContract(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "oracle.txt").write_text("add(2, 3) == 5\n", encoding="utf-8")
        self.store = StateStore(self.root)
        task = self.store.create("trusted tdd", mode="write", risk="high")
        self.task_id = task["id"]
        self.store.transition("DISCOVER", "fixture discovery")
        self.store.transition("PRECHECK", "fixture precheck")
        governance = capture_governance(self.root)
        compiled_policy = seal({
            "schema": 1,
            "task": {"tdd_mode": "RED_REQUIRED"},
        })
        write_scope = seal({
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
        })
        artifacts = {
            "governance_snapshot": governance,
            "constitution": {"digest": SHA_A},
            "instruction_provenance": {"digest": SHA_B},
            "repository_discovery": {"digest": SHA_C},
            "workspace_snapshot": workspace_fingerprint(self.root),
            "test_law_baseline": {"digest": SHA_A},
            "tdd_plan": {
                "digest": SHA_B,
                "payload": {"mode": "RED_REQUIRED"},
            },
            "compiled_policy": compiled_policy,
            "mandatory_gates": {"digest": SHA_A},
            "write_scope": write_scope,
            "budgets": {"digest": SHA_C},
            "command_matrix": {"digest": SHA_A},
            "review_requirements": {"digest": SHA_B},
        }
        self.store.mutate(lambda state: state["precheck"].update(artifacts))
        self.store.transition("TRIAGE", "fixture precheck is current")
        self.store.transition("PLAN", "fixture plan")
        self.store.transition("TEST_DESIGN", "property first")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def cli(self, *args: str, expected: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", str(CLI), "--root", str(self.root), "--json", *args],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, expected, completed.stderr + completed.stdout)
        stream = completed.stdout if completed.stdout.strip() else completed.stderr
        return json.loads(stream)

    def design(self, *, test_id: str = "tests.test_calc.CalcContract.test_add") -> dict:
        return self.cli(
            "tdd",
            "design",
            "--adapter",
            "unittest",
            "--test-id",
            test_id,
            "--test-path",
            "tests/test_calc.py",
            "--oracle-path",
            "tests/oracle.txt",
        )

    def test_public_path_derives_legitimate_red_and_green_from_execution(self) -> None:
        designed = self.design()
        self.assertEqual(designed["status"], "TEST_DESIGNED")

        baseline = self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "the acceptance assertion exposes the missing addition behavior",
        )
        self.assertEqual(baseline["classification"], "EXPECTED_BEHAVIORAL_RED")
        self.assertTrue(baseline["implementation_authorized"])
        self.assertEqual(baseline["observation"]["relevant_test_id"], "tests.test_calc.CalcContract.test_add")
        self.assertEqual(baseline["observation"]["returncode"], 1)
        self.assertTrue(baseline["observation"]["started"])
        self.assertTrue(baseline["observation"]["assertion_boundary_reached"])
        self.assertNotIn("harness_valid", baseline)
        self.assertNotIn("baseline_intact", baseline)

        self.cli("task", "transition", "BASELINE", "--reason", "observed RED")
        self.cli("task", "transition", "IMPLEMENT", "--reason", "baseline authority is current")
        (self.root / "src" / "calc.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        green = self.cli("tdd", "green")
        self.assertEqual(green["classification"], "GREEN")
        self.assertTrue(green["passed"])
        self.assertEqual(green["observation"]["returncode"], 0)
        self.assertTrue(green["observation"]["relevant_test_executed"])
        self.assertNotIn("passed", green["request"])

        status = self.cli("tdd", "status")
        self.assertEqual(status["cycle"]["status"], "GREEN_PROVEN")
        self.assertEqual(status["cycle"]["baseline_evidence_id"], baseline["evidence_id"])
        self.assertEqual(status["cycle"]["green_evidence_id"], green["evidence_id"])

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep negative assurance runs from source; deployed smoke executes the public success path")
    def test_production_mutation_before_baseline_is_detected(self) -> None:
        self.design()
        (self.root / "src" / "calc.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        result = self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "the required behavior should still be absent",
            expected=1,
        )
        self.assertEqual(result["classification"], "TDD_CHRONOLOGY_VIOLATION")
        self.assertFalse(result["implementation_authorized"])
        with self.assertRaisesRegex(Exception, "baseline|invalid transition"):
            self.store.transition("IMPLEMENT", "must reject retroactive TDD")

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep negative assurance runs from source; deployed smoke executes the public success path")
    def test_wrong_test_zero_collection_and_harness_error_fail_closed(self) -> None:
        self.design(test_id="tests.test_calc.CalcContract.test_missing")
        missing = self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "the intended selector must execute",
            expected=1,
        )
        self.assertEqual(missing["classification"], "NO_RELEVANT_TEST_EXECUTED")
        self.cli("tdd", "abort", "--reason", "correct the relevant test selector")

        (self.root / "tests" / "test_calc.py").write_text("raise RuntimeError('import crash')\n", encoding="utf-8")
        self.design()
        crashed = self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "an import crash is not semantic RED",
            expected=1,
        )
        self.assertEqual(crashed["classification"], "HARNESS_FAILURE")
        self.assertFalse(crashed["implementation_authorized"])

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep negative assurance runs from source; deployed smoke executes the public success path")
    def test_timeout_is_distinct_and_cannot_authorize_implementation(self) -> None:
        (self.root / "tests" / "test_calc.py").write_text(
            "import time, unittest\n\n"
            "class CalcContract(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        time.sleep(5)\n",
            encoding="utf-8",
        )
        self.design()
        timed_out = self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "timeouts are capability failures, not RED",
            "--timeout",
            "0.05",
            expected=1,
        )
        self.assertEqual(timed_out["classification"], "TIMEOUT")
        self.assertFalse(timed_out["implementation_authorized"])

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep negative assurance runs from source; deployed smoke executes the public success path")
    def test_green_rejects_test_or_oracle_mutation_after_red(self) -> None:
        self.design()
        self.cli(
            "tdd",
            "baseline",
            "--semantic-reason",
            "the addition behavior is absent",
        )
        self.cli("task", "transition", "BASELINE", "--reason", "observed RED")
        self.cli("task", "transition", "IMPLEMENT", "--reason", "implement")
        (self.root / "src" / "calc.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        (self.root / "tests" / "oracle.txt").write_text("changed oracle\n", encoding="utf-8")
        rejected = self.cli("tdd", "green", expected=1)
        self.assertEqual(rejected["classification"], "FROZEN_CONTRACT_VIOLATION")
        self.assertFalse(rejected["passed"])

    def test_low_level_caller_assertions_cannot_create_implementation_authority(self) -> None:
        task = self.store.load()
        cycle = new_tdd_cycle(
            task_id=task["id"],
            cycle_id="caller-forged",
            mode="RED_REQUIRED",
            designed_at_revision=task["revision"],
            test_contract_digest=SHA_A,
            oracle_digest=SHA_B,
        )
        cycle = record_baseline(
            cycle,
            outcome="RED",
            observed_implementation_digest=SHA_C,
            command=["never-executed"],
            environment_digest=SHA_A,
            output_digest=SHA_B,
            semantic_reason="caller asserts semantic failure",
            harness_valid=True,
            baseline_intact=True,
        )
        with self.assertRaisesRegex(Exception, "framework.*evidence|trusted.*observation|untrusted"):
            self.store.record_tdd_cycle(cycle)

    def test_public_interface_has_no_success_boolean_escape_hatch(self) -> None:
        self.design()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(CLI),
                "--root",
                str(self.root),
                "tdd",
                "baseline",
                "--semantic-reason",
                "attempt",
                "--harness-valid",
                "true",
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
