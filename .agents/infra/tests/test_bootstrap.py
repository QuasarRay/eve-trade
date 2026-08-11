import shutil,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.bootstrap import BEGIN, BootstrapError, install, uninstall
from agentinfra.atomic import AtomicWriteError, atomic_write_bytes
from agentinfra.transaction import TransactionError

SOURCE=Path(__file__).resolve().parents[2]

class TestBootstrap(unittest.TestCase):
    def _root(self,td):
        r=Path(td);(r/'.agents'/'bootstrap').mkdir(parents=True)
        shutil.copy2(SOURCE/'bootstrap'/'root-AGENTS.block.md',r/'.agents'/'bootstrap'/'root-AGENTS.block.md')
        shutil.copy2(SOURCE/'VERSION',r/'.agents'/'VERSION')
        return r
    def test_preserves_existing_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._root(td);before='# Existing project instructions\nKeep me.\n';(r/'AGENTS.md').write_text(before)
            preview=install(r);self.assertFalse(preview['applied']);self.assertTrue(preview['changed'])
            try:
                applied=install(r,apply=True)
            except TransactionError as exc:
                self.fail(f'transactional bootstrap install was rejected by ordinary governance guard: {exc}')
            self.assertTrue(applied['applied'])
            self.assertIn(BEGIN,(r/'AGENTS.md').read_text())
            self.assertIn('Keep me.',(r/'AGENTS.md').read_text())
            removed=uninstall(r,apply=True);self.assertTrue(removed['applied'])
            self.assertEqual((r/'AGENTS.md').read_text(),before)
            with self.assertRaisesRegex(AtomicWriteError,"governing instruction"):
                atomic_write_bytes(r/'AGENTS.md',b'forbidden',root=r)
    def test_clean_install_creates_and_uninstall_removes_root_agents(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._root(td);self.assertFalse((r/'AGENTS.md').exists())
            self.assertTrue(install(r,apply=True)['applied'])
            self.assertTrue((r/'AGENTS.md').is_file())
            self.assertTrue(uninstall(r,apply=True)['applied'])
            self.assertFalse((r/'AGENTS.md').exists())
    def test_unknown_managed_edit_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            r=self._root(td);(r/'AGENTS.md').write_text(f'{BEGIN}\nmanual\n<!-- AEGIS:END -->\n')
            with self.assertRaises(BootstrapError):install(r)
