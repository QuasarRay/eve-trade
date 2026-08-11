import json, subprocess, sys, tempfile, unittest
from pathlib import Path

CLI=Path(__file__).resolve().parents[2]/"bin"/"agentctl.py"

class TestCliWorkflow(unittest.TestCase):
    def root(self,td):
        root=Path(td);(root/".aegis"/"runtime").mkdir(parents=True);(root/".agents"/"framework.toml").parent.mkdir(parents=True);(root/".agents"/"framework.toml").write_text("[framework]\nversion='5.0.0'\n")
        return root
    def run_cli(self,root,*args,expect=0):
        cp=subprocess.run([sys.executable,"-B",str(CLI),"--root",str(root),*args],text=True,capture_output=True)
        self.assertEqual(cp.returncode,expect,msg=cp.stderr+cp.stdout)
        return json.loads(cp.stdout) if cp.stdout.strip().startswith(("{","[")) else cp.stdout

    def test_task_list_and_durable_child_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.root(td)
            task=self.run_cli(root,"task","new","--title","handoff")
            listed=self.run_cli(root,"task","list")
            self.assertEqual([x["id"] for x in listed],[task["id"]])
            opened=self.run_cli(root,"subagent","open","--role","reviewer")
            lease=opened["lease"]["lease_id"]
            self.run_cli(root,"subagent","close","--lease-id",lease,"--outcome","accepted","--summary","review integrated")
            state=self.run_cli(root,"task","status")
            self.assertIsNone(state["active_child"]);self.assertEqual(state["child_history"][-1]["outcome"],"accepted")
            self.assertEqual(state["child_history"][-1]["summary"],"review integrated")

    def test_decision_record(self):
        with tempfile.TemporaryDirectory() as td:
            root=self.root(td)
            self.run_cli(root,"task","new","--title","decision")
            self.run_cli(root,"task","decision-add","Use adapter","--rationale","preserve compatibility")
            state=self.run_cli(root,"task","status")
            self.assertEqual(state["decisions"][0]["statement"],"Use adapter")
