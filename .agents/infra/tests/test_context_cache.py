import tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.context_cache import ContextLedger
class TestContext(unittest.TestCase):
    def test_freshness(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);(r/".aegis"/"runtime").mkdir(parents=True);p=r/"x";p.write_text("a");c=ContextLedger(r);c.record_file(p,"known");self.assertTrue(c.check_file(p)["fresh"]);p.write_text("b");self.assertFalse(c.check_file(p)["fresh"])
    def test_external_fingerprint_and_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);(r/".aegis"/"runtime").mkdir(parents=True);c=ContextLedger(r);c.record_external("docs","v1","known",ttl_seconds=60,provenance="content-sha256");self.assertTrue(c.check_external("docs")["fresh"]);self.assertTrue(c.check_external("docs","v1")["fresh"]);self.assertFalse(c.check_external("docs","v2")["fresh"])
