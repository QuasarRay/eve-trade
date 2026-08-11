from __future__ import annotations

import unittest


DEPLOYMENT_LAYOUT = __file__.replace("\\", "/").casefold().find("/.agents/infra/tests/") >= 0

try:
    from infra.tests import test_public_falsification_workflow as public_fixture
except ModuleNotFoundError:  # deployed unittest discovery exposes the tests directory directly
    import test_public_falsification_workflow as public_fixture


class TDDAbortRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = public_fixture.PublicFalsificationWorkflowTests(
            methodName="test_required_family_uses_executed_current_evidence"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @unittest.skipIf(DEPLOYMENT_LAYOUT, "abort regression is source-only deep assurance")
    def test_public_abort_can_close_a_legitimate_red_cycle(self) -> None:
        self.fixture.cli(
            "tdd", "design", "--adapter", "unittest",
            "--test-id", "tests.test_normalize.NormalizeContract.test_trim",
            "--test-path", "tests/test_normalize.py",
            "--oracle-path", "tests/oracle.txt",
        )
        self.fixture.cli("tdd", "baseline", "--semantic-reason", "normalization does not trim")
        aborted = self.fixture.cli("tdd", "abort", "--reason", "acceptance oracle needs correction")
        self.assertEqual(aborted["status"], "TDD_CYCLE_ABORTED")
        task = self.fixture.store.load()
        self.assertFalse(task["tdd"]["baseline_observed_by_framework"])
        self.assertFalse(task["tdd"]["green_observed_by_framework"])


if __name__ == "__main__":
    unittest.main()
