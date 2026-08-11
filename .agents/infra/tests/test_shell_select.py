import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.shell_select import available, choose

class TestShell(unittest.TestCase):
    def test_oneshot_is_direct(self):self.assertEqual(choose("oneshot")["shell"],"direct")
    def test_xonsh_preferred_for_mixed_interactive_when_available(self):
        def fake(name):return "/bin/xonsh" if name=="xonsh" else None
        with patch("agentinfra.shell_select.shutil.which",side_effect=fake), patch("agentinfra.shell_select._xonsh_compatible",return_value=True):
            available.cache_clear()
            self.assertEqual(choose("interactive")["shell"],"xonsh")
        available.cache_clear()
    def test_unknown_purpose_fails_closed(self):
        with self.assertRaises(ValueError):choose("mystery-purpose")
    def test_incompatible_xonsh_is_not_selected(self):
        with patch("agentinfra.shell_select.shutil.which",side_effect=lambda name:"/bin/xonsh" if name=="xonsh" else None), patch("agentinfra.shell_select._xonsh_compatible",return_value=False):
            available.cache_clear()
            self.assertEqual(choose("python-mixed")["shell"],"direct")
        available.cache_clear()
    def test_project_required_native_shell_takes_precedence(self):
        with patch("agentinfra.shell_select.shutil.which",side_effect=lambda name:f"/native/{name}" if name in {"xonsh","bash"} else None), patch("agentinfra.shell_select._xonsh_compatible",return_value=True):
            available.cache_clear()
            selected=choose("python-mixed",required_shell="BASH")
            self.assertEqual((selected["shell"],selected["path"]),("bash","/native/bash"))
        available.cache_clear()
    def test_unavailable_required_native_shell_fails_closed(self):
        with patch("agentinfra.shell_select.shutil.which",return_value=None):
            available.cache_clear()
            with self.assertRaises(RuntimeError):choose("interactive",required_shell="xonsh")
        available.cache_clear()
    def test_discovery_is_cached_across_repeated_selection(self):
        with patch("agentinfra.shell_select.shutil.which",return_value=None) as which:
            available.cache_clear()
            choose("interactive");choose("interactive")
            self.assertEqual(which.call_count,5)
        available.cache_clear()
