from __future__ import annotations

import datetime
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class TrivyExceptionTests(unittest.TestCase):
    def test_only_documented_path_specific_findings_are_exempt(self) -> None:
        exceptions = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text(encoding="utf-8"))

        self.assertEqual(
            exceptions,
            {
                "misconfigurations": [
                    {
                        "id": "GCP-0015",
                        "paths": ["distributed-backend/terraform/gke/runtime.tf"],
                        "expired_at": datetime.date(2027, 1, 1),
                        "statement": "Cloud SQL enforces ssl_mode ENCRYPTED_ONLY and generated clients require sslmode=require; this check only recognizes deprecated require_ssl.",
                    },
                    {
                        "id": "AVD-DS-0002",
                        "paths": ["vendor/go.opentelemetry.io/otel/dependencies.Dockerfile"],
                        "expired_at": datetime.date(2027, 1, 1),
                        "statement": "This vendored OpenTelemetry Renovate metadata lists source images only and is never built or deployed.",
                    },
                    {
                        "id": "KSV-0014",
                        "paths": [
                            "distributed-backend/orchestration/kubernetes/overlay/local/nsq-local.yaml"
                        ],
                        "expired_at": datetime.date(2027, 1, 1),
                        "statement": "This strategic-merge patch inherits the NSQ container readOnlyRootFilesystem setting from base/nsq.yaml; the fully rendered local manifest is verified separately.",
                    },
                    {
                        "id": "KSV-0118",
                        "paths": [
                            "distributed-backend/orchestration/kubernetes/overlay/local/nsq-local.yaml",
                            "distributed-backend/orchestration/kubernetes/overlay/prod/patches/nsq.yaml",
                        ],
                        "expired_at": datetime.date(2027, 1, 1),
                        "statement": "These strategic-merge patches inherit NSQ pod and container security contexts from base/nsq.yaml; fully rendered local and production manifests are verified separately.",
                    },
                ]
            },
        )


if __name__ == "__main__":
    unittest.main()
