from __future__ import annotations

import pytest

from eve_trade_hypothesis.config import SuiteConfig
from eve_trade_hypothesis.runtime import ContractRuntime


def pytest_addoption(parser):
    parser.addoption(
        "--eve-hypothesis-strict",
        action="store_true",
        default=False,
        help="fail rather than skip when a live service/tool/fault driver is unavailable",
    )


@pytest.fixture(scope="session")
def eve_suite_config(pytestconfig):
    config = SuiteConfig.from_env()
    if pytestconfig.getoption("--eve-hypothesis-strict") and not config.strict:
        config = SuiteConfig(
            repo_root=config.repo_root,
            strict=True,
            target_branch=config.target_branch,
            fault_driver=config.fault_driver,
            evidence_driver=config.evidence_driver,
            native_max_examples=config.native_max_examples,
            live_max_examples=config.live_max_examples,
            static_max_examples=config.static_max_examples,
            model_max_examples=config.model_max_examples,
        )
    return config


@pytest.fixture
def contract_runtime(eve_suite_config, request):
    runtime = ContractRuntime(eve_suite_config)
    request.addfinalizer(runtime.close)
    return runtime
