#!/usr/bin/env bash
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG must point to the explicitly supplied disposable-cluster config}"
: "${EVE_TRADE_DISPOSABLE_CONTEXT:?set to the exact disposable kubectl current-context}"

run_id="${EVE_TRADE_TEST_RUN_ID:-$(python -c 'import secrets; print("pt-" + secrets.token_hex(8))')}"
artifact_root="${EVE_TRADE_PROPERTY_ARTIFACTS:-${RUNNER_TEMP:-/tmp}/eve-trade-property-${run_id}}"
namespace="${EVE_TRADE_APP_NAMESPACE:-eve-trade}"
: "${EVE_TRADE_SCENARIO_INVOCATION:?EVE_TRADE_SCENARIO_INVOCATION is required}"

mkdir -p "$artifact_root"
export EVE_TRADE_TEST_RUN_ID="$run_id"

python distributed-backend/tests/property-tests/infra/orchestrate.py \
  --mode supplied \
  --namespace "$namespace" \
  --run-id "$run_id" \
  --artifacts "$artifact_root" \
  --invocation "$EVE_TRADE_SCENARIO_INVOCATION" \
  --confirm-disposable-context "$EVE_TRADE_DISPOSABLE_CONTEXT"
