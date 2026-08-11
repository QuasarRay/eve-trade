#!/usr/bin/env xonsh
# Xonsh wrapper. Use Xonsh only when its mixed Python/subprocess semantics reduce tool/context churn.
import subprocess
import sys
from pathlib import Path
ctl = Path($XONSH_SOURCE).resolve().with_name("agentctl.py")
if sys.version_info < (3, 11):
    print("Aegis requires Python 3.11 or newer", file=sys.stderr)
    raise SystemExit(126)
completed = subprocess.run([sys.executable, "-B", str(ctl), *sys.argv[1:]], check=False)
raise SystemExit(completed.returncode)
