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

from agentinfra.assurance import build_falsification_receipt
from agentinfra.governance import capture_governance
from agentinfra.state_store import StateStore
from agentinfra.workspace import workspace_fingerprint


SHA = "a" * 64


def seal(value: dict) -> dict:
    body = dict(value)
    body["digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


class PublicFalsificationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aegis falsification ")
        self.root = Path(self.temporary.name)
        (self.root / ".agents").mkdir()
        (self.root / ".agents" / "framework.toml").write_text(
            "[framework]\nversion='5.1.2'\n", encoding="utf-8"
        )
        (self.root / "src").mkdir()
        (self.root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "normalize.py").write_text(
            "def normalize(value):\n    return value\n", encoding="utf-8"
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_normalize.py").write_text(
            "import unittest\n"
            "from src.normalize import normalize\n\n"
            "class NormalizeContract(unittest.TestCase):\n"
            "    def test_trim(self):\n"
            "        self.assertEqual(normalize('  x  '), 'x')\n\n"
            "    def test_boundary(self):\n"
            "        self.assertEqual(normalize(''), '')\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "oracle.txt").write_text("trim surrounding whitespace\n", encoding="utf-8")
        self.store = StateStore(self.root)
        task = self.store.create("public falsification", mode="write", risk="high")
        self.task_id = task["id"]
        self.store.transition("DISCOVER", "discover")
        self.store.transition("PRECHECK", "precheck")
        governance = capture_governance(self.root)
        compiled = seal({
            "schema": 1,
            "task": {"tdd_mode": "RED_REQUIRED", "classes": ["BUG_FIX"]},
            "falsification": {"required_families": ["boundary"]},
        })
        scope = seal({
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
            "constitution": {"digest": SHA},
            "instruction_provenance": {"digest": SHA},
            "repository_discovery": {"digest": SHA},
            "workspace_snapshot": workspace_fingerprint(self.root),
            "test_law_baseline": {"digest": SHA},
            "tdd_plan": {"digest": SHA},
            "compiled_policy": compiled,
            "mandatory_gates": {"digest": SHA},
            "write_scope": scope,
            "budgets": {"digest": SHA},
            "command_matrix": {"digest": SHA},
            "review_requirements": {"digest": SHA},
        }
        self.store.mutate(lambda state: state["precheck"].update(artifacts))
        self.store.transition("TRIAGE", "precheck current")
        self.store.transition("PLAN", "plan")
        self.store.transition("TEST_DESIGN", "test first")

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

    def reach_falsify(self) -> dict:
        self.cli(
            "tdd", "design", "--adapter", "unittest",
            "--test-id", "tests.test_normalize.NormalizeContract.test_trim",
            "--test-path", "tests/test_normalize.py",
            "--oracle-path", "tests/oracle.txt",
        )
        self.cli("tdd", "baseline", "--semantic-reason", "normalization does not trim")
        self.cli("task", "transition", "BASELINE", "--reason", "observed RED")
        self.cli("task", "transition", "IMPLEMENT", "--reason", "implement")
        (self.root / "src" / "normalize.py").write_text(
            "def normalize(value):\n    return value.strip()\n", encoding="utf-8"
        )
        self.cli("tdd", "green")
        self.cli("task", "transition", "GREEN", "--reason", "trusted green")
        return self.cli("task", "transition", "FALSIFY", "--reason", "seek counterexamples")

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "the deployed complete public-workflow smoke test already executes this falsification path")
    def test_required_family_uses_executed_current_evidence(self) -> None:
        task = self.reach_falsify()
        cycle = next(item for item in task["tdd"]["cycles"] if item["cycle_id"] == task["tdd"]["active_cycle_id"])
        forged = build_falsification_receipt(
            task_id=task["id"],
            tdd_cycle_digest=cycle["cycle_sha256"],
            epoch=task["change_epoch"],
            diff_digest=task["diff_digest"],
            methods=["caller prose"],
            attempts=["caller prose"],
            boundary_cases=["caller prose"],
            counterexamples=[],
            outcome="NO_COUNTEREXAMPLE",
        )
        with self.assertRaisesRegex(Exception, "execution|evidence|schema"):
            self.store.record_falsification(forged)

        attempt = self.cli(
            "falsify", "run",
            "--family", "boundary",
            "--adapter", "unittest",
            "--test-id", "tests.test_normalize.NormalizeContract.test_boundary",
            "--interpretation", "empty input preserves the boundary contract",
        )
        self.assertEqual(attempt["capability_status"], "PROVEN")
        self.assertEqual(attempt["outcome"], "NO_COUNTEREXAMPLE")
        self.assertTrue(attempt["observation"]["relevant_test_executed"])
        self.assertTrue(attempt["evidence_id"].startswith("E-"))

        completed = self.cli("falsify", "complete")
        receipt = completed["receipt"]
        self.assertEqual(receipt["schema"], 2)
        self.assertEqual(receipt["required_families"], ["boundary"])
        self.assertEqual(receipt["outcome"], "NO_COUNTEREXAMPLE")
        self.assertEqual(receipt["attempts"][0]["evidence_id"], attempt["evidence_id"])
        self.assertNotIn("methods", receipt)
        self.assertNotIn("attempts", completed.get("request", {}))


if __name__ == "__main__":
    unittest.main()
