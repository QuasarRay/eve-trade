#!/usr/bin/env bash
set -euo pipefail

cluster_name="${EVE_TRADE_KIND_CLUSTER:-eve-trade-ci}"
namespace="eve-trade"
created_cluster=0
forward_pids=()
log_root="${RUNNER_TEMP:-/tmp}/eve-trade-kind-e2e"
mkdir -p "$log_root"

cleanup() {
  status=$?
  trap - EXIT
  set +e
  if (( status != 0 )); then
    kubectl -n "$namespace" get pods,svc,jobs -o wide
    kubectl -n "$namespace" get events --sort-by=.lastTimestamp | tail -n 100
    kubectl -n "$namespace" logs deployment/postgres --all-containers --tail=200
    kubectl -n "$namespace" logs deployment/postgres --all-containers --previous --tail=200
    kubectl -n "$namespace" logs job/settlement-db-migrate --all-containers --tail=200
    kubectl -n "$namespace" logs job/local-dev-world-seed --all-containers --tail=200
    kubectl -n "$namespace" logs deployment/encore-backend --all-containers --tail=200
    kubectl -n "$namespace" logs deployment/trade-settlement --all-containers --tail=200
    kubectl -n "$namespace" logs deployment/quilkin --all-containers --tail=200
  fi
  if ((${#forward_pids[@]})); then
    kill "${forward_pids[@]}" 2>/dev/null
    wait "${forward_pids[@]}" 2>/dev/null
  fi
  if (( created_cluster == 1 )) && [[ "${EVE_TRADE_KEEP_KIND:-0}" != "1" ]]; then
    kind delete cluster --name "$cluster_name"
  fi
  exit "$status"
}
trap cleanup EXIT

for executable in docker kind kubectl encore python curl; do
  command -v "$executable" >/dev/null || {
    echo "required executable is missing: $executable" >&2
    exit 127
  }
done

if ! kind get clusters | grep -Fxq "$cluster_name"; then
  kind create cluster --name "$cluster_name" --config .github/kind-e2e.yaml --wait 120s
  created_cluster=1
fi

if [[ "${EVE_TRADE_E2E_SKIP_BUILD:-0}" != "1" ]]; then
  GOFLAGS=-mod=mod encore build docker --config infra/encore/self-host.nsq.json eve-trade/encore-backend:dev
  docker build -f distributed-backend/docker/trade-settlement.Dockerfile -t eve-trade/trade-settlement:dev .
  docker build -f distributed-backend/docker/quilkin.Dockerfile -t eve-trade/quilkin:dev .
  docker build -f simulator/Dockerfile -t eve-trade/simulator:dev .
fi

kind load docker-image --name "$cluster_name" \
  eve-trade/encore-backend:dev \
  eve-trade/trade-settlement:dev \
  eve-trade/quilkin:dev \
  eve-trade/simulator:dev

kubectl apply -k distributed-backend/orchestration/kubernetes/overlay/local
kubectl -n "$namespace" patch service quilkin --type merge --patch \
  '{"spec":{"type":"NodePort","ports":[{"name":"udp","port":26001,"targetPort":"udp","protocol":"UDP","nodePort":32601}]}}'

kubectl -n "$namespace" wait --for=condition=complete job/settlement-db-migrate --timeout=300s
kubectl -n "$namespace" wait --for=condition=complete job/local-dev-world-seed --timeout=300s
for deployment in postgres trade-settlement encore-backend quilkin simulator; do
  kubectl -n "$namespace" rollout status "deployment/$deployment" --timeout=300s
done
kubectl -n "$namespace" rollout status statefulset/nsqd --timeout=300s

kubectl -n "$namespace" port-forward service/encore-backend 14000:4000 >"$log_root/encore-forward.log" 2>&1 &
forward_pids+=("$!")
kubectl -n "$namespace" port-forward service/simulator 18000:8000 >"$log_root/simulator-forward.log" 2>&1 &
forward_pids+=("$!")
kubectl -n "$namespace" port-forward service/trade-settlement 19092:9092 >"$log_root/settlement-forward.log" 2>&1 &
forward_pids+=("$!")
kubectl -n "$namespace" port-forward service/nsqd 14150:4150 >"$log_root/nsqd-forward.log" 2>&1 &
forward_pids+=("$!")
kubectl -n "$namespace" port-forward service/nsqd 14151:4151 >"$log_root/nsqd-http-forward.log" 2>&1 &
forward_pids+=("$!")
kubectl -n "$namespace" port-forward service/postgres 15432:5432 >"$log_root/postgres-forward.log" 2>&1 &
forward_pids+=("$!")

curl --fail --silent --show-error --retry 60 --retry-all-errors --retry-delay 2 \
  http://127.0.0.1:14000/gateway/readyz >/dev/null
curl --fail --silent --show-error --retry 60 --retry-all-errors --retry-delay 2 \
  http://127.0.0.1:18000/api/gui/buttons/ >/dev/null

export EVE_TRADE_E2E_PRODUCTION_GATE=1
export EVE_TRADE_ENCORE_URL=http://127.0.0.1:14000
export EVE_TRADE_SIMULATOR_URL=http://127.0.0.1:18000
export EVE_TRADE_SETTLEMENT_GRPC=127.0.0.1:19092
export EVE_TRADE_NSQ_TCP=127.0.0.1:14150
export EVE_TRADE_NSQ_HTTP=http://127.0.0.1:14151
export EVE_TRADE_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:15432/eve_trade
export EVE_TRADE_MARKET_DATABASE_URL=postgres://eve_trade_market_readonly:market-readonly-password@127.0.0.1:15432/eve_trade
export EVE_TRADE_RUNTIME_DATABASE_URL=postgres://eve_trade_runtime:runtime-password@127.0.0.1:15432/eve_trade
export EVE_TRADE_QUILKIN_UDP_HOST=127.0.0.1
export EVE_TRADE_QUILKIN_UDP_PORT=26001
export EVE_TRADE_EDGE_RESPONSE_KEY_ID=primary
export EVE_TRADE_EDGE_RESPONSE_SECRET=local-game-edge-secret
export EVE_TRADE_EDGE_SELLER_KEY_ID=seller
export EVE_TRADE_EDGE_SELLER_SECRET=seller-player-secret
export EVE_TRADE_EDGE_BUYER_KEY_ID=buyer
export EVE_TRADE_EDGE_BUYER_SECRET=buyer-player-secret
export EVE_TRADE_EDGE_OTHER_KEY_ID=other
export EVE_TRADE_EDGE_OTHER_SECRET=other-player-secret

python distributed-backend/observability/ci/observed_run.py integration --strict
