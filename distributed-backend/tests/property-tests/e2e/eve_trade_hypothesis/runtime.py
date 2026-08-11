from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SuiteConfig
from .external import ExternalContractDriver
from .live import LiveAdapter
from .repo import RepoInspector


@dataclass
class ContractRuntime:
    config: SuiteConfig

    def __post_init__(self) -> None:
        self.repo = RepoInspector(self.config.repo_root, strict=self.config.strict)
        self.live = LiveAdapter(self.config.repo_root, strict=self.config.strict)
        self.fault = ExternalContractDriver(
            self.config.fault_driver,
            repo_root=self.config.repo_root,
            strict=self.config.strict,
            role="fault",
            run_id=self.config.run_id,
        )
        self.evidence = ExternalContractDriver(
            self.config.evidence_driver,
            repo_root=self.config.repo_root,
            strict=self.config.strict,
            role="evidence",
            run_id=self.config.run_id,
        )

    def close(self) -> None:
        self.live.close()

    def case_dict(self, **values: Any) -> dict[str, Any]:
        return values
