#!/usr/bin/env bash
set -euo pipefail

run_id="${EVE_TRADE_TEST_RUN_ID:-$(python -c 'import secrets; print("pt-" + secrets.token_hex(8))')}"
artifact_root="${EVE_TRADE_PROPERTY_ARTIFACTS:-${RUNNER_TEMP:-/tmp}/eve-trade-property-${run_id}}"
suite="${EVE_TRADE_PROPERTY_SUITE:-implemented}"

mkdir -p "$artifact_root"
export EVE_TRADE_TEST_RUN_ID="$run_id"

python distributed-backend/tests/property-tests/infra/orchestrate.py \
  --mode kind \
  --namespace eve-trade \
  --run-id "$run_id" \
  --artifacts "$artifact_root" \
  --suite "$suite" \
  --chaos-examples "${EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES:-3}"

