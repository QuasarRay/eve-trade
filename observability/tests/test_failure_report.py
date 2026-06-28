from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observability.ci.classify_failure import classify_failure
from observability.ci.collect_pytest import PytestSummary
from observability.ci.generate_failure_report import generate_failure_report
from observability.ci.run_command import CommandResult
from observability.ci.run_context import RunContext


class FailureReportTests(unittest.TestCase):
    def test_report_contains_failure_source_bubbleup_and_artifact_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / ".o11y" / "runs" / "report-test"
            run_dir.mkdir(parents=True)
            context = RunContext("report-test", run_dir, root, "2026-01-01T00:00:00+00:00", "local", github_sha="abc123")
            command = CommandResult(
                name="pytest-e2e", stage="e2e", argv=["python", "-m", "pytest"], exit_code=1,
                started_at="start", ended_at="end", duration_ms=10.0, stdout="", stderr="failed",
                metadata_path="commands/e2e/pytest-e2e/command.json", log_path="commands/e2e/pytest-e2e/command.log",
            )
            log = run_dir / command.log_path
            log.parent.mkdir(parents=True)
            log.write_text("failed\n", encoding="utf-8")
            pytest = PytestSummary(
                first_failing_test_nodeid="distributed-backend/tests/e2e/test_trade_lifecycle.py::test_accepting_trade_rejects_zero_quantity",
                failure_message="expected rejection",
                assertion_text="quantity_requested=0 was accepted",
                source_file="distributed-backend/tests/e2e/test_trade_lifecycle.py",
                source_line=100,
                source_url="https://github.com/example/eve-trade/blob/abc123/distributed-backend/tests/e2e/test_trade_lifecycle.py#L100",
                failed_count=1,
            )
            classification = classify_failure(nodeid=pytest.first_failing_test_nodeid, assertion=pytest.assertion_text)

            outputs = generate_failure_report(context, command, pytest, classification)

            markdown = outputs["markdown"].read_text(encoding="utf-8")
            self.assertIn("accept-validation", markdown)
            self.assertIn("BubbleUp", markdown)
            self.assertIn("Open failing source line", markdown)
            self.assertIn(command.log_path, markdown)
            self.assertTrue(outputs["html"].is_file())
            self.assertTrue(outputs["json"].is_file())


if __name__ == "__main__":
    unittest.main()
