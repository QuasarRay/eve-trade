"""Execute static and property-based reliability contracts inside Dagger."""
from __future__ import annotations
from _common import sh, stage, with_source
from _images import PYTHON_SLIM_IMAGE

HYPOTHESIS_VERSION="6.165.2"

async def run(dag):
    ctr=with_source(dag.container().from_(PYTHON_SLIM_IMAGE),dag)
    ctr=sh(ctr,[
        "python .github/dagger/tests/static_contract.py",
        "python -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r .github/dagger/requirements-reliability.txt",
        "PYTHONPATH=.github/dagger/tests python -m pytest -q .github/dagger/tests",
        "PYTHONPATH=.github/dagger/tests python .github/dagger/tests/run_exhaustive_reliability_model.py",
    ])
    await ctr.sync()
    return {"hypothesis_version":HYPOTHESIS_VERSION,"property_suite":"passed"}

if __name__ == "__main__": stage("reliability-contract",run)
