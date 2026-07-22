from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "verify.yaml"


def workflow() -> dict[str, Any]:
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise AssertionError("verify workflow must parse to an object")
    return parsed


def release_job() -> dict[str, Any]:
    jobs = workflow().get("jobs", {})
    candidates = [value for key, value in jobs.items() if "release" in str(key).lower()]
    if len(candidates) != 1:
        raise AssertionError(f"expected one protected release job, found {len(candidates)}")
    return candidates[0]


def terraform_matrix() -> dict[str, dict[str, Any]]:
    job = workflow()["jobs"]["terraform"]
    rows = job["strategy"]["matrix"]["include"]
    return {str(row["provider"]): row for row in rows}


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    return [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(document, dict)]


def simulator_startup(overrides: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "eve_trade_simulator.settings",
            "SIMULATOR_ENVIRONMENT": "production",
            "SIMULATOR_SECRET_KEY": "production-secret-not-default",
            "SIMULATOR_DEBUG": "0",
            "SIMULATOR_ALLOWED_HOSTS": "simulator.example.invalid",
            "GAME_PACKET_HMAC_SECRET": "production-hmac-not-default",
        }
    )
    for key, value in overrides.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=ROOT / "simulator",
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def configured_socket_family(environment: dict[str, str]) -> int:
    script = r'''
import socket
from unittest.mock import patch
import django
django.setup()
from trade_gui.udp_client import _UdpSession
seen = []
class FakeSocket:
    def __init__(self, family, kind): seen.append(family)
    def settimeout(self, value): pass
    def close(self): pass
with patch("trade_gui.udp_client.socket.socket", FakeSocket):
    _UdpSession().get_socket()
print(seen[0])
'''
    merged = dict(os.environ)
    merged.update({"DJANGO_SETTINGS_MODULE": "eve_trade_simulator.settings", **environment})
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT / "simulator",
        env=merged,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return int(result.stdout.strip())


