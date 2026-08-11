from __future__ import annotations

"""Small framework-owned unittest observation adapter.

The adapter captures test output away from its protocol stream and emits one
machine-readable result.  Its exit status distinguishes pass, semantic
assertion failure, harness error, and absent/irrelevant selection.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import traceback
import unittest


def _ids(suite: unittest.TestSuite) -> list[str]:
    found: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            found.extend(_ids(item))
        else:
            found.append(item.id())
    return found


class ObservingResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[str] = []
        self.completed: list[str] = []
        self.failure_ids: list[str] = []
        self.error_ids: list[str] = []
        self.skipped_ids: list[str] = []
        self.error_kinds: list[str] = []

    def startTest(self, test) -> None:  # noqa: N802 - unittest protocol
        self.started.append(test.id())
        super().startTest(test)

    def stopTest(self, test) -> None:  # noqa: N802 - unittest protocol
        self.completed.append(test.id())
        super().stopTest(test)

    def addFailure(self, test, err) -> None:  # noqa: N802 - unittest protocol
        self.failure_ids.append(test.id())
        super().addFailure(test, err)

    def addError(self, test, err) -> None:  # noqa: N802 - unittest protocol
        self.error_ids.append(test.id())
        rendered = "".join(traceback.format_exception(*err))
        if "has no attribute" in rendered:
            self.error_kinds.append("SELECTOR_NOT_FOUND")
        elif "ModuleNotFoundError" in rendered or "ImportError" in rendered or "import" in rendered.casefold():
            self.error_kinds.append("IMPORT_OR_COLLECTION_ERROR")
        else:
            self.error_kinds.append("TEST_RUNTIME_ERROR")
        super().addError(test, err)

    def addSkip(self, test, reason) -> None:  # noqa: N802 - unittest protocol
        self.skipped_ids.append(test.id())
        super().addSkip(test, reason)


def observe(root: Path, test_id: str) -> tuple[dict, int]:
    project = root.resolve(strict=True)
    sys.path.insert(0, str(project))
    loader = unittest.TestLoader()
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        suite = loader.loadTestsFromName(test_id)
        collected = _ids(suite)
        result = ObservingResult()
        suite.run(result)
    output = captured_out.getvalue().encode("utf-8", "replace")
    errors = captured_err.getvalue().encode("utf-8", "replace")
    relevant_collected = test_id in collected
    relevant_started = test_id in result.started
    relevant_completed = test_id in result.completed
    relevant_failed = test_id in result.failure_ids
    relevant_errored = test_id in result.error_ids
    relevant_skipped = test_id in result.skipped_ids
    payload = {
        "schema": 1,
        "adapter": "unittest",
        "requested_test_id": test_id,
        "collected_ids": sorted(collected),
        "collected_count": len(collected),
        "started_ids": result.started,
        "completed_ids": result.completed,
        "failure_ids": result.failure_ids,
        "error_ids": result.error_ids,
        "skipped_ids": result.skipped_ids,
        "error_kinds": result.error_kinds,
        "relevant_collected": relevant_collected,
        "relevant_started": relevant_started,
        "relevant_completed": relevant_completed,
        "relevant_failed": relevant_failed,
        "relevant_errored": relevant_errored,
        "relevant_skipped": relevant_skipped,
        "tests_run": result.testsRun,
        "captured_stdout_sha256": hashlib.sha256(output).hexdigest(),
        "captured_stderr_sha256": hashlib.sha256(errors).hexdigest(),
        "captured_stdout_bytes": len(output),
        "captured_stderr_bytes": len(errors),
    }
    if relevant_failed and relevant_started and relevant_completed and not relevant_errored:
        return payload, 1
    if relevant_collected and relevant_started and relevant_completed and not relevant_skipped and not result.errors and not result.failures:
        return payload, 0
    if result.errors and any(kind != "SELECTOR_NOT_FOUND" for kind in result.error_kinds):
        return payload, 2
    if not relevant_collected or not relevant_started:
        return payload, 3
    return payload, 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aegis-unittest-observer")
    parser.add_argument("--root", required=True)
    parser.add_argument("--test-id", required=True)
    args = parser.parse_args(argv)
    try:
        payload, code = observe(Path(args.root), args.test_id)
    except BaseException as exc:
        payload = {
            "schema": 1,
            "adapter": "unittest",
            "requested_test_id": args.test_id,
            "observer_failure": type(exc).__name__,
        }
        code = 2
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
