from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_rendered_kubernetes import image_reference_error


class ProductionImagePolicyTests(unittest.TestCase):
    def test_rejects_all_zero_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'0' * 64}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_repeated_character_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'a' * 64}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_known_sentinel_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'deadbeef' * 8}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_placeholder_registry(self) -> None:
        image = f"registry.example.com/app@sha256:{'1234abcd' * 8}"
        self.assertEqual(image_reference_error(image), "placeholder registry")

    def test_accepts_immutable_non_placeholder_reference(self) -> None:
        image = f"ghcr.io/example/app@sha256:{'1234abcd' * 8}"
        self.assertIsNone(image_reference_error(image))


if __name__ == "__main__":
    unittest.main()
