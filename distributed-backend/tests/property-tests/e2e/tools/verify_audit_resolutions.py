#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eve_trade_hypothesis.catalog import proposed_names  # noqa: E402
from eve_trade_hypothesis.evidence_specs import build_evidence_spec  # noqa: E402
from eve_trade_hypothesis.catalog import load_catalog  # noqa: E402
from eve_trade_hypothesis.semantic_overrides import (  # noqa: E402
    SEMANTIC_EVIDENCE_OVERRIDES,
    SEMANTIC_OVERRIDE_FINDINGS,
)


def main() -> int:
    manifest = json.loads((ROOT / 'audit_resolution_manifest.json').read_text(encoding='utf-8'))
    errors: list[str] = []
    proposed = proposed_names()

    direct_expected: set[str] = set()
    strategy_fixed: set[str] = set()
    evidence_fixed = 0
    fault_fixed = 0
    for record in manifest['records']:
        name = record['name']
        if name not in proposed:
            errors.append(f'audit resolution name absent from fixed catalog: {name}')
        for finding in record['findings']:
            method = finding['resolution']
            if method == 'audited_semantic_override_with_raw_observation_validation':
                direct_expected.add(name)
            elif method == 'category_specific_strategy_precedence_fixed':
                strategy_fixed.add(name)
            elif method == 'protocol_v2_contract_evidence_spec':
                evidence_fixed += 1
            elif method == 'protocol_v2_fault_evidence_spec':
                fault_fixed += 1

    if not direct_expected <= set(SEMANTIC_EVIDENCE_OVERRIDES):
        missing = sorted(direct_expected - set(SEMANTIC_EVIDENCE_OVERRIDES))
        errors.append(
            f'first-audit semantic overrides missing from code: {len(missing)} {missing[:10]}'
        )

    catalog = load_catalog()
    for category_id, category in catalog['categories'].items():
        for name in category['names']:
            try:
                spec = build_evidence_spec(int(category_id), name)
            except Exception as exc:  # noqa: BLE001
                errors.append(f'{name}: evidence spec failed to compile: {exc}')
                continue
            if len(spec.predicates) < 4:
                errors.append(f'{name}: evidence spec lacks semantic predicate')

    # The old external trust-oracle implementation must be gone.
    external_source = (ROOT / 'eve_trade_hypothesis' / 'external.py').read_text(encoding='utf-8')
    if 'validate_audited_semantics(contract_name, result)' not in external_source:
        errors.append('external protocol does not invoke audited semantic validation')
    if 'case_sha256' not in external_source or 'protocol_version = 2' not in external_source:
        errors.append('external protocol is not bound to protocol-v2 exact-case evidence')

    # Existing branch-only tests must skip as not-applicable, not fail strict mode.
    engine_source = (ROOT / 'eve_trade_hypothesis' / 'engine.py').read_text(encoding='utf-8')
    branch_block = engine_source[engine_source.index('if not native_file:'):]
    if 'pytest.skip' not in branch_block.split('repo =', 1)[0]:
        errors.append('branch-only existing wrapper does not skip when absent')

    # No hidden UUID4 generation remains inside contract implementations.
    for path in (ROOT / 'eve_trade_hypothesis' / 'contracts').glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'uuid4':
                errors.append(f'uncontrolled uuid4 call remains: {path.name}:{node.lineno}')

    # Strategy regression: the named invalid sequence must construct an `invalid`
    # branch before generic quantity/ISK token matching.  Verify source shape without
    # requiring Hypothesis to be installed for this structural tool.
    strategy_source = (ROOT / 'eve_trade_hypothesis' / 'strategies.py').read_text(encoding='utf-8')
    if strategy_source.index('if category in {32, 33, 83}') > strategy_source.index('if any(token in name for token in ("quantity"'):
        errors.append('state-machine strategy branch again appears after generic numeric token branch')
    if '"invalid_settlement_operation_sequence" in name' not in strategy_source:
        errors.append('invalid settlement sequence strategy no longer guarantees an invalid operation')

    if errors:
        print('AUDIT RESOLUTION VERIFICATION FAILED', file=sys.stderr)
        for error in errors:
            print(f'- {error}', file=sys.stderr)
        return 1

    print(f'audit records checked: {len(manifest["records"])}')
    print(f'first-audit semantic overrides: {len(direct_expected)}')
    print(f'total semantic overrides after second pass: {len(SEMANTIC_EVIDENCE_OVERRIDES)}')
    print(f'missing-contract findings closed by v2 evidence: {evidence_fixed}')
    print(f'missing-fault findings closed by v2 evidence: {fault_fixed}')
    print(f'strategy-precedence findings closed: {len(strategy_fixed)}')
    print('audit resolution verification: complete')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
