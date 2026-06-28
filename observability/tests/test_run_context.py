from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from observability.ci.run_context import create_run_context, load_run_context


class RunContextTests(unittest.TestCase):
    def test_context_always_writes_required_artifacts_and_latest_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "go.work").write_text("go 1.26\n", encoding="utf-8")

            context = create_run_context(root, run_id="test-run")

            for name in ("run-context.json", "git.json", "tool-versions.json", "env-redacted.json"):
                self.assertTrue((context.run_dir / name).is_file(), name)
            value = json.loads((context.run_dir / "run-context.json").read_text(encoding="utf-8"))
            self.assertEqual(value["observability.run_id"], "test-run")
            self.assertEqual((root / ".o11y" / "runs" / "latest-local.txt").read_text(encoding="utf-8").strip(), str(context.run_dir.resolve()))

    def test_loaded_downloaded_run_uses_its_current_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "go.work").write_text("go 1.26\n", encoding="utf-8")
            original = create_run_context(root, run_id="portable-run")
            downloaded = root / "downloaded" / "portable-run"
            shutil.copytree(original.run_dir, downloaded)

            loaded = load_run_context(downloaded)

            self.assertEqual(loaded.run_dir, downloaded.resolve())


if __name__ == "__main__":
    unittest.main()
