from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from agentinfra.law_runtime import resolve_law_layout
from build_traceability import apply_ledger, build, canonical_digest, inventory, project_root, record_evidence_digest, specification_roots
from constitutional_catalog import REQUIRED_CONSTITUTIONAL_LAW_NAMES, build_constitutional_catalog, evaluate_constitutional_catalog
from scenarios import FAMILIES, run_family
import test_specification as adapters

ROOT = project_root(HERE)


class TestLawSuiteMeta(unittest.TestCase):
    def test_constitutional_catalog_binds_exact_test_methods_not_module_aggregates(self):
        catalog = build_constitutional_catalog(ROOT)
        for law in catalog["laws"]:
            expected = {
                "source-assurance::"
                + f"{item['category']}-test-{Path(item['file']).stem}-{item['id']}"
                for item in law["property_tests"]
            }
            self.assertEqual(set(law["required_observations"]), expected)
            self.assertTrue(all("-module-" not in binding for binding in expected))

        observations = {
            binding: (True, "exact mapped test passed")
            for law in catalog["laws"]
            for binding in law["required_observations"]
        }
        target = catalog["laws"][0]
        skipped_binding = target["required_observations"][0]
        observations[skipped_binding] = (False, "exact mapped test was skipped")
        evaluated = evaluate_constitutional_catalog(catalog, observations)
        outcomes = {item["id"]: item["outcome"] for item in evaluated["laws"]}
        self.assertEqual(outcomes[target["id"]], "FAIL")

    def test_constitutional_outcomes_bind_to_executed_observations(self):
        catalog = build_constitutional_catalog(ROOT)
        labels = {
            binding
            for item in catalog["laws"]
            for binding in item["required_observations"]
        }
        observations = {label: (True, "isolated module passed") for label in labels}
        passed = evaluate_constitutional_catalog(catalog, observations)
        self.assertEqual(passed["counts"], {"PASS": 105})
        self.assertEqual(passed["outcome"], "PASS")
        failed_label = sorted(labels)[0]
        observations[failed_label] = (False, "seeded failure")
        failed = evaluate_constitutional_catalog(catalog, observations)
        self.assertEqual(failed["outcome"], "FAIL")
        self.assertGreater(failed["counts"].get("FAIL", 0), 0)
        self.assertTrue(all(item["started"] and item["completed"] for item in failed["laws"]))

    def test_constitutional_capability_skip_is_explicit_and_release_acceptable(self):
        from constitutional_catalog import constitutional_report_is_acceptable

        catalog = build_constitutional_catalog(ROOT)
        observations = {
            binding: (True, "exact mapped test passed")
            for law in catalog["laws"]
            for binding in law["required_observations"]
        }
        skipped_binding = catalog["laws"][0]["required_observations"][0]
        observations[skipped_binding] = (
            False,
            json.dumps(
                {
                    "outcome": "SKIP",
                    "capability_status": "UNAVAILABLE",
                    "detail": "host cannot provide the required filesystem capability",
                },
                sort_keys=True,
            ),
        )

        report = evaluate_constitutional_catalog(catalog, observations)

        affected = [
            law for law in report["laws"]
            if skipped_binding in law["required_observations"]
        ]
        self.assertTrue(affected)
        self.assertTrue(all(law["outcome"] == "UNAVAILABLE" for law in affected))
        self.assertNotIn("FAIL", report["counts"])
        self.assertEqual(report["outcome"], "PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS")
        self.assertTrue(constitutional_report_is_acceptable(report))
        report["outcome"] = "FAIL"
        self.assertFalse(constitutional_report_is_acceptable(report))

    def test_constitutional_catalog_is_exact_executable_and_property_subsumed(self):
        catalog = build_constitutional_catalog(ROOT)
        entries = catalog["laws"]
        self.assertEqual(len(REQUIRED_CONSTITUTIONAL_LAW_NAMES), 105)
        self.assertEqual(catalog["law_count"], 105)
        self.assertEqual([item["id"] for item in entries], list(REQUIRED_CONSTITUTIONAL_LAW_NAMES))
        self.assertEqual(len({item["id"] for item in entries}), 105)
        for item in entries:
            self.assertEqual(item["severity"], "HARD")
            self.assertTrue(item["invariants"])
            self.assertTrue(item["observation_boundary"])
            self.assertTrue(item["falsifier"])
            self.assertTrue(item["property_tests"])
            self.assertTrue(item["required_observations"])
            self.assertIn(item["tdd_mode"], {"RED_REQUIRED", "CHARACTERIZATION_REQUIRED"})
            self.assertEqual(item["red_required"], item["tdd_mode"] == "RED_REQUIRED")
            for binding in item["required_observations"]:
                family, label = binding.split("::", 1)
                self.assertEqual(family, "source-assurance")
                result = run_family(str(ROOT.resolve()), family)
                self.assertIn(label, {entry[0] for entry in result.observations})

    def test_law_template_examples_are_schema_valid_and_inert(self):
        layout = resolve_law_layout(ROOT)
        template_path = ROOT / "laws" / "template.toml" if layout.mode == "source" else ROOT / ".agents" / "laws" / "template.toml"
        with template_path.open("rb") as stream:
            template = tomllib.load(stream)
        ids = [item.get("id") for item in template.get("law", [])]
        self.assertEqual(ids, ["example.public_entrypoint_exists", "example.production_probe"])
        with (layout.law_definitions / "framework.toml").open("rb") as stream:
            defaults = {item["id"] for item in tomllib.load(stream).get("law", [])}
        self.assertTrue(set(ids).isdisjoint(defaults), "example template laws must not be acceptance-loaded")
        self.assertFalse((ROOT / "src" / "package.py").exists(), "template placeholder unexpectedly became a production oracle")

    def test_traceability_inventory_is_exact_bijection(self):
        entries, _ = inventory(ROOT)
        registry = build(ROOT)
        self.assertEqual(len(entries), 834)
        self.assertEqual(registry["requirement_count"], 834)
        self.assertEqual({item["name"] for item in entries}, {item["name"] for item in registry["requirements"]})

    def test_source_and_installed_specification_mirrors_are_exact(self):
        roots = specification_roots(ROOT)
        self.assertIn(len(roots), {1, 2})
        if len(roots) == 2:
            left = {path.name: path.read_bytes() for path in sorted(roots[0].glob("*.md"))}
            right = {path.name: path.read_bytes() for path in sorted(roots[1].glob("*.md"))}
            self.assertEqual(left, right)

    def test_every_proposed_requirement_has_exact_executable_adapter(self):
        registry = build(ROOT)
        proposed = [item for item in registry["requirements"] if item["source_file"][:2] not in {"00", "01", "02"}]
        missing = [item["name"] for item in proposed if not callable(getattr(adapters.TestSpecificationLaws, item["name"], None))]
        self.assertEqual(missing, [])

    def test_semantic_catalog_is_exact_nonvacuous_and_references_real_observations(self):
        registry = build(ROOT)
        seen_names = set()
        observations: dict[str, set[str]] = {}
        for requirement in registry["requirements"]:
            self.assertNotIn(requirement["name"], seen_names)
            seen_names.add(requirement["name"])
            bindings = requirement["required_observations"]
            self.assertTrue(bindings)
            self.assertEqual(len(bindings), len(set(bindings)))
            prefix = requirement["source_file"][:2]
            if prefix in {"00", "01", "02"}:
                expected_family = {"00": "outer-unit", "01": "outer-law", "02": "outer-template"}[prefix]
                self.assertEqual(bindings, [f"{expected_family}::{requirement['name']}"])
                continue
            for binding in bindings:
                family, label = binding.split("::", 1)
                self.assertIn(family, FAMILIES)
                if family not in observations:
                    result = run_family(str(ROOT.resolve()), family)
                    observations[family] = {item[0] for item in result.observations}
                self.assertIn(label, observations[family], f"{requirement['name']} -> {binding}")

    def test_every_semantic_family_has_battery_or_explicit_outer_boundary(self):
        registry = build(ROOT)
        scenarios = {item["scenario"] for item in registry["requirements"]}
        outer = {"legacy-python", "legacy-builtins", "law-template", "codex-live"}
        self.assertEqual(sorted(scenarios - set(FAMILIES) - outer), [])

    def test_traceability_artifact_matches_deterministic_builder(self):
        committed = json.loads((HERE / "traceability.json").read_text(encoding="utf-8"))
        rebuilt = build(ROOT)
        self.assertEqual(committed["inventory_sha256"], rebuilt["inventory_sha256"])
        self.assertEqual(committed["source_files"], rebuilt["source_files"])
        self.assertEqual(committed["semantic_catalog_sha256"], rebuilt["semantic_catalog_sha256"])
        self.assertEqual(
            [
                (item["name"], item["required_capability"], item["required_observations"], item["oracle"])
                for item in committed["requirements"]
            ],
            [
                (item["name"], item["required_capability"], item["required_observations"], item["oracle"])
                for item in rebuilt["requirements"]
            ],
        )
        self.assertEqual([item["name"] for item in committed["requirements"]], [item["name"] for item in rebuilt["requirements"]])

    def test_traceability_rejects_invalid_top_level_run_and_evidence_seal(self):
        requirement={
            "name":"test_one","source_file":"99_test.md","source_line":1,
            "required_capability":"portable","required_observations":["law-runner::one"],
            "status":"MISSING",
        }
        record={
            "started":True,"finished":True,"outcome":"PASS","capability_status":"AVAILABLE",
            "required_capability":"portable","required_observations":["law-runner::one"],
            "oracle_count":1,"detail":"executed",
        }
        record["evidence_digest"]=record_evidence_digest(record)
        inventory_digest=canonical_digest([{"source_file":"99_test.md","source_line":1,"name":"test_one"}])
        ledger={
            "schema":2,"suite":"aegis-comprehensive-laws","started_and_finished":True,
            "requirement_count":1,"inventory_sha256":inventory_digest,"integrity_errors":[],
            "definition_digests_before":{"test":"a"},"definition_digests_after":{"test":"a"},
            "requirements":{"test_one":record},"counts":{"PASS":1},"capabilities":{"AVAILABLE":1},
            "outcome":"PASS",
        }
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"ledger.json"
            for mutate,error in (
                (lambda value:value.update(outcome="FAIL"),"top-level outcome"),
                (lambda value:value.update(started_and_finished=False),"did not start and finish"),
                (lambda value:value.update(integrity_errors=["forged"]),"integrity errors"),
                (lambda value:value["requirements"]["test_one"].update(evidence_digest="0"*64),"evidence seal"),
            ):
                candidate=json.loads(json.dumps(ledger));mutate(candidate);path.write_text(json.dumps(candidate),encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError,error):
                    apply_ledger([dict(requirement)],path)


if __name__ == "__main__":
    unittest.main()
