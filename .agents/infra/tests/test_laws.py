import json, os, tempfile, threading, time, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.laws import LawRunner

class TestLaws(unittest.TestCase):
    def test_unsupported_schema_is_an_explicit_error_result(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('schema=999\n[[law]]\nid="x"\ndescription="x"\nkind="file_exists"\npath="x"\n')
            out=LawRunner(r).run([p])
            self.assertEqual(len(out),1)
            self.assertEqual(out[0].outcome,"ERROR")
            self.assertIn("unsupported law schema",out[0].detail)

    def test_json_law_failure_records_exact_counterexample(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('[[law]]\nid="x"\ndescription="x"\nkind="json_command"\ncommand=["python","-c","import json;print(json.dumps({\\"x\\":2}))"]\njson_path="x"\noperator="eq"\nvalue=3\n')
            result=LawRunner(r).run([p])[0]
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["counterexample"],{"json_path":"x","actual":2,"expected":3,"operator":"eq"})

    def test_json_law(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('[[law]]\nid="x"\ndescription="x"\nkind="json_command"\ncommand=["python","-c","import json;print(json.dumps({\\"x\\":2}))"]\njson_path="x"\noperator="eq"\nvalue=2\n')
            out=LawRunner(r).run([p]);self.assertTrue(out[0].passed,out[0])
    def test_sequence_law(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('[[law]]\nid="seq"\ndescription="seq"\nkind="command_sequence"\nsteps=[{command=["python","-c","print(1)"],stdout_regex="1"},{command=["python","-c","print(2)"],stdout_regex="2"}]\n')
            self.assertTrue(LawRunner(r).run([p])[0].passed)
    def test_differential_json_law(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";cmd='import json;print(json.dumps({"a":1,"noise":2}))'
            # TOML encoded by hand for a deterministic test.
            p.write_text('[[law]]\nid="diff"\ndescription="diff"\nkind="differential_json"\nleft_command=["python","-c","import json;print(json.dumps({\\"a\\":1,\\"noise\\":2}))"]\nright_command=["python","-c","import json;print(json.dumps({\\"a\\":1,\\"noise\\":9}))"]\njson_paths=["a"]\n')
            self.assertTrue(LawRunner(r).run([p])[0].passed)
    def test_invalid_severity_fails_loading(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('[[law]]\nid="x"\ndescription="x"\nkind="command"\nseverity="typo"\ncommand=["python","-c","pass"]\n')
            with self.assertRaises(ValueError):LawRunner(r).load([p])
    def test_empty_sequence_is_not_vacuous_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td);p=r/"laws.toml";p.write_text('[[law]]\nid="x"\ndescription="x"\nkind="command_sequence"\nsteps=[]\n')
            self.assertFalse(LawRunner(r).run([p])[0].passed)

    def test_transient_protected_test_rewrite_is_detected_after_bytes_and_mtime_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);tests=root/".agents"/"infra"/"tests";tests.mkdir(parents=True)
            protected=tests/"test_guard.py";protected.write_bytes(b"ORIGINAL=1\n")
            info=protected.stat();original=protected.read_bytes()
            law=root/"laws.toml";law.write_text('[[law]]\nid="slow"\ndescription="slow"\nkind="command"\ncommand=["{python}","-c","import time;time.sleep(.15)"]\n')
            def mutate_restore():
                time.sleep(.03)
                protected.write_bytes(b"MUTATED=1\n")
                protected.write_bytes(original)
                os.utime(protected,ns=(info.st_atime_ns,info.st_mtime_ns))
            thread=threading.Thread(target=mutate_restore)
            thread.start();results=LawRunner(root).run([law]);thread.join()
            self.assertTrue(any(item.id=="framework.laws.immutable_during_run" and not item.passed for item in results),results)
            self.assertEqual(protected.read_bytes(),original)
