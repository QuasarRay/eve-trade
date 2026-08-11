from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

from hypothesis import given, strategies as st


ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LAYOUT = ROOT.name == ".agents"
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

from agentinfra import scope as scope_module

try:
    from infra.tests import test_public_falsification_workflow as public_fixture
except ModuleNotFoundError:  # deployed unittest discovery exposes the tests directory directly
    import test_public_falsification_workflow as public_fixture


class DynamicWriteAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = public_fixture.PublicFalsificationWorkflowTests(
            methodName="test_required_family_uses_executed_current_evidence"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "deep source assurance is covered by the deployed public-workflow smoke test")
    def test_static_scope_has_no_authority_and_runtime_derives_it(self) -> None:
        compile_parameters = inspect.signature(scope_module.compile_write_scope).parameters
        self.assertNotIn(
            "baseline_authorized",
            compile_parameters,
            "dynamic implementation authority is still embedded in the frozen scope compiler",
        )
        authority_parameters = inspect.signature(scope_module.write_authorized).parameters
        self.assertNotIn("scope", authority_parameters)
        self.assertNotIn("task", authority_parameters)
        self.assertIn("task_id", authority_parameters)

        task = self.fixture.store.load()
        original_scope = task["precheck"]["write_scope"]
        self.assertEqual(original_scope["schema"], 2)
        self.assertNotIn("baseline_authorized", original_scope)
        self.assertTrue(
            scope_module.write_authorized(
                self.fixture.root,
                "tests/test_normalize.py",
                task_id=task["id"],
            )
        )
        self.assertFalse(
            scope_module.write_authorized(
                self.fixture.root,
                "src/normalize.py",
                task_id=task["id"],
            )
        )

        self.fixture.cli(
            "tdd", "design", "--adapter", "unittest",
            "--test-id", "tests.test_normalize.NormalizeContract.test_trim",
            "--test-path", "tests/test_normalize.py",
            "--oracle-path", "tests/oracle.txt",
        )
        self.fixture.cli("tdd", "baseline", "--semantic-reason", "normalization does not trim")
        self.fixture.cli("task", "transition", "BASELINE", "--reason", "trusted RED")
        self.fixture.cli("task", "transition", "IMPLEMENT", "--reason", "dynamic authority")
        implementation_task = self.fixture.store.load()
        self.assertEqual(
            implementation_task["precheck"]["write_scope"],
            original_scope,
            "baseline authority mutated the frozen static scope",
        )
        self.assertTrue(
            scope_module.write_authorized(
                self.fixture.root,
                "src/normalize.py",
                task_id=implementation_task["id"],
            )
        )
        self.assertFalse(
            scope_module.write_authorized(
                self.fixture.root,
                "tests/test_normalize.py",
                task_id=implementation_task["id"],
            )
        )

    @given(
        path=st.sampled_from(
            (
                ".agents/core/policy.md",
                ".Agents\\core\\policy.md",
                "AGENTS.md",
                "nested/project/source.py",
            )
        )
    )
    def test_protected_boundaries_never_gain_dynamic_authority(self, path: str) -> None:
        self.assertIn(
            "task_id",
            inspect.signature(scope_module.write_authorized).parameters,
            "managed authority still accepts caller-supplied scope/phase facts",
        )
        task = self.fixture.store.load()
        self.assertFalse(scope_module.write_authorized(self.fixture.root, path, task_id=task["id"]))


if __name__ == "__main__":
    unittest.main()
