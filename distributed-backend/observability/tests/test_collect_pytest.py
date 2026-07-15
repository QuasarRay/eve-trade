from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observability.ci.collect_pytest import _parse_junit


class PytestCollectorTests(unittest.TestCase):
    def test_junit_failure_without_file_uses_module_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            junit = Path(temporary) / "junit.xml"
            junit.write_text(
                '<testsuite tests="1" failures="1" errors="0" skipped="0" time="0.1">'
                '<testcase classname="distributed-backend.tests.e2e.test_trade_lifecycle" name="test_zero">'
                '<failure message="expected rejection">assert False</failure>'
                "</testcase></testsuite>",
                encoding="utf-8",
            )

            summary = _parse_junit(junit)

            self.assertEqual(summary["source_file"], "distributed-backend/tests/e2e/test_trade_lifecycle.py")
            self.assertEqual(
                summary["first_failing_test_nodeid"],
                "distributed-backend/tests/e2e/test_trade_lifecycle.py::test_zero",
            )


if __name__ == "__main__":
    unittest.main()
