from pathlib import Path
import sys
HERE=Path(__file__).resolve()
INFRA=HERE.parents[1]/"infra"
sys.path.insert(0,str(INFRA))
from agentinfra.cli import main
raise SystemExit(main())
