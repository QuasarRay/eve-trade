from __future__ import annotations

import importlib
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LAYOUT = FRAMEWORK_ROOT.name == ".agents"
ROOT = FRAMEWORK_ROOT.parent if DEPLOYMENT_LAYOUT else FRAMEWORK_ROOT
INFRA = FRAMEWORK_ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))


class HistoricalSemanticIntegrityTests(unittest.TestCase):
    def _implementation(self):
        try:
            return importlib.import_module("law_tests.historical_integrity")
        except ModuleNotFoundError:
            self.fail(
                "historical universal claims have no execution-backed integrity boundary"
            )

    def _campaign(self):
        try:
            return importlib.import_module("law_tests.historical_campaign")
        except ModuleNotFoundError:
            self.fail("historical execution campaign is unavailable")

    def test_canonical_universes_reject_proper_subsets_and_metadata_only_proof(self) -> None:
        integrity = self._implementation()
        universes = integrity.canonical_universes(ROOT)

        self.assertEqual(set(universes), set(integrity.REQUIRED_UNIVERSES))
        for name, universe in universes.items():
            with self.subTest(universe=name):
                self.assertTrue(universe["members"])
                self.assertEqual(len(universe["members"]), len(set(universe["members"])))
                self.assertRegex(universe["digest"], r"^[0-9a-f]{64}$")

        v4 = universes["historical_v4_flaws"]["members"]
        metadata_only = [
            {
                "flaw_id": flaw_id,
                "oracle_id": "named-regression",
                "seeded_probe": "metadata-is-not-execution",
                "regression": "metadata-is-not-execution",
            }
            for flaw_id in v4
        ]
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.validate_v4_campaign(ROOT, metadata_only)

        mutation_targets = universes["required_mutation_targets"]["members"]
        complete = {
            target: {
                "target_id": target,
                "status": "KILLED",
                "proof_digest": "1" * 64,
            }
            for target in mutation_targets
        }
        missing = dict(complete)
        missing.pop(mutation_targets[-1])
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.score_hard_mutations(ROOT, missing)
        score = integrity.score_hard_mutations(ROOT, complete)
        self.assertEqual(score["required_targets"], len(mutation_targets))
        self.assertEqual(score["killed_mutants"], len(mutation_targets))
        self.assertEqual(score["missing_targets"], [])
        self.assertEqual(score["effective_completeness"], 1.0)

    @unittest.skipIf(
        DEPLOYMENT_LAYOUT,
        "the source-only clean-release campaign must not recursively invoke itself from deployed self-tests",
    )
    def test_clean_release_sequence_runs_current_deployed_laws(self) -> None:
        campaign = self._campaign()
        with tempfile.TemporaryDirectory(prefix="historical-release-contract-", dir=ROOT) as directory:
            fixture = Path(directory) / "fixture"
            campaign._deployment(ROOT, fixture)
            try:
                release = campaign._release_sequence(fixture)
            except campaign.CampaignError as exc:
                self.fail(f"clean deployed assurance sequence failed: {exc}")
        self.assertEqual(release["status"], "PASS")
        phases = {record["phase"]: record for record in release["phases"]}
        deployed = phases["deployed-law-suite"]
        self.assertEqual(deployed["returncode"], 0)
        self.assertFalse(deployed["timed_out"])

    @unittest.skipIf(
        DEPLOYMENT_LAYOUT,
        "the source-only clean-release campaign must not recursively invoke itself from deployed self-tests",
    )
    def test_historical_campaign_is_execution_backed_and_exact(self) -> None:
        integrity = self._implementation()
        try:
            report = integrity.run_historical_integrity_campaign(ROOT)
        except integrity.HistoricalIntegrityError as exc:
            self.fail(str(exc))
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["status"], "PASS")

        universes = integrity.canonical_universes(ROOT)
        required_flaws = universes["historical_v4_flaws"]["members"]
        records = report["v4_flaw_campaign"]["records"]
        self.assertEqual([record["flaw_id"] for record in records], required_flaws)
        self.assertEqual(report["v4_flaw_campaign"]["required"], len(required_flaws))
        self.assertEqual(report["v4_flaw_campaign"]["seeded_red"], len(required_flaws))
        self.assertEqual(report["v4_flaw_campaign"]["fixed_green"], len(required_flaws))
        self.assertEqual(report["v4_flaw_campaign"]["missing"], [])
        for record in records:
            with self.subTest(flaw=record["flaw_id"]):
                self.assertEqual(record["seeded"]["outcome"], "RED")
                self.assertEqual(record["fixed"]["outcome"], "GREEN")
                self.assertEqual(record["seeded"]["oracle_id"], record["fixed"]["oracle_id"])
                self.assertNotEqual(
                    record["seeded"]["production_digest"],
                    record["fixed"]["production_digest"],
                )
                for phase in ("seeded", "fixed"):
                    observation = record[phase]
                    self.assertTrue(observation["argv"])
                    self.assertIsInstance(observation["returncode"], int)
                    self.assertRegex(observation["stdout_sha256"], r"^[0-9a-f]{64}$")
                    self.assertRegex(observation["stderr_sha256"], r"^[0-9a-f]{64}$")
                    self.assertRegex(observation["evidence_digest"], r"^[0-9a-f]{64}$")

        mutation = report["hard_mutation_campaign"]
        self.assertEqual(
            mutation["required_targets"],
            len(universes["required_mutation_targets"]["members"]),
        )
        self.assertEqual(mutation["missing_targets"], [])
        self.assertEqual(mutation["effective_completeness"], 1.0)

        hard = report["hard_invariant_campaign"]
        self.assertEqual(
            hard["required_targets"],
            len(universes["hard_invariants"]["members"]),
        )
        self.assertEqual(hard["missing_targets"], [])
        self.assertEqual(hard["effective_completeness"], 1.0)

        semantic = report["semantic_mapping_audit"]
        self.assertEqual(semantic["reviewed"], semantic["required"])
        self.assertEqual(semantic["weaker"], [])
        self.assertEqual(
            set(semantic["records"]),
            set(semantic["elevated_requirement_names"]),
        )
        json.dumps(report, sort_keys=True)

    def test_every_hard_policy_invariant_is_independently_rejected_when_removed(self) -> None:
        integrity = self._implementation()
        from agentinfra import policy

        validator = getattr(policy, "validate_compiled_policy", None)
        if not callable(validator):
            self.fail("compiled policy has no independent HARD-invariant validation boundary")

        reference = {
            "id": "reference",
            "path": "reference.txt",
            "revision": "v1",
            "sha256": "1" * 64,
            "read_only": True,
            "command": ["python", "reference.py"],
            "observation": "stdout",
            "normalization": "identity",
            "permitted_divergence": [],
        }

        def contract(*, packs=(), references=()):
            return {
                "schema": 1,
                "project": {"name": "hard-universe"},
                "policy": {"packs": list(packs)},
                "boundaries": {
                    "source": ["src/**"],
                    "generated": [],
                    "immutable": [],
                    "vendor": [],
                },
                "references": list(references),
            }

        variants = (
            policy.compile_contract(
                contract(packs=sorted(policy.PACKS), references=(reference,)),
                declared_classes=sorted(policy.TASK_CLASSES),
                changed_paths=("src/app.py",),
                risk="critical",
            ),
            policy.compile_contract(
                contract(),
                declared_classes=("REFACTOR",),
                changed_paths=("src/app.py",),
                risk="high",
            ),
            policy.compile_contract(
                contract(),
                declared_classes=("DOCUMENTATION",),
                changed_paths=("README.md",),
                risk="low",
            ),
        )
        expected = integrity.canonical_universes(ROOT)["hard_invariants"]["members"]
        by_gate = {
            gate["id"]: compiled
            for compiled in variants
            for gate in compiled["gates"]
            if gate["severity"] == "HARD"
        }
        self.assertEqual(set(by_gate), set(expected))
        for gate_id in expected:
            with self.subTest(gate=gate_id):
                validator(by_gate[gate_id])
                mutant = copy.deepcopy(by_gate[gate_id])
                mutant["gates"] = [gate for gate in mutant["gates"] if gate["id"] != gate_id]
                body = {key: value for key, value in mutant.items() if key != "digest"}
                mutant["digest"] = hashlib.sha256(
                    json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
                ).hexdigest()
                with self.assertRaises(policy.PolicyError):
                    validator(mutant)

    def test_actual_process_campaign_uses_current_evidence_state_and_lease_apis(self) -> None:
        integrity = self._implementation()
        report = integrity.run_actual_process_campaign()
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["actual_processes"])
        self.assertEqual(
            [item["outcome"] for item in report["evidence_race"]["observations"]],
            ["APPENDED", "APPENDED"],
        )
        self.assertEqual(
            sorted(item["outcome"] for item in report["state_revision_race"]["observations"]),
            ["COMMITTED", "REJECTED"],
        )
        self.assertEqual(
            sorted(item["outcome"] for item in report["lease_race"]["observations"]),
            ["ACQUIRED", "REJECTED"],
        )
        for family in ("evidence_race", "state_revision_race", "lease_race"):
            pids = [item["pid"] for item in report[family]["processes"]]
            self.assertEqual(len(pids), len(set(pids)))
            self.assertTrue(all(pid > 0 for pid in pids))


if __name__ == "__main__":
    unittest.main()
