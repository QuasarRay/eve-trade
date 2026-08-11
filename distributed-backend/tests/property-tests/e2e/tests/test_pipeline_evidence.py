from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


E2E_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = Path(__file__).resolve().parents[2] / "infra"
sys.path.insert(0, str(INFRA_ROOT))

from orchestrate import pytest_stage  # noqa: E402


def test_failed_pytest_stage_returns_persistable_evidence_before_pipeline_abort():
    # Keep the nested pytest root inside the permitted workspace. Some managed
    # Windows sandboxes cannot traverse the parent of their isolated temp root.
    with tempfile.TemporaryDirectory(prefix=".pipeline-canary-", dir=E2E_ROOT) as temporary:
        artifact_root = Path(temporary)
        canary = artifact_root / "test_pipeline_failure_canary.py"
        canary.write_text("def test_canary():\n    assert False\n", encoding="utf-8")

        record = pytest_stage(
            "failure-canary",
            [str(canary), "-q"],
            env=os.environ.copy(),
            artifact_root=artifact_root,
            timeout=60,
        )

        assert record["exit_status"] == 1
        assert Path(record["junit"]).is_file()
        assert "FAILED" in Path(record["log"]).read_text(encoding="utf-8")
