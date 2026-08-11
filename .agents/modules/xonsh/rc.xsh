# Source explicitly from Xonsh when you want the project-local helper alias.
# Root discovery mirrors agentinfra.paths.find_root and does not modify global Xonsh configuration.
import sys
from pathlib import Path

def _aegis_find_root():
    p=Path.cwd().resolve()
    for candidate in (p,*p.parents):
        if (candidate/".agents"/"framework.toml").is_file():return candidate
    return None

_aegis_root=_aegis_find_root()
if _aegis_root is not None:
    _aegis_ctl=_aegis_root/".agents"/"bin"/"agentctl.py"
    aliases["agentctl"]=[sys.executable,"-B",str(_aegis_ctl)]
    $AEGIS_ROOT=str(_aegis_root)
