from __future__ import annotations

import json
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from observability.ci.run_context import create_run_context, finalize_run_context, load_run_context


class RunContextTests(unittest.TestCase):
    def test_context_writes_required_artifacts_without_promoting_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_repo(root)

            context = create_run_context(root, run_id="test-run")

            for name in ("run-context.json", "git.json", "tool-versions.json", "env-redacted.json", "provenance.json", "run-status.json"):
                self.assertTrue((context.run_dir / name).is_file(), name)
            value = json.loads((context.run_dir / "run-context.json").read_text(encoding="utf-8"))
            self.assertEqual(value["observability.run_id"], "test-run")
            self.assertFalse((root / ".o11y" / "runs" / "latest-local.txt").exists())
            index = json.loads((root / ".o11y" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest_completed_run_id"], "")

            finalize_run_context(context, status="COMPLETE", command="check", exit_code=0)

            self.assertEqual((root / ".o11y" / "runs" / "latest-local.txt").read_text(encoding="utf-8").strip(), "test-run")
            index = json.loads((root / ".o11y" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest_completed_run_id"], "test-run")

    def test_loaded_downloaded_run_uses_its_current_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_repo(root)
            original = create_run_context(root, run_id="portable-run")
            downloaded = root / "downloaded" / "portable-run"
            shutil.copytree(original.run_dir, downloaded)

            loaded = load_run_context(downloaded)

            self.assertEqual(loaded.run_dir, downloaded.resolve())


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "go.work").write_text("go 1.26\n", encoding="utf-8")
    subprocess.run(["git", "add", "go.work"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


if __name__ == "__main__":
    unittest.main()
