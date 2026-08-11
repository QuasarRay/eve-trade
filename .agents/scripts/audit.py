from pathlib import Path
import subprocess,sys
framework=Path(__file__).resolve().parents[1]
root=framework.parent if framework.name==".agents" else framework
raise SystemExit(subprocess.call([sys.executable,str(framework/"bin"/"agentctl.py"),"--root",str(root),"audit"],cwd=root))
