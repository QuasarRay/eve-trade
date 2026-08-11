from __future__ import annotations

import contextlib
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable

import pytest


@dataclass(frozen=True)
class Snapshot:
    total_items: int
    total_isk: int
    trade_count: int
    item_escrow_count: int
    wallet_escrow_count: int
    wallet_ledger_count: int
    item_ledger_count: int
    state_change_count: int
    settlement_batch_count: int
    settlement_step_count: int
    settlement_operation_count: int
    outbox_count: int


class LiveAdapter:
    """Black-box adapter over the repository's own E2E helpers.

    The adapter deliberately imports the helpers from the checked-out repository
    rather than copying their protocol implementation. That makes these
    Hypothesis tests follow the same canonical simulator -> Quilkin -> gateway ->
    Market -> Pub/Sub -> Rust settlement path as the native E2E suite.
    """

    def __init__(self, root: Path, *, strict: bool):
        self.root = root.resolve()
        self.strict = strict
        self._helpers: ModuleType | None = None
        self._db = None
        self._gateway = None
        self._settlement = None
        self._edge = None
        self._lock = threading.RLock()

    @property
    def helpers(self) -> ModuleType:
        if self._helpers is None:
            self._helpers = self._load_helpers()
        return self._helpers

    def _load_helpers(self) -> ModuleType:
        path = self.root / "distributed-backend" / "tests" / "e2e" / "helpers.py"
        if not path.exists():
            self.unavailable(f"E2E helpers not found: {path}")
        e2e_dir = str(path.parent)
        if e2e_dir not in sys.path:
            sys.path.insert(0, e2e_dir)
        spec = importlib.util.spec_from_file_location("eve_trade_e2e_helpers_hypothesis", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot import E2E helpers from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def unavailable(self, reason: str) -> None:
        if self.strict:
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    def require_env(self, *names: str) -> dict[str, str]:
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            self.unavailable("missing live-test environment: " + ", ".join(missing))
        return {n: os.environ[n] for n in names}

    def require_basic(self) -> None:
        self.require_env(
            "EVE_TRADE_SIMULATOR_URL",
            "EVE_TRADE_DATABASE_URL",
        )

    @property
    def db(self):
        self.require_basic()
        if self._db is None:
            self._db = self.helpers.Database(os.environ["EVE_TRADE_DATABASE_URL"])
        return self._db

    @property
    def gateway(self):
        self.require_basic()
        if self._gateway is None:
            self._gateway = self.helpers.GatewayClient(os.environ["EVE_TRADE_SIMULATOR_URL"])
        return self._gateway

    @property
    def settlement(self):
        endpoint = self.require_env("EVE_TRADE_SETTLEMENT_GRPC")["EVE_TRADE_SETTLEMENT_GRPC"]
        if self._settlement is None:
            self._settlement = self.helpers.SettlementClient(endpoint)
        return self._settlement

    @property
    def edge(self):
        values = self.require_env(
            "EVE_TRADE_QUILKIN_UDP_HOST",
            "EVE_TRADE_EDGE_RESPONSE_SECRET",
            "EVE_TRADE_EDGE_RESPONSE_KEY_ID",
        )
        if self._edge is None:
            self._edge = self.helpers.AuthenticatedEdgeClient(
                values["EVE_TRADE_QUILKIN_UDP_HOST"],
                int(os.environ.get("EVE_TRADE_QUILKIN_UDP_PORT", "26001")),
                values["EVE_TRADE_EDGE_RESPONSE_SECRET"],
                values["EVE_TRADE_EDGE_RESPONSE_KEY_ID"],
            )
        return self._edge

    def reset_example(self) -> None:
        """Reset correctness-critical persistent state before each Hypothesis example."""
        with self._lock:
            self.db.reset()
            nsq_http = os.environ.get("EVE_TRADE_NSQ_HTTP")
            if nsq_http and hasattr(self.helpers, "wait_for_pubsub_idle"):
                self.helpers.wait_for_pubsub_idle(nsq_http)

    def close(self) -> None:
        for value in (self._edge, self._settlement, self._gateway, self._db):
            if value is not None:
                with contextlib.suppress(Exception):
                    value.close()
        self._edge = self._settlement = self._gateway = self._db = None

    def seed_world(self, **kwargs: Any):
        return self.helpers.seed_world(self.db, **kwargs)

    def create_trade(self, world: Any, **kwargs: Any):
        return self.helpers.create_trade(self.gateway, world, **kwargs)

    def issue_payload(self, world: Any, **kwargs: Any) -> dict[str, Any]:
        return self.helpers.issue_payload(world, **kwargs)

    def accept_payload(self, world: Any, trade: Any, **kwargs: Any) -> dict[str, Any]:
        return self.helpers.accept_payload(world, trade, **kwargs)

    def cancel_payload(self, world: Any, trade: Any, **kwargs: Any) -> dict[str, Any]:
        return self.helpers.cancel_payload(world, trade, **kwargs)

    def accept_trade(self, world: Any, trade: Any, **kwargs: Any) -> dict[str, Any]:
        return self.helpers.accept_trade(self.gateway, world, trade, **kwargs)

    def cancel_trade(self, world: Any, trade: Any, **kwargs: Any) -> dict[str, Any]:
        return self.helpers.cancel_trade(self.gateway, world, trade, **kwargs)

    def expect_rpc_failure(self, call: Callable[[], Any], *, code: str | None = None, contains: str | None = None):
        failure_type = self.helpers.RpcFailure
        try:
            call()
        except failure_type as exc:
            if code is not None:
                assert exc.code == code, f"RPC code {exc.code!r}, expected {code!r}"
            if contains is not None:
                assert contains.lower() in exc.message.lower(), (exc.message, contains)
            return exc
        raise AssertionError("expected request to fail")

    def table_count(self, table: str) -> int:
        return int(self.db.scalar(f"SELECT count(*) FROM {table}"))

    def table_exists(self, table: str) -> bool:
        return bool(self.db.scalar(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s)",
            (table,),
        ))

    def columns(self, table: str) -> set[str]:
        return {
            row["column_name"]
            for row in self.db.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                (table,),
            )
        }

    def rows(self, table: str, *, order_by: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        return self.db.fetchall(sql)

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self.db.scalar(sql, params)

    def snapshot(self) -> Snapshot:
        h = self.helpers
        def count(table: str) -> int:
            return self.table_count(table) if self.table_exists(table) else 0
        return Snapshot(
            total_items=h.total_item_quantity(self.db),
            total_isk=h.total_isk_amount(self.db),
            trade_count=count("trade_instance"),
            item_escrow_count=count("item_stack_escrow"),
            wallet_escrow_count=count("wallet_escrow"),
            wallet_ledger_count=count("wallet_ledger"),
            item_ledger_count=count("item_stack_ledger"),
            state_change_count=count("trade_state_change"),
            settlement_batch_count=count("settlement_batch"),
            settlement_step_count=count("settlement_step"),
            settlement_operation_count=count("settlement_operation"),
            outbox_count=count("settlement_outbox"),
        )

    def trade_row(self, trade: Any) -> dict[str, Any]:
        return self.helpers.trade_row(self.db, trade)

    def item_escrow_row(self, trade: Any) -> dict[str, Any]:
        return self.helpers.item_escrow_row(self.db, trade)

    def wallet_row(self, wallet_id: str) -> dict[str, Any]:
        return self.helpers.wallet_row(self.db, wallet_id)

    def item_stack_row(self, item_stack_id: str) -> dict[str, Any]:
        return self.helpers.item_stack_row(self.db, item_stack_id)

    def idempotency_row(self, key: str) -> dict[str, Any]:
        return self.helpers.idempotency_record_row(self.db, key)

    def batch_row(self, key: str) -> dict[str, Any]:
        return self.helpers.settlement_batch_row(self.db, key)

    def concurrent(self, functions: Iterable[Callable[[], Any]], *, max_workers: int | None = None) -> list[tuple[bool, Any]]:
        funcs = list(functions)
        if not funcs:
            return []
        results: list[tuple[bool, Any]] = []
        barrier = threading.Barrier(len(funcs))
        def wrapped(fn: Callable[[], Any]):
            barrier.wait(timeout=10)
            return fn()
        with ThreadPoolExecutor(max_workers=max_workers or len(funcs)) as pool:
            futures = [pool.submit(wrapped, fn) for fn in funcs]
            for f in futures:
                try:
                    results.append((True, f.result(timeout=60)))
                except Exception as exc:  # result is intentionally asserted by caller
                    results.append((False, exc))
        return results

    def edge_credentials(self, principal: str) -> tuple[str, str]:
        prefix = principal.upper()
        values = self.require_env(
            f"EVE_TRADE_EDGE_{prefix}_KEY_ID",
            f"EVE_TRADE_EDGE_{prefix}_SECRET",
        )
        return values[f"EVE_TRADE_EDGE_{prefix}_KEY_ID"], values[f"EVE_TRADE_EDGE_{prefix}_SECRET"]

    def canonical_edge_packet(self, world: Any, *, action: str = "market_place_sell_order", interaction_id: str | None = None) -> dict[str, Any]:
        interaction_id = interaction_id or self.helpers.fresh_key("hypothesis-edge")
        base: dict[str, Any] = {
            "schema_version": "eve-trade-gui.v1",
            "interaction_id": interaction_id,
            "ui": {"window": "regional_market", "action": action},
        }
        if action == "market_place_sell_order":
            base["input"] = {
                "issued_by_capsuleer_id": world.seller_id,
                "item_stack": {
                    "item_stack_id": world.seller_stack_id,
                    "owner_id": world.seller_id,
                    "item_type_id": world.item_type_id,
                    "station_id": world.station_id,
                    "quantity": 10,
                },
                "quantity": 1,
                "unit_price_isk": 1,
            }
        elif action == "market_buy_from_sell_order":
            base["input"] = {"buyer_capsuleer_id": world.buyer_id, "quantity": 1}
        elif action == "market_cancel_order":
            base["input"] = {"cancelled_by_capsuleer_id": world.seller_id}
        else:
            base["input"] = {}
        return base

    def signed_edge_envelope(self, packet: dict[str, Any], *, principal: str = "seller", key_id: str | None = None, secret: str | None = None, schema_version: str | None = None, algorithm: str | None = None) -> bytes:
        helpers = self.helpers
        if key_id is None or secret is None:
            default_key, default_secret = self.edge_credentials(principal)
            key_id = default_key if key_id is None else key_id
            secret = default_secret if secret is None else secret
        schema = schema_version or getattr(helpers, "EDGE_REQUEST_SCHEMA", "eve-trade-edge.v2")
        algo = algorithm or getattr(helpers, "HMAC_SHA256_ALGORITHM", "hmac-sha256")
        signing = helpers.envelope_signing_bytes(schema, algo, key_id, packet)
        signature = base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        return json.dumps(
            {
                "schema_version": schema,
                "payload": packet,
                "auth": {"algorithm": algo, "key_id": key_id, "signature": signature},
            },
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")

    def raw_udp(self, payload: bytes, *, timeout: float = 3.0, recv_size: int = 65535) -> bytes | None:
        values = self.require_env("EVE_TRADE_QUILKIN_UDP_HOST")
        endpoint = (
            values["EVE_TRADE_QUILKIN_UDP_HOST"],
            int(os.environ.get("EVE_TRADE_QUILKIN_UDP_PORT", "26001")),
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(timeout)
            udp.sendto(payload, endpoint)
            try:
                response, _ = udp.recvfrom(recv_size)
                return response
            except TimeoutError:
                return None
            except socket.timeout:
                return None

    def decoded_udp_response(self, payload: bytes, *, timeout: float = 3.0) -> dict[str, Any] | None:
        response = self.raw_udp(payload, timeout=timeout)
        if response is None:
            return None
        decoded = json.loads(response)
        if isinstance(decoded, dict) and isinstance(decoded.get("payload"), dict):
            return decoded["payload"]
        return decoded if isinstance(decoded, dict) else {"raw": decoded}
