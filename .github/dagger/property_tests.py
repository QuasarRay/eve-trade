"""Deterministic-emulation catalog, controller, and explicit-test structural gate."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import PYTHON_BOOKWORM_IMAGE, RUST_IMAGE


async def run(dag):
    python = with_source(dag.container().from_(PYTHON_BOOKWORM_IMAGE), dag)
    python = sh(
        python,
        [
            "python -m pip install --disable-pip-version-check "
            "-r distributed-backend/tests/property-tests/e2e/requirements.txt "
            "-r distributed-backend/tests/property-tests/infra/requirements.txt",
            "python -m compileall -q "
            "distributed-backend/tests/property-tests/e2e "
            "distributed-backend/tests/property-tests/reusable-oracles "
            "distributed-backend/tests/property-tests/infra/emulated-scenarios/runtime "
            "distributed-backend/tests/property-tests/infra/orchestrate.py "
            "distributed-backend/tests/property-tests/infra/install_litmus.py",
            "python distributed-backend/tests/property-tests/infra/emulated-scenarios/runtime/generate_manifests.py --check",
            "python distributed-backend/tests/property-tests/infra/emulated-scenarios/runtime/validate.py",
            "python -m pytest --collect-only -q "
            "-c distributed-backend/tests/property-tests/e2e/pytest.ini "
            "distributed-backend/tests/property-tests/e2e",
        ],
    )
    await python.sync()

    controller = with_source(dag.container().from_(RUST_IMAGE), dag).with_workdir(
        "/src/distributed-backend/tests/property-tests/infra/"
        "emulated-scenarios/runtime/deterministic-controller"
    )
    controller = sh(controller, ["cargo check"])
    await controller.sync()
    return {
        "catalog_validator": "passed",
        "explicit_business_test_collection": "passed",
        "anysystem_controller_compile": "passed",
    }


if __name__ == "__main__":
    stage("deterministic-emulation", run)
