from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.cli import build_parser, law_files
from agentinfra.context_cache import ContextLedger
from agentinfra import paths as paths_module


SHA_A = "a" * 64


def migration():
    return importlib.import_module("agentinfra.runtime_migration")


def precheck():
    return importlib.import_module("agentinfra.precheck")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0")
        if path.is_file():
            data = path.read_bytes()
            digest.update(str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def fixture(root: Path) -> None:
    (root / ".agents" / "runtime" / "tasks" / "old-task").mkdir(parents=True)
    (root / ".agents" / "persistent" / "task-anchors").mkdir(parents=True)
    (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
    (root / ".agents" / "runtime" / "tasks" / "old-task" / "state.json").write_text('{"schema":2}\n', encoding="utf-8")
    (root / ".agents" / "runtime" / "cache.bin").write_bytes(b"cache")
    (root / ".agents" / "persistent" / "task-anchors" / "old-task.json").write_bytes(b"anchor")
    (root / "AGENTS.md").write_text("governing instructions\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("user data\n", encoding="utf-8")


class OperationalRuntimeProperties(unittest.TestCase):
    def test_source_checkout_wins_operational_framework_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("4.0.0\n", encoding="utf-8")
            (root / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
            (root / "infra" / "agentinfra").mkdir(parents=True)
            (root / "infra" / "laws").mkdir()
            source_law = root / "infra" / "laws" / "framework.toml"
            source_law.write_text("[[law]]\nid='source'\n", encoding="utf-8")
            (root / "laws" / "project").mkdir(parents=True)
            project_law = root / "laws" / "project" / "project.toml"
            project_law.write_text("[[law]]\nid='project'\n", encoding="utf-8")
            (root / "modules").mkdir()
            (root / ".agents" / "infra" / "laws").mkdir(parents=True)
            installed_law = root / ".agents" / "infra" / "laws" / "framework.toml"
            installed_law.write_text("[[law]]\nid='stale-installed'\n", encoding="utf-8")
            (root / ".agents" / "framework.toml").write_text("[framework]\nversion='3.0.0'\n", encoding="utf-8")
            nested = root / "nested" / "work"
            nested.mkdir(parents=True)

            self.assertEqual(paths_module.framework_dir(root), root.resolve())
            self.assertEqual(paths_module.find_root(nested), root.resolve())
            selected = law_files(root.resolve(), type("Args", (), {"files": []})())
            self.assertEqual(selected, [source_law, project_law])
            self.assertNotIn(installed_law, selected)

    def test_runtime_layout_has_separate_named_control_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                paths_module.runtime_dir(root), paths_module.tasks_dir(root), paths_module.persistent_dir(root),
                paths_module.leases_dir(root), paths_module.evidence_dir(root), paths_module.compiled_policy_dir(root),
                paths_module.cache_dir(root), paths_module.audit_dir(root), paths_module.manifests_dir(root),
            }
            self.assertEqual(len(paths), 9)
            self.assertTrue(all(path.parts[-2] == ".aegis" for path in paths))
            (root / ".agents").mkdir()
            ledger = ContextLedger(root)
            self.assertEqual(ledger.path.parent, paths_module.cache_dir(root))

    @given(payload=st.binary(min_size=0, max_size=256))
    def test_legacy_runtime_migration_is_copy_only_and_idempotent(self, payload: bytes) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            (root / ".agents" / "runtime" / "cache.bin").write_bytes(payload)
            governance_before = tree_digest(root / ".agents")
            unrelated_before = (root / "unrelated.txt").read_bytes()
            first = migration().migrate_runtime(root, apply=True)
            self.assertTrue(first["applied"])
            self.assertEqual((root / ".aegis" / "runtime" / "legacy" / "cache.bin").read_bytes(), payload)
            self.assertEqual((root / ".aegis" / "tasks" / "old-task" / "state.json").read_text(encoding="utf-8"), '{"schema":2}\n')
            self.assertEqual((root / ".aegis" / "state" / "task-anchors" / "old-task.json").read_bytes(), b"anchor")
            self.assertEqual(tree_digest(root / ".agents"), governance_before)
            self.assertEqual((root / "unrelated.txt").read_bytes(), unrelated_before)
            second = migration().migrate_runtime(root, apply=True)
            self.assertFalse(second["applied"])
            self.assertEqual(tree_digest(root / ".agents"), governance_before)

    def test_migration_collision_and_interruption_fail_without_partial_destination_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            destination = root / ".aegis" / "runtime" / "legacy" / "cache.bin"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"different")
            with self.assertRaises(migration().RuntimeMigrationError):
                migration().migrate_runtime(root, apply=True)
            self.assertEqual(destination.read_bytes(), b"different")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            def fail(stage: str, _journal: dict) -> None:
                if stage == "after_destination":
                    raise RuntimeError("injected interruption")
            with self.assertRaises(migration().RuntimeMigrationError):
                migration().migrate_runtime(root, apply=True, fault=fail)
            self.assertFalse((root / ".aegis" / "runtime" / "legacy" / "cache.bin").exists())
            self.assertFalse((root / ".aegis" / "tasks" / "old-task" / "state.json").exists())

    def test_operational_cli_does_not_expose_governance_mutators(self) -> None:
        parser = build_parser()
        forbidden = (
            ["bootstrap", "install"], ["bootstrap", "uninstall"], ["manifest", "write"],
            ["manifest", "anchor"], ["module", "install", "codex"], ["module", "uninstall", "codex"],
            ["module", "scaffold", "x"], ["codex-install"],
        )
        for argv in forbidden:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_operational_cli_exposes_discovery_policy_precheck_and_runtime_migration(self) -> None:
        parser = build_parser()
        supported = (
            ["discover"], ["policy", "validate", "project.toml"], ["policy", "compile", "project.toml"],
            ["policy", "explain", "project.toml"], ["precheck", "build", "project.toml"],
            ["runtime", "migrate"], ["runtime", "migrate", "--apply"],
        )
        for argv in supported:
            with self.subTest(argv=argv):
                parser.parse_args(argv)

    def test_precheck_builds_content_addressed_artifacts_without_governance_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".agents").mkdir()
            (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("governance\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_main.py").write_text("assert True\n", encoding="utf-8")
            governance_before = tree_digest(root / ".agents")
            contract = {
                "schema": 1,
                "project": {"name": "fixture"},
                "policy": {"packs": ["python-control-plane"]},
                "boundaries": {"source": ["src/**"], "generated": [], "immutable": [], "vendor": []},
                "commands": {"test": [["python", "-m", "unittest"]]},
            }
            result = precheck().build_precheck(
                root,
                project_contract=contract,
                declared_classes=["FEATURE"],
                changed_paths=["src/main.py"],
                risk="medium",
                test_contract_digest=SHA_A,
                oracle_digest=SHA_A,
            )
            required = {
                "governance_snapshot", "constitution", "instruction_provenance", "repository_discovery",
                "workspace_snapshot", "test_law_baseline", "tdd_plan", "compiled_policy", "mandatory_gates",
                "write_scope", "budgets", "command_matrix", "review_requirements",
            }
            self.assertEqual(set(result["artifacts"]), required)
            self.assertTrue(all(len(value["digest"]) == 64 for value in result["artifacts"].values()))
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(tree_digest(root / ".agents"), governance_before)


if __name__ == "__main__":
    unittest.main()
