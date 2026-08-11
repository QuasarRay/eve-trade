"""Simulator and observability Python verification stage."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import PYTHON_SLIM_IMAGE


async def run(dag) -> None:
    ctr = with_source(dag.container().from_(PYTHON_SLIM_IMAGE), dag)
    ctr = sh(
        ctr,
        [
            "python -m compileall simulator/eve_trade_simulator simulator/trade_gui distributed-backend/tests/e2e distributed-backend/observability",
            "python -m pip install --disable-pip-version-check -r simulator/requirements-test.txt",
            "cd simulator",
            "python -m coverage run --rcfile=.coveragerc manage.py test trade_gui",
            "python -m coverage report --rcfile=.coveragerc --fail-under=80",
            "cd /src",
            "python -m pip install --disable-pip-version-check -r distributed-backend/observability/requirements-test.txt",
            "PYTHONPATH=distributed-backend python -m coverage run --rcfile=distributed-backend/observability/.coveragerc -m unittest discover -s distributed-backend/observability/tests -v",
            "python -m coverage report --rcfile=distributed-backend/observability/.coveragerc --fail-under=80",
        ],
    )
    await ctr.sync()


if __name__ == "__main__":
    stage("python", run)
