import os
import stat
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentinfra.process import run_process
from agentinfra.security import SecurityError, ensure_private_control_file, minimal_subprocess_env, redact_text


class TestProcessSecurity(unittest.TestCase):
    def test_posix_world_writable_control_adapter_is_rejected_before_use(self):
        class WorldWritableControl:
            def is_symlink(self):
                return False

            def exists(self):
                return True

            def stat(self, *, follow_symlinks=True):
                class Result:
                    st_mode = stat.S_IFREG | 0o666

                return Result()

            def __str__(self):
                return "forged-world-writable-control"

        with patch("agentinfra.security.os.name", "posix"):
            with self.assertRaises(SecurityError):
                ensure_private_control_file(WorldWritableControl())

    def test_law_runner_preserves_argv_boundaries_for_spaces_quotes_unicode_and_metacharacters(self):
        with tempfile.TemporaryDirectory(prefix="Aegis space ") as td:
            values = ["space value", 'quote"value', "unicodé-雪", "&|;$()"]
            code = "import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=False))"
            result = run_process([sys.executable, "-c", code, *values], cwd=Path(td))
            self.assertEqual(result.returncode, 0)
            import json

            self.assertEqual(json.loads(result.stdout), values)

    def test_law_stdout_and_stderr_capture_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_process(
                [sys.executable, "-c", "import sys;sys.stdout.write('x'*50000);sys.stderr.write('y'*50000)"],
                cwd=Path(td),
                capture_limit=4096,
            )
            self.assertEqual(len(result.stdout), 4096)
            self.assertEqual(len(result.stderr), 4096)
            self.assertTrue(result.stdout_truncated)
            self.assertTrue(result.stderr_truncated)
            self.assertEqual(result.stdout_bytes, 50000)
            self.assertEqual(result.stderr_bytes, 50000)

    def test_law_runner_reports_exact_exit_code_and_timeout_status(self):
        with tempfile.TemporaryDirectory() as td:
            failed = run_process([sys.executable, "-c", "raise SystemExit(7)"], cwd=Path(td))
            self.assertEqual(failed.returncode, 7)
            self.assertFalse(failed.timed_out)
            timed = run_process([sys.executable, "-c", "import time;time.sleep(30)"], cwd=Path(td), timeout=0.2)
            self.assertTrue(timed.timed_out)
            self.assertNotEqual(timed.returncode, 0)

    def test_framework_redacts_common_token_key_password_and_private_key_patterns(self):
        raw = "token=abc123 password: hunter2 sk-abcdefghijklmnopqrstuvwxyz"
        redacted = redact_text(raw)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)

    def test_module_action_receives_minimal_environment_allowlist_by_default(self):
        old = os.environ.get("AEGIS_TEST_SECRET_TOKEN")
        os.environ["AEGIS_TEST_SECRET_TOKEN"] = "must-not-leak"
        try:
            env = minimal_subprocess_env()
            self.assertNotIn("AEGIS_TEST_SECRET_TOKEN", env)
            self.assertNotIn("must-not-leak", env.values())
        finally:
            if old is None:
                os.environ.pop("AEGIS_TEST_SECRET_TOKEN", None)
            else:
                os.environ["AEGIS_TEST_SECRET_TOKEN"] = old

    def test_subprocess_home_and_cache_are_isolated_outside_workspace_and_removed(self):
        with tempfile.TemporaryDirectory(prefix="aegis workspace ") as td:
            root = Path(td)
            code = (
                "import json,os,pathlib;"
                "home=pathlib.Path(os.environ['HOME']);"
                "(home/'child-marker').write_text('x');"
                "print(json.dumps({'home':str(home),'userprofile':os.environ.get('USERPROFILE'),"
                "'cache':os.environ.get('XDG_CACHE_HOME')}))"
            )
            result = run_process([sys.executable, "-c", code], cwd=root)
            self.assertEqual(result.returncode, 0)
            import json

            payload = json.loads(result.stdout)
            child_home = Path(payload["home"])
            self.assertNotEqual(child_home, root)
            self.assertEqual(payload["userprofile"], payload["home"])
            self.assertFalse(child_home.exists(), "isolated subprocess home was not cleaned")
            self.assertFalse((root / "~").exists(), "subprocess treated a literal tilde as its home")
