import subprocess,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.workspace import git_workspace_fingerprint, workspace_fingerprint

class TestWorkspace(unittest.TestCase):
    def test_non_git_fallback_changes(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);(r/"a.txt").write_text("a");one=workspace_fingerprint(r);self.assertEqual(one["kind"],"tree")
            (r/"a.txt").write_text("b");two=workspace_fingerprint(r);self.assertNotEqual(one["sha256"],two["sha256"])
    def test_git_fingerprint_changes_with_untracked_content(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);subprocess.run(["git","init","-q"],cwd=r,check=True)
            a=git_workspace_fingerprint(r);self.assertTrue(a["available"])
            (r/"x.txt").write_text("a");b=git_workspace_fingerprint(r);self.assertNotEqual(a["sha256"],b["sha256"])
            (r/"x.txt").write_text("b");c=git_workspace_fingerprint(r);self.assertNotEqual(b["sha256"],c["sha256"])
