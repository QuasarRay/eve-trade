import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.lawlib import LawFailure, associative, commutative, conservation, deterministic, differential, idempotent, invariant_sequence, monotonic, roundtrip

class TestLawLib(unittest.TestCase):
    def test_common_laws(self):
        self.assertEqual(deterministic(lambda x:x*x,[1,2,3]).cases,3)
        self.assertEqual(idempotent(abs,[-2,1]).cases,2)
        self.assertEqual(roundtrip(str,int,[1,2]).cases,2)
        self.assertEqual(commutative(lambda a,b:a+b,[(1,2)]).cases,1)
        self.assertEqual(associative(lambda a,b:a+b,[(1,2,3)]).cases,1)
        self.assertEqual(monotonic(lambda x:x*2,[(1,2)]).cases,1)
        self.assertEqual(conservation(lambda xs:list(reversed(xs)),[[1,2,3]],len).cases,1)
        self.assertEqual(differential(lambda x:x+1,lambda x:1+x,[2]).cases,1)
        self.assertEqual(invariant_sequence(0,[1,2],lambda s,a:s+a,lambda s:s>=0).cases,2)
    def test_violation_raises(self):
        with self.assertRaises(LawFailure):idempotent(lambda x:x+1,[1])
