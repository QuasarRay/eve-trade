from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.manifest import verify as verify_manifest
from agentinfra.audit import audit as framework_audit
from agentinfra.process import run_process


ROOT = Path(__file__).resolve().parents[2]


def release_source():
    return importlib.import_module("agentinfra.release_source")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0")
        if path.is_file():
            data = path.read_bytes()
            digest.update(str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def source_fixture(root: Path) -> None:
    (root / ".agents").mkdir()
    (root / ".agents" / "sentinel").write_text("active governance", encoding="utf-8")
    (root / "VERSION").write_text("5.1.2\n", encoding="utf-8")
    (root / "README.md").write_text("# Aegis Framework 5.1.2\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changes\n", encoding="utf-8")
    (root / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    (root / "MIGRATION.md").write_text("# Migration\n", encoding="utf-8")
    (root / "framework.toml").write_text('[framework]\nversion="5.1.2"\n', encoding="utf-8")
    (root / "workspace-policy.toml").write_text("[fingerprint]\nephemeral=[]\ninclude_ignored=[]\nexternal_symlinks=[]\n", encoding="utf-8")
    (root / "infra").mkdir()
    (root / "infra" / "pyproject.toml").write_text('[project]\nname="aegis-agentinfra"\nversion="5.1.2"\n', encoding="utf-8")
    (root / "infra" / "agentinfra").mkdir()
    (root / "infra" / "agentinfra" / "__init__.py").write_text("", encoding="utf-8")
    (root / "core").mkdir()
    (root / "core" / "charter.md").write_text("immutable doctrine", encoding="utf-8")


class ReleaseSourceProperties(unittest.TestCase):
    def test_subprocess_hypothesis_storage_cannot_mutate_sealed_working_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "from hypothesis.configuration import storage_directory; "
                "path = storage_directory('tmp').path; "
                "path.mkdir(parents=True, exist_ok=True); "
                "(path / 'sentinel').write_text('runtime-only', encoding='utf-8'); "
                "print(path.resolve())"
            )

            result = run_process([sys.executable, "-B", "-c", script], cwd=root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (root / ".hypothesis").exists(),
                "property-test runtime storage contaminated the sealed working tree",
            )

    def test_source_package_excludes_hypothesis_runtime_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            cache_file = root / "infra" / ".hypothesis" / "tmp" / "runtime-sentinel"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text("generated runtime cache", encoding="utf-8")

            destination = root / "dist" / "package"
            release_source().build_deployment_tree(root, destination)

            self.assertFalse((destination / ".agents" / "infra" / ".hypothesis").exists())
            manifest = (destination / ".agents" / "MANIFEST.sha256").read_text(encoding="utf-8")
            self.assertNotIn(".hypothesis", manifest)

    def test_tdd_production_classifier_includes_deployable_governing_policy(self) -> None:
        spec = importlib.util.spec_from_file_location("aegis_verify_release_classifier", ROOT / "scripts" / "verify_release.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        behavioral = (
            "framework.toml",
            "core/00-charter.md",
            "protocols/STATE.md",
            "bootstrap/root-AGENTS.block.md",
            "modules/codex/module.toml",
            "modules/codex/POLICY.md",
            "modules/codex/roles/BASE.md",
            "infra/agentinfra/state_store.py",
            "scripts/verify_release.py",
        )
        non_behavioral = (
            "README.md",
            "MIGRATION.md",
            "infra/README.md",
            "infra/tests/test_state_machine.py",
            "infra/property_tests/test_tdd_lifecycle_properties.py",
            "infra/law_tests/test_meta.py",
        )
        self.assertTrue(all(verifier._behavioral_production_path(path) for path in behavioral))
        self.assertTrue(all(not verifier._behavioral_production_path(path) for path in non_behavioral))

    def test_tdd_provenance_requires_exact_changed_production_file_bijection(self) -> None:
        spec = importlib.util.spec_from_file_location("aegis_verify_release_map", ROOT / "scripts" / "verify_release.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / ".aegis" / "audit"
            cycles = audit / "tdd"
            cycles.mkdir(parents=True)
            implementation = root / "infra" / "agentinfra" / "mechanism.py"
            implementation.parent.mkdir(parents=True)
            implementation.write_text("VALUE = 1\n", encoding="utf-8")
            test_file = root / "infra" / "tests" / "test_mechanism.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("assert True\n", encoding="utf-8")
            cycle = {
                "schema": 1,
                "cycle_id": "TDD-X",
                "status": "GREEN",
                "mode": "RED_REQUIRED",
                "test_contract_sha256": "a" * 64,
                "oracle_sha256": "a" * 64,
                "red": {"command": "python -m unittest test_mechanism"},
                "green": {"command": "python -m unittest test_mechanism", "implementation_sha256": "b" * 64},
            }
            (cycles / "TDD-X.json").write_text(json.dumps(cycle), encoding="utf-8")
            mapping = {
                "schema": 1,
                "baseline_revision": "fixture-baseline",
                "production_files": {
                    "infra/agentinfra/mechanism.py": {
                        "sha256": hashlib.sha256(implementation.read_bytes()).hexdigest(),
                        "cycles": ["TDD-X"],
                        "classification": "behavioral-production",
                        "rationale": "fixture behavior",
                    }
                },
            }
            map_path = audit / "tdd-production-map.json"
            map_path.write_text(json.dumps(mapping), encoding="utf-8")
            changed = ["infra/agentinfra/mechanism.py", "infra/tests/test_mechanism.py"]

            report = verifier.validate_tdd_provenance(root, changed_paths=changed)

            self.assertEqual(report["changed_production_files"], 1)
            self.assertEqual(report["production_changes_without_provenance"], 0)
            map_path.unlink()
            with self.assertRaisesRegex(RuntimeError, "production TDD map"):
                verifier.validate_tdd_provenance(root, changed_paths=changed)
            map_path.write_text(json.dumps(mapping), encoding="utf-8")
            implementation.write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "digest"):
                verifier.validate_tdd_provenance(root, changed_paths=changed)

    def test_source_audit_needs_no_deployed_agents_and_reads_only_source_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            source = release_source()
            for relative in source.SOURCE_FILES:
                path = ROOT / relative
                if path.is_file():
                    shutil.copy2(path, root / relative)
            for relative in source.SOURCE_DIRECTORIES:
                path = ROOT / relative
                if path.is_dir():
                    shutil.copytree(
                        path,
                        root / relative,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )

            self.assertFalse((root / ".agents").exists())
            self.assertEqual(framework_audit(root), [])
            self.assertFalse((root / ".agents").exists())
            self.assertFalse((root / ".aegis").exists())
    def test_tdd_provenance_audit_records_aborted_cycles_without_treating_them_as_green(self) -> None:
        spec = importlib.util.spec_from_file_location("aegis_verify_release", ROOT / "scripts" / "verify_release.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / ".aegis" / "audit" / "tdd"
            audit.mkdir(parents=True)
            green = {
                "schema": 1,
                "cycle_id": "TDD-1",
                "status": "GREEN",
                "mode": "RED_REQUIRED",
                "test_contract_sha256": "a" * 64,
                "oracle_sha256": "a" * 64,
                "red": {"command": "python contract.py"},
                "green": {"command": "python contract.py", "implementation_sha256": "b" * 64},
            }
            aborted = {
                "schema": 1,
                "cycle_id": "TDD-1",
                "status": "ABORTED_BEFORE_GREEN",
                "reason": "test fixture contradicted a stronger invariant",
            }
            (audit / "TDD-001.json").write_text(json.dumps(green), encoding="utf-8")
            (audit / "TDD-001-aborted.json").write_text(json.dumps(aborted), encoding="utf-8")
            report = verifier.validate_tdd_provenance(root)
            self.assertEqual(report["cycle_count"], 1)
            self.assertEqual(report["aborted_count"], 1)
            self.assertEqual(report["aborted"][0]["cycle_id"], "TDD-1")

    def test_release_verifier_is_source_authoritative_and_never_self_installs_governance(self) -> None:
        source = (ROOT / "scripts" / "verify_release.py").read_text(encoding="utf-8")
        self.assertIn('ROOT / "infra"', source)
        self.assertIn("build_deployment_tree", source)
        self.assertIn('root / ".aegis" / "law-results"', source)
        self.assertNotIn('ROOT / ".agents" / "infra"', source)
        self.assertNotIn('"bootstrap" / "install.py"', source)
        self.assertNotIn('"--apply"', source)
        self.assertNotIn('root / ".agents" / "persistent"', source)

    def test_repository_readme_uses_canonical_version(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        heading = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(r"# Aegis Framework ([0-9]+\.[0-9]+\.[0-9]+)", heading)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), version)

    def test_all_source_metadata_validates_against_version_single_source(self) -> None:
        report = release_source().validate_version_consistency(ROOT)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())

    @given(extra=st.binary(min_size=0, max_size=128))
    def test_source_build_is_deterministic_and_never_writes_active_agents(self, extra: bytes) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            (root / "core" / "extra.bin").write_bytes(extra)
            before = tree_digest(root / ".agents")
            first = release_source().build_deployment_tree(root, root / "dist" / "first")
            second = release_source().build_deployment_tree(root, root / "dist" / "second")
            self.assertEqual(first["content_sha256"], second["content_sha256"])
            self.assertEqual(tree_digest(root / ".agents"), before)
            self.assertTrue((root / "dist" / "first" / ".agents" / "MANIFEST.sha256").is_file())
            self.assertEqual((root / "dist" / "first" / ".agents" / "VERSION").read_text(encoding="utf-8"), "5.1.2\n")

    def test_destination_must_be_outside_active_governance_and_source_tree_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            with self.assertRaises(release_source().ReleaseSourceError):
                release_source().build_deployment_tree(root, root / ".agents")
            report = release_source().build_deployment_tree(root, root / "dist" / "aegis-governance")
            packaged = root / "dist" / "aegis-governance" / ".agents" / "core" / "charter.md"
            self.assertEqual(packaged.read_text(encoding="utf-8"), "immutable doctrine")
            self.assertEqual(report["source_root"], str(root.resolve()))

    def test_package_manifest_detects_post_build_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            destination = root / "dist" / "package"
            release_source().build_deployment_tree(root, destination)
            self.assertTrue(release_source().verify_deployment_tree(destination)["ok"])
            (destination / ".agents" / "core" / "charter.md").write_text("tampered", encoding="utf-8")
            self.assertFalse(release_source().verify_deployment_tree(destination)["ok"])

    def test_release_metadata_is_derived_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            destination = root / "dist" / "package"
            result = release_source().build_deployment_tree(root, destination)
            metadata = json.loads((destination / "RELEASE.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["version"], "5.1.2")
            self.assertEqual(metadata["manifest_sha256"], result["manifest_sha256"])

    def test_deployment_release_metadata_is_accepted_by_framework_manifest_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            destination = root / "dist" / "package"
            release_source().build_deployment_tree(root, destination)
            ok, detail = verify_manifest(destination, require_release_anchor=True)
            self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
