#!/usr/bin/env bash
set -euo pipefail

run_id="${EVE_TRADE_TEST_RUN_ID:-$(python -c 'import secrets; print("pt-" + secrets.token_hex(8))')}"
artifact_root="${EVE_TRADE_PROPERTY_ARTIFACTS:-${RUNNER_TEMP:-/tmp}/eve-trade-property-${run_id}}"
: "${EVE_TRADE_SCENARIO_INVOCATION:?EVE_TRADE_SCENARIO_INVOCATION is required}"
: "${EVE_TRADE_APP_NAMESPACE:?EVE_TRADE_APP_NAMESPACE is required}"

mkdir -p "$artifact_root"
export EVE_TRADE_TEST_RUN_ID="$run_id"

python distributed-backend/tests/property-tests/infra/orchestrate.py \
  --mode kind \
  --namespace "$EVE_TRADE_APP_NAMESPACE" \
  --run-id "$run_id" \
  --artifacts "$artifact_root" \
  --invocation "$EVE_TRADE_SCENARIO_INVOCATION"
