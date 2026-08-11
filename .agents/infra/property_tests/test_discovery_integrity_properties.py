from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.governance import GovernanceViolation, assert_mutation_allowed


LEAF = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=12,
)
LOCKFILE = st.sampled_from(("Cargo.lock", "poetry.lock", "package-lock.json", "go.sum", "pnpm-lock.yaml"))


def _modules():
    return importlib.import_module("agentinfra.discovery"), importlib.import_module("agentinfra.governance")


def _fixture(directory: str) -> Path:
    root = Path(directory)
    (root / ".agents" / "core").mkdir(parents=True)
    (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
    (root / ".agents" / "core" / "policy.md").write_text("immutable\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("root instructions\n", encoding="utf-8")
    return root


class DiscoveryIntegrityProperties(unittest.TestCase):
    @given(package=LEAF, lockfile=LOCKFILE)
    def test_discovery_is_deterministic_and_classifies_boundaries(self, package: str, lockfile: str) -> None:
        discovery, _ = _modules()
        with tempfile.TemporaryDirectory(prefix="aegis discovery ") as directory:
            root = _fixture(directory)
            (root / package / "src").mkdir(parents=True)
            (root / package / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n", encoding="utf-8")
            (root / lockfile).write_text("locked\n", encoding="utf-8")
            (root / "vendor").mkdir()
            (root / "generated").mkdir()
            nested = root / package / "nested-repo" / ".git"
            nested.mkdir(parents=True)
            first = discovery.discover_repository(root)
            second = discovery.discover_repository(root)
            self.assertEqual(first, second)
            self.assertEqual(first["repository_root"], str(root.resolve()))
            self.assertIn(f"{package}/pyproject.toml", first["package_roots"])
            self.assertIn(lockfile, first["lockfiles"])
            self.assertIn(f"{package}/nested-repo", first["nested_repositories"])
            self.assertIn(".agents", first["governance_roots"])
            self.assertRegex(first["digest"], r"^[0-9a-f]{64}$")

    def test_discovery_artifact_is_written_only_under_dot_aegis(self) -> None:
        discovery, _ = _modules()
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            governance_before = hashlib.sha256((root / ".agents" / "framework.toml").read_bytes()).hexdigest()
            artifact = discovery.discover_repository(root)
            path = discovery.write_discovery_artifact(root, artifact)
            self.assertEqual(path.parent, root / ".aegis" / "audit")
            self.assertEqual(hashlib.sha256((root / ".agents" / "framework.toml").read_bytes()).hexdigest(), governance_before)

    @given(payload=st.binary(min_size=0, max_size=128))
    def test_governance_snapshot_detects_out_of_band_change_and_fails_closed(self, payload: bytes) -> None:
        _, governance = _modules()
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            snapshot = governance.capture_governance(root)
            self.assertTrue(governance.verify_governance(root, snapshot)["ok"])
            target = root / ".agents" / "core" / "policy.md"
            target.write_bytes(payload + b"changed")  # simulated same-user out-of-band mutation in fixture only
            with self.assertRaises(GovernanceViolation):
                governance.verify_governance(root, snapshot)

    def test_governing_instruction_snapshot_detects_root_agents_change(self) -> None:
        _, governance = _modules()
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            snapshot = governance.capture_governance(root)
            (root / "AGENTS.md").write_text("out-of-band rewrite\n", encoding="utf-8")
            with self.assertRaises(GovernanceViolation):
                governance.verify_governance(root, snapshot)

    @given(alias=st.sampled_from((".agents", ".AGENTS", ".Agents", ".aGeNtS")), leaf=LEAF)
    def test_windows_case_aliases_cannot_bypass_governance_guard(self, alias: str, leaf: str) -> None:
        if os.name != "nt":
            self.skipTest("Windows case-alias property")
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            with self.assertRaises(GovernanceViolation):
                assert_mutation_allowed(root, root / alias / leaf, operation="alias write")

    def test_symlink_alias_into_governance_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            alias = root / "governance-alias"
            try:
                alias.symlink_to(root / ".agents", target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(GovernanceViolation):
                assert_mutation_allowed(root, alias / "core" / "policy.md", operation="redirected write")

    def test_windows_junction_alias_into_governance_is_rejected_when_supported(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows junction integration property")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell junction creation capability is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture(directory)
            alias = root / "governance-junction"
            command = (
                "$ErrorActionPreference='Stop';"
                "New-Item -ItemType Junction -Path $env:AEGIS_JUNCTION -Target $env:AEGIS_TARGET | Out-Null"
            )
            created = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=root,
                env={**os.environ, "AEGIS_JUNCTION": str(alias), "AEGIS_TARGET": str(root / ".agents")},
                capture_output=True,
                check=False,
                timeout=20,
            )
            if created.returncode != 0:
                self.skipTest("Windows junction creation capability is unavailable")
            with self.assertRaises(GovernanceViolation):
                assert_mutation_allowed(root, alias / "core" / "policy.md", operation="junction write")


if __name__ == "__main__":
    unittest.main()
