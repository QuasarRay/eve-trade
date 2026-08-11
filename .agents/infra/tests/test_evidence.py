import json, tempfile, unittest
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.evidence import _tail_record, append_evidence, load_evidence, rollback_last_evidence, verify_evidence

class TestEvidence(unittest.TestCase):
    def test_caller_cannot_label_manual_append_as_verified_observation(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,"minted only by a framework observation boundary"):
                append_evidence(Path(td),"observation","caller claim",provenance="verified-observation")
            self.assertFalse((Path(td)/"evidence.jsonl").exists())

    def test_large_tail_lookup_uses_bounded_block_reads(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"evidence.jsonl"
            record={"id":"E-large","payload":"x"*(256*1024)}
            path.write_bytes(json.dumps(record).encode("utf-8")+b"\n")
            real_open=Path.open
            reads=[]

            class CountingStream:
                def __init__(self,stream): self.stream=stream
                def __enter__(self): return self
                def __exit__(self,*args): return self.stream.__exit__(*args)
                def seek(self,*args): return self.stream.seek(*args)
                def tell(self): return self.stream.tell()
                def read(self,*args): reads.append(args); return self.stream.read(*args)

            def counted_open(target,*args,**kwargs):
                return CountingStream(real_open(target,*args,**kwargs))

            with patch.object(Path,"open",counted_open):
                self.assertEqual(_tail_record(path),record)
            self.assertLessEqual(len(reads),6)

    def test_chain_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);a=append_evidence(d,"test","one");b=append_evidence(d,"test","two")
            self.assertEqual(b["previous_sha256"],a["record_sha256"])
            self.assertTrue(verify_evidence(d)[0])
            p=d/"evidence.jsonl";lines=p.read_text().splitlines();rec=json.loads(lines[0]);rec["summary"]="tampered";lines[0]=json.dumps(rec);p.write_text("\n".join(lines)+"\n")
            self.assertFalse(verify_evidence(d)[0])

    def test_rollback_removes_only_the_verified_tail_and_preserves_chain(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            first=append_evidence(d,"test","one")
            second=append_evidence(d,"test","two")
            with self.assertRaisesRegex(RuntimeError,"not the verified ledger tail"):
                rollback_last_evidence(d,first["record_sha256"])
            rollback_last_evidence(d,second["record_sha256"])
            self.assertEqual([record["id"] for record in load_evidence(d)],[first["id"]])
            replacement=append_evidence(d,"test","replacement")
            self.assertEqual(replacement["sequence"],2)
            self.assertEqual(replacement["previous_sha256"],first["record_sha256"])
