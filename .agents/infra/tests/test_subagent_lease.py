import tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.locks import LeaseLock, LockError

class TestLease(unittest.TestCase):
    def test_sequential_and_cross_invocation_release(self):
        with tempfile.TemporaryDirectory() as td:
            l=LeaseLock(Path(td)/"lease.json","one-child")
            info=l.acquire(task_id="T1",role="reviewer")
            with self.assertRaises(LockError):l.acquire(task_id="T2",role="worker")
            # Logical lease is intentionally releasable from a later CLI process using its token.
            l.release(info["lease_id"])
            self.assertFalse(l.inspect()["exists"])
