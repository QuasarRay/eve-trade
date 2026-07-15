from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observability.ci.storage import RunStorage


class StorageTests(unittest.TestCase):
    def test_copying_artifact_already_at_destination_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = RunStorage(Path(temporary))
            source = storage.write_text("pytest/pytest-junit.xml", "<testsuites />\n")

            copied = storage.copy(source, "pytest/pytest-junit.xml")

            self.assertEqual(copied, source)
            self.assertEqual(source.read_text(encoding="utf-8"), "<testsuites />\n")


if __name__ == "__main__":
    unittest.main()
