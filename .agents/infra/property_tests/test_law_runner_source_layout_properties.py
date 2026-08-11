from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.laws import LawRunner


class LawRunnerSourceLayoutProperties(unittest.TestCase):
    def source_root(self, directory: str) -> Path:
        root = Path(directory)
        for relative in ("infra/tests", "infra/laws", "infra/law_tests", "tests-to-impl"):
            (root / relative).mkdir(parents=True)
        (root / "framework.toml").write_text("source-authority\n", encoding="utf-8")
        (root / ".agents").mkdir()
        (root / ".agents" / "framework.toml").write_text("stale-deployment\n", encoding="utf-8")
        return root

    @given(kind=st.sampled_from(("file", "command")))
    def test_portable_agents_paths_resolve_to_source_authority(self, kind: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.source_root(directory)
            definition = root / "infra" / "laws" / "source.toml"
            if kind == "file":
                definition.write_text(
                    '[[law]]\nid="source.file"\ndescription="source"\nkind="file_contains"\n'
                    'path=".agents/framework.toml"\ntext="source-authority"\n',
                    encoding="utf-8",
                )
            else:
                (root / "infra" / "tests" / "probe.py").write_text("print('source-probe')\n", encoding="utf-8")
                definition.write_text(
                    '[[law]]\nid="source.command"\ndescription="source"\nkind="command"\n'
                    'command=["{python}",".agents/infra/tests/probe.py"]\nstdout_regex="source-probe"\n',
                    encoding="utf-8",
                )
            result = LawRunner(root).run([definition])
            self.assertEqual(len(result), 1, result)
            self.assertTrue(result[0].passed, result[0])

    def test_clean_deployment_keeps_agents_paths_under_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".agents" / "infra" / "laws").mkdir(parents=True)
            (root / ".agents" / "infra" / "tests").mkdir(parents=True)
            (root / ".agents" / "infra" / "law_tests").mkdir(parents=True)
            (root / ".agents" / "tests-to-impl").mkdir(parents=True)
            (root / ".agents" / "framework.toml").write_text("deployed-authority\n", encoding="utf-8")
            definition = root / ".agents" / "infra" / "laws" / "deployed.toml"
            definition.write_text(
                '[[law]]\nid="deployed.file"\ndescription="deployed"\nkind="file_contains"\n'
                'path=".agents/framework.toml"\ntext="deployed-authority"\n',
                encoding="utf-8",
            )
            result = LawRunner(root).run([definition])
            self.assertEqual(len(result), 1, result)
            self.assertTrue(result[0].passed, result[0])


if __name__ == "__main__":
    unittest.main()
