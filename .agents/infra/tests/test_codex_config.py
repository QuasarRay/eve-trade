import tempfile, unittest, tomllib, shutil
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.codex_config import merge_conservative, ConfigError, install, uninstall, verify_static, load_role_specs

class TestCodex(unittest.TestCase):
    def _schema_probe(self):
        return {"available":True,"capability":"AVAILABLE","probe_kind":"isolated-unit-contract","supported_keys":["model","model_reasoning_effort","max_concurrent_threads_per_session","max_depth","multi_agent_v2","default_subagent_model","default_subagent_reasoning_effort"],"version":"isolated-test-schema"}
    def _fixture(self,td):
        src=Path(__file__).resolve().parents[2];r=Path(td)
        shutil.copytree(src/"modules"/"codex",r/".agents"/"modules"/"codex")
        (r/".aegis"/"runtime").mkdir(parents=True)
        shutil.copy2(src/"VERSION",r/".agents"/"VERSION")
        return r
    def test_merge_preserves_user(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);out=merge_conservative('approval_policy = "never"\n\n[features]\nfoo=true\n',r)
            d=tomllib.loads(out);self.assertEqual(d["approval_policy"],"never");self.assertEqual(d["model"],"gpt-5.6-sol");self.assertEqual(d["model_reasoning_effort"],"max");self.assertEqual(d["agents"]["max_concurrent_threads_per_session"],1);self.assertEqual(d["agents"]["max_depth"],1);self.assertEqual(d["features"]["multi_agent_v2"]["max_concurrent_threads_per_session"],2);self.assertIn("aegis_verifier",d["agents"])
    def test_conflict_fails(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td)
            with self.assertRaises(ConfigError):merge_conservative('model = "other"\n',r)
    def test_install_roles_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);report=install(r,dry_run=False,schema_probe=self._schema_probe());self.assertTrue(report["changed"]);ok,detail=verify_static(r);self.assertTrue(ok,detail)
    def test_install_uninstall_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);(r/".codex").mkdir();original='approval_policy = "never"\n';(r/".codex"/"config.toml").write_text(original)
            install(r,dry_run=False,schema_probe=self._schema_probe());self.assertTrue(verify_static(r)[0]);uninstall(r,dry_run=False);self.assertEqual((r/".codex"/"config.toml").read_text(),original);self.assertFalse(any((r/".codex"/"agents").glob("aegis-*.toml")))
    def test_uninstall_refuses_post_install_drift(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);install(r,dry_run=False,schema_probe=self._schema_probe());p=r/".codex"/"config.toml";p.write_text(p.read_text()+"# user edit\n")
            with self.assertRaises(ConfigError):uninstall(r,dry_run=False)
    def test_role_registry_is_single_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=self._fixture(td);specs=load_role_specs(root);self.assertGreaterEqual(len(specs),10);self.assertEqual(specs["aegis_implementer"]["sandbox_mode"],"workspace-write")
    def test_merge_with_existing_nested_agent_but_no_parent_table(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);out=merge_conservative('[agents.custom]\ndescription="custom"\n',r);d=tomllib.loads(out);self.assertEqual(d["agents"]["custom"]["description"],"custom");self.assertEqual(d["features"]["multi_agent_v2"]["max_concurrent_threads_per_session"],2)
    def test_v2_limit_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td)
            with self.assertRaises(ConfigError):merge_conservative('[features.multi_agent_v2]\nmax_concurrent_threads_per_session=4\n',r)
    def test_dotted_and_inline_managed_tables_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td)
            for text in ['agents.max_depth = 1\n','agents = { max_depth = 1 }\n','features = { multi_agent_v2 = { max_concurrent_threads_per_session = 2 } }\n','features.multi_agent_v2.max_concurrent_threads_per_session = 2\n']:
                with self.subTest(text=text):
                    with self.assertRaises(ConfigError):merge_conservative(text,r)
    def test_managed_role_allows_extra_unmanaged_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._fixture(td);base=merge_conservative('',r);base=base.replace('config_file = "agents/aegis-verifier.toml"','config_file = "agents/aegis-verifier.toml"\nnickname_candidates=["Verifier"]');out=merge_conservative(base,r);self.assertIn('nickname_candidates',out)
