from pathlib import Path
import subprocess,sys
framework=Path(__file__).resolve().parents[1]
root=framework.parent if framework.name==".agents" else framework
cmd=[sys.executable,str(framework/"bin"/"agentctl.py"),"--root",str(root),"law","run"]
raise SystemExit(subprocess.call(cmd,cwd=root))
