"""Architecture boundaries and schema ownership stage."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import PYTHON_SLIM_IMAGE


async def run(dag) -> None:
    ctr = with_source(dag.container().from_(PYTHON_SLIM_IMAGE), dag)
    ctr = sh(
        ctr,
        [
            "python -m pip install --disable-pip-version-check 'PyYAML==6.0.3'",
            "python scripts/verify_architecture_boundaries.py",
            "python scripts/verify_schema_ownership.py",
            "python -m unittest -v scripts.tests.test_verify_architecture_boundaries",
        ],
    )
    await ctr.sync()


if __name__ == "__main__":
    stage("architecture", run)