class WorkflowAndInfrastructureRegressionContracts(unittest.TestCase):
    def test_main_release_requires_successful_verification_for_exact_merge_sha(self) -> None:
        job = release_job()
        condition = str(job.get("if", ""))
        self.assertIn("github.sha", condition)
        self.assertRegex(condition, r"verified|verification")

    def test_main_release_cannot_use_source_branch_success_as_merge_sha_evidence(self) -> None:
        job = release_job()
        serialized = json.dumps(job, sort_keys=True)
        self.assertNotRegex(serialized, r"head_sha|pull_request\.head|source.*sha")
        self.assertRegex(serialized, r"github\.sha|merge.*sha")

    def test_main_release_waits_for_e2e_and_o11y_aggregate_completion(self) -> None:
        needs = release_job().get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        self.assertIn("e2e", needs)
        self.assertIn("o11y-aggregate", needs)

    def test_actions_upload_artifact_pin_declares_supported_runner_node_runtime(self) -> None:
        pins = re.findall(r"actions/upload-artifact@([0-9a-f]{40})", WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertTrue(pins)
        policy_path = ROOT / ".github" / "action-runtime-policy.json"
        self.assertTrue(policy_path.is_file(), "immutable action pins have no reviewed runner-runtime policy")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        for pin in pins:
            self.assertIn(pin, policy)
            self.assertIn(policy[pin]["runtime"], {"node20", "node24"})
            self.assertTrue(policy[pin]["supported_on_ubuntu_24_04"])

    def test_gke_terraform_init_uses_readonly_provider_lockfile(self) -> None:
        row = terraform_matrix()["gke"]
        self.assertEqual(row.get("lockfile_args"), "-lockfile=readonly")
        self.assertTrue((ROOT / row["root"] / ".terraform.lock.hcl").is_file())

    def test_all_terraform_targets_enforce_identical_provider_lock_policy(self) -> None:
        matrix = terraform_matrix()
        self.assertEqual(set(matrix), {"eks", "gke", "talos-omni"})
        self.assertEqual({row.get("lockfile_args") for row in matrix.values()}, {"-lockfile=readonly"})
        self.assertTrue(all((ROOT / row["root"] / ".terraform.lock.hcl").is_file() for row in matrix.values()))

    def test_udp_client_supports_configured_ipv4_only_contract(self) -> None:
        family = configured_socket_family({"QUILKIN_UDP_ADDRESS_FAMILY": "ipv4"})
        self.assertEqual(family, socket.AF_INET)

    def test_udp_client_supports_ipv6_when_dual_stack_is_enabled(self) -> None:
        family = configured_socket_family({"QUILKIN_UDP_ADDRESS_FAMILY": "dual", "QUILKIN_UDP_HOST": "::1"})
        self.assertEqual(family, socket.AF_INET6, "dual-stack configuration still creates an IPv4-only socket")

    def test_udp_client_resolves_and_connects_using_configured_address_family(self) -> None:
        ipv4 = configured_socket_family({"QUILKIN_UDP_ADDRESS_FAMILY": "ipv4"})
        ipv6 = configured_socket_family({"QUILKIN_UDP_ADDRESS_FAMILY": "ipv6", "QUILKIN_UDP_HOST": "::1"})
        self.assertEqual((ipv4, ipv6), (socket.AF_INET, socket.AF_INET6))

    def _assert_production_startup_rejected(self, environment: dict[str, str | None], setting: str) -> None:
        result = simulator_startup(environment)
        self.assertNotEqual(result.returncode, 0, f"production simulator accepted unsafe {setting}")
        self.assertIn(setting, result.stderr + result.stdout)

    def test_simulator_refuses_non_development_startup_with_default_secret(self) -> None:
        self._assert_production_startup_rejected({"SIMULATOR_SECRET_KEY": None}, "SIMULATOR_SECRET_KEY")

    def test_simulator_refuses_non_development_startup_with_debug_enabled(self) -> None:
        self._assert_production_startup_rejected({"SIMULATOR_DEBUG": "1"}, "SIMULATOR_DEBUG")

    def test_simulator_refuses_non_development_startup_with_wildcard_hosts(self) -> None:
        self._assert_production_startup_rejected({"SIMULATOR_ALLOWED_HOSTS": "*"}, "SIMULATOR_ALLOWED_HOSTS")

    def test_simulator_refuses_non_development_startup_with_default_hmac_key(self) -> None:
        self._assert_production_startup_rejected({"GAME_PACKET_HMAC_SECRET": None}, "GAME_PACKET_HMAC_SECRET")

    def test_nsq_connection_requires_authenticated_transport(self) -> None:
        config = json.loads((ROOT / "infra" / "encore" / "self-host.nsq.json").read_text(encoding="utf-8"))
        nsq = config["pubsub"][0]
        self.assertTrue(nsq.get("authentication", {}).get("required"))
        self.assertRegex(str(nsq.get("authentication", {}).get("credential_secret", "")), r"^[A-Z0-9_]+$")

    def test_nsq_connection_requires_tls_outside_local_development(self) -> None:
        config = json.loads((ROOT / "infra" / "encore" / "self-host.nsq.json").read_text(encoding="utf-8"))
        nsq = config["pubsub"][0]
        self.assertTrue(nsq.get("tls", {}).get("required"))
        nsq_manifest = (ROOT / "distributed-backend" / "orchestration" / "kubernetes" / "base" / "nsq.yaml").read_text(encoding="utf-8")
        self.assertIn("--tls-required=true", nsq_manifest)

    def _assert_nsq_policy_runtime_probe(self, role: str) -> None:
        policies = load_yaml_documents(
            ROOT / "distributed-backend" / "orchestration" / "kubernetes" / "overlay" / "prod" / "networkpolicies.yaml"
        )
        nsq = next(document for document in policies if document.get("metadata", {}).get("name") == "nsqd-ingress")
        allowed = nsq["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]
        self.assertEqual(allowed, {"app.kubernetes.io/name": "encore-backend"})
        e2e_script = (ROOT / "scripts" / "run_kind_e2e.sh").read_text(encoding="utf-8")
        marker = f"unauthorized-nsq-{role}"
        if marker not in e2e_script:
            self.fail(f"no ephemeral-cluster negative probe exists for unauthorized NSQ {role}")
        jobs = workflow().get("jobs", {})
        kind_steps = [
            step
            for job in jobs.values()
            for step in job.get("steps", [])
            if isinstance(step, dict)
            and "scripts/run_kind_e2e.sh"
            in str(step.get("run", "") or step.get("with", {}).get("command", ""))
        ]
        self.assertTrue(kind_steps, "protected verification never executes the ephemeral Kubernetes network-policy probes")

    def test_network_policy_blocks_unauthorized_nsq_publishers(self) -> None:
        self._assert_nsq_policy_runtime_probe("publisher")

    def test_network_policy_blocks_unauthorized_nsq_consumers(self) -> None:
        self._assert_nsq_policy_runtime_probe("consumer")


if __name__ == "__main__":
    unittest.main()
