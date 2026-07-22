from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import udp_client


class ControlledSocket:
    created = 0
    closed = 0
    instances: list["ControlledSocket"] = []

    def __init__(
        self,
        family: int = socket.AF_INET,
        _kind: int = socket.SOCK_DGRAM,
        *,
        script: list[Any] | None = None,
    ):
        type(self).created += 1
        type(self).instances.append(self)
        self.script = list(script or [])
        self.family = family
        self.timeout: float | None = None
        self.timeout_history: list[float] = []
        self.sent: list[tuple[bytes, tuple[Any, ...]]] = []
        self.is_closed = False

    @classmethod
    def reset_counts(cls) -> None:
        cls.created = 0
        cls.closed = 0
        cls.instances = []

    def close(self) -> None:
        if not self.is_closed:
            type(self).closed += 1
            self.is_closed = True

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout
        self.timeout_history.append(timeout)

    def sendto(self, payload: bytes, address: tuple[Any, ...]) -> int:
        if self.is_closed:
            raise OSError("socket is closed")
        self.sent.append((payload, address))
        return len(payload)

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.script:
            raise socket.timeout("controlled socket script exhausted")
        value = self.script.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class SocketFactory:
    def __init__(self, scripts: list[list[Any]]):
        self.scripts = list(scripts)

    def __call__(self, family: int = socket.AF_INET, _kind: int = socket.SOCK_DGRAM) -> ControlledSocket:
        script = self.scripts.pop(0) if self.scripts else []
        return ControlledSocket(family, _kind, script=script)


def signed_response(interaction_id: str, **values: Any) -> bytes:
    payload = {"interaction_id": interaction_id, **values}
    canonical = udp_client.response_signing_bytes(
        udp_client.EDGE_RESPONSE_SCHEMA, "primary", payload
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(b"edge-secret", canonical, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return json.dumps(
        {
            "schema_version": udp_client.EDGE_RESPONSE_SCHEMA,
            "payload": payload,
            "auth": {
                "algorithm": udp_client.HMAC_SHA256_ALGORITHM,
                "key_id": "primary",
                "signature": signature,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def valid_packet(interaction_id: str = "udp-regression") -> dict[str, Any]:
    return {
        "schema_version": "eve-trade-gui.v1",
        "interaction_id": interaction_id,
        "ui": {"action": "market_place_sell_order"},
        "input": {"issued_by_capsuleer_id": 1001, "quantity": 1},
    }


@override_settings(
    GAME_PACKET_HMAC_SECRET="edge-secret",
    GAME_PACKET_HMAC_KEY_ID="primary",
    GAME_PACKET_PRINCIPAL_KEYS_JSON='{"1001":{"key_id":"seller","secret":"edge-secret"}}',
    QUILKIN_UDP_HOST="127.0.0.1",
    QUILKIN_UDP_PORT=26001,
    QUILKIN_UDP_TIMEOUT_SECONDS=0.05,
    QUILKIN_UDP_MAX_ATTEMPTS=2,
    QUILKIN_UDP_RETRY_BACKOFF_SECONDS=0,
    QUILKIN_UDP_SESSION_POOL_SIZE=2,
)
class UdpSessionPoolRegressionTests(SimpleTestCase):
    def setUp(self) -> None:
        udp_client.reset_udp_session_pool()
        ControlledSocket.reset_counts()

    def tearDown(self) -> None:
        udp_client.reset_udp_session_pool()

    def _checkout_with_timeout(self, pool: udp_client._UdpSessionPool, timeout: float) -> Any:
        try:
            return pool.checkout(timeout=timeout)
        except TypeError as exc:
            self.fail(f"UDP session checkout has no bounded timeout contract: {exc}")

    def _checkout_with_cancel(self, pool: udp_client._UdpSessionPool, cancelled: threading.Event) -> Any:
        try:
            return pool.checkout(cancelled=cancelled)
        except TypeError as exc:
            self.fail(f"UDP session checkout has no cancellation contract: {exc}")

    def _snapshot(self, pool: udp_client._UdpSessionPool) -> dict[str, Any]:
        snapshot = getattr(pool, "snapshot", None)
        self.assertTrue(callable(snapshot), "UDP session pool exposes no behavior-backed metrics snapshot")
        return snapshot()

    def test_udp_session_checkout_respects_request_deadline(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            with self.assertRaises(TimeoutError):
                with self._checkout_with_timeout(pool, 0):
                    self.fail("checkout succeeded after its request deadline")

    def test_udp_session_checkout_times_out_when_pool_is_saturated(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                with self._checkout_with_timeout(pool, 0.01):
                    pass
            self.assertLess(time.monotonic() - started, 0.1)

    def test_udp_session_pool_saturation_returns_retryable_overload_error(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            try:
                with self._checkout_with_timeout(pool, 0):
                    pass
            except Exception as exc:
                self.assertTrue(getattr(exc, "retryable", False), f"pool overload is not retryable: {exc!r}")
                self.assertEqual(getattr(exc, "code", None), "udp_pool_saturated")
                return
        self.fail("saturated pool did not return a retryable overload error")

    def test_udp_session_pool_never_blocks_request_worker_indefinitely(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            with self.assertRaises(TimeoutError):
                with self._checkout_with_timeout(pool, 0.01):
                    pass

    def test_udp_session_lease_is_released_after_success(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            self.assertEqual(pool.available.qsize(), 0)
        self.assertEqual(pool.available.qsize(), 1)

    def test_udp_session_lease_is_released_after_timeout(self) -> None:
        factory = SocketFactory([[socket.timeout("lost")], [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))]])
        with patch("trade_gui.udp_client.socket.socket", factory), patch("trade_gui.udp_client.time.sleep"):
            self.assertEqual(udp_client.send_gui_packet(valid_packet())["status"], "accepted")
        self.assertEqual(udp_client._get_udp_session_pool().available.qsize(), 2)

    def test_udp_session_lease_is_released_after_cancellation(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        cancelled = threading.Event()
        cancelled.set()
        checkout = self._checkout_with_cancel(pool, cancelled)
        with self.assertRaisesRegex(Exception, "cancel"):
            with checkout:
                pass
        self.assertEqual(pool.available.qsize(), 1)

    def test_udp_session_lease_is_released_after_exception(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with pool.checkout():
                raise RuntimeError("injected")
        self.assertEqual(pool.available.qsize(), 1)

    def test_udp_session_pool_rejects_checkout_after_close(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        pool.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            with pool.checkout():
                pass

    def test_udp_session_pool_close_prevents_socket_recreation(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        pool.close()
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                with pool.checkout() as session:
                    session.get_socket()
        self.assertEqual(ControlledSocket.created, 0)

    def test_udp_session_return_to_closed_pool_closes_socket(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        lease = pool.checkout()
        session = lease.__enter__()
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            session.get_socket()
            pool.close()
            lease.__exit__(None, None, None)
        self.assertTrue(session.sock is None or session.sock.is_closed)
        self.assertEqual(pool.available.qsize(), 0, "returned session was requeued into a closed pool")

    def test_udp_session_return_to_retired_generation_closes_socket(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        lease = pool.checkout()
        session = lease.__enter__()
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            sock = session.get_socket()
            reset = getattr(pool, "reset", None)
            self.assertTrue(callable(reset), "pool has no generation-aware reset operation")
            reset()
            lease.__exit__(None, None, None)
        self.assertTrue(sock.is_closed)
        self.assertEqual(pool.available.qsize(), 0)

    def test_udp_session_pool_reset_does_not_requeue_old_generation_session(self) -> None:
        old = udp_client._get_udp_session_pool()
        lease = old.checkout()
        lease.__enter__()
        udp_client.reset_udp_session_pool()
        new = udp_client._get_udp_session_pool()
        lease.__exit__(None, None, None)
        self.assertIsNot(old, new)
        self.assertEqual(old.available.qsize(), 0, "retired generation accepted a returned lease")

    @override_settings(QUILKIN_UDP_SESSION_POOL_SIZE=1)
    def test_udp_session_pool_reconfiguration_does_not_leak_checked_out_sockets(self) -> None:
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            old = udp_client._get_udp_session_pool()
            lease = old.checkout()
            session = lease.__enter__()
            sock = session.get_socket()
            with self.settings(QUILKIN_UDP_SESSION_POOL_SIZE=2):
                udp_client._get_udp_session_pool()
            lease.__exit__(None, None, None)
        self.assertTrue(sock.is_closed)
        self.assertEqual(ControlledSocket.created, ControlledSocket.closed)

    def test_udp_session_pool_close_waits_for_checked_out_leases_with_deadline(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        lease = pool.checkout()
        lease.__enter__()
        close_done = threading.Event()
        failures: list[BaseException] = []

        def close_pool() -> None:
            try:
                pool.close(deadline=time.monotonic() + 0.1)
            except BaseException as exc:
                failures.append(exc)
            finally:
                close_done.set()

        thread = threading.Thread(target=close_pool)
        thread.start()
        self.assertFalse(close_done.wait(0.01), "close returned while a lease was still checked out")
        lease.__exit__(None, None, None)
        self.assertTrue(close_done.wait(0.1), "close did not finish after the lease returned")
        thread.join(0.1)
        if failures:
            self.fail(f"UDP session pool close lacks a bounded lease-drain contract: {failures[0]}")

    def test_udp_session_pool_close_completes_when_all_leases_return(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            with pool.checkout() as session:
                session.get_socket()
            pool.close()
        self.assertEqual(ControlledSocket.created, ControlledSocket.closed)

    def test_udp_session_pool_close_does_not_deadlock_waiting_callers(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        held = pool.checkout()
        held.__enter__()
        outcome: list[str] = []
        waiter_done = threading.Event()
        close_done = threading.Event()
        failures: list[BaseException] = []

        def wait_for_lease() -> None:
            try:
                with pool.checkout():
                    outcome.append("acquired")
            except Exception:
                outcome.append("closed")
            finally:
                waiter_done.set()

        def close_pool() -> None:
            try:
                pool.close(deadline=time.monotonic() + 0.1)
            except BaseException as exc:
                failures.append(exc)
            finally:
                close_done.set()

        waiter = threading.Thread(target=wait_for_lease)
        closer = threading.Thread(target=close_pool)
        waiter.start()
        closer.start()
        held.__exit__(None, None, None)
        self.assertTrue(waiter_done.wait(0.1), "waiting checkout deadlocked with close")
        self.assertTrue(close_done.wait(0.1), "pool close deadlocked while waking a waiting checkout")
        waiter.join(0.1)
        closer.join(0.1)
        if failures:
            raise failures[0]
        self.assertEqual(outcome, ["closed"])

    def _concurrent_lifecycle(self, action: str) -> None:
        pool = udp_client._UdpSessionPool(2)
        held = pool.checkout()
        held.__enter__()
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def mutate() -> None:
            try:
                barrier.wait()
                if action == "reset":
                    reset = getattr(pool, "reset", None)
                    self.assertTrue(callable(reset), "pool has no synchronized reset")
                    reset()
                else:
                    pool.close()
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=mutate)
        thread.start()
        barrier.wait()
        held.__exit__(None, None, None)
        thread.join(0.2)
        self.assertFalse(thread.is_alive(), f"concurrent {action} deadlocked")
        if failures:
            raise failures[0]

    def test_udp_session_pool_concurrent_checkout_and_reset_is_race_free(self) -> None:
        self._concurrent_lifecycle("reset")

    def test_udp_session_pool_concurrent_checkout_and_close_is_race_free(self) -> None:
        self._concurrent_lifecycle("close")

    def test_udp_session_pool_concurrent_return_and_close_is_race_free(self) -> None:
        self._concurrent_lifecycle("close")

    def test_udp_session_pool_concurrent_configuration_changes_are_race_free(self) -> None:
        pools: list[udp_client._UdpSessionPool] = []
        barrier = threading.Barrier(3)

        def load(size: int) -> None:
            with self.settings(QUILKIN_UDP_SESSION_POOL_SIZE=size):
                barrier.wait()
                pools.append(udp_client._get_udp_session_pool())

        threads = [threading.Thread(target=load, args=(size,)) for size in (1, 2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(0.2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(pools), 2)

    def test_udp_session_pool_never_exceeds_configured_socket_limit(self) -> None:
        pool = udp_client._UdpSessionPool(2)
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            with pool.checkout() as first, pool.checkout() as second:
                first.get_socket()
                second.get_socket()
                self.assertEqual(ControlledSocket.created, 2)

    def test_udp_session_pool_reports_checked_out_session_count(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            self.assertEqual(self._snapshot(pool)["checked_out"], 1)

    def test_udp_session_pool_reports_acquisition_wait_duration(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            with self.assertRaises(TimeoutError):
                with self._checkout_with_timeout(pool, 0.01):
                    pass
        self.assertGreater(
            self._snapshot(pool)["acquisition_wait_seconds"],
            0,
            "timed-out acquisition recorded no observed wait duration",
        )

    def test_udp_session_pool_reports_acquisition_timeout_count(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            with self.assertRaises(TimeoutError):
                with self._checkout_with_timeout(pool, 0):
                    pass
        self.assertEqual(self._snapshot(pool)["acquisition_timeouts"], 1)

    def test_udp_session_pool_reports_session_replacement_count(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with patch("trade_gui.udp_client.socket.socket", ControlledSocket):
            with pool.checkout() as session:
                session.get_socket()
                session.reset()
                session.get_socket()
        self.assertEqual(self._snapshot(pool)["session_replacements"], 1)

    def test_udp_session_pool_reports_saturation(self) -> None:
        pool = udp_client._UdpSessionPool(1)
        with pool.checkout():
            self.assertTrue(self._snapshot(pool)["saturated"])

    def _send_script(self, scripts: list[list[Any]], interaction_id: str = "udp-regression") -> dict[str, Any]:
        with patch("trade_gui.udp_client.socket.socket", SocketFactory(scripts)), patch("trade_gui.udp_client.time.sleep"):
            return udp_client.send_gui_packet(valid_packet(interaction_id))

    def test_udp_client_discards_delayed_response_from_previous_interaction(self) -> None:
        result = self._send_script([[
            (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
            (signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001)),
        ]])
        self.assertEqual(result["interaction_id"], "udp-regression")

    def test_udp_client_discards_duplicate_response_from_previous_interaction(self) -> None:
        result = self._send_script([[
            (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
            (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
            (signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001)),
        ]])
        self.assertEqual(result["interaction_id"], "udp-regression")

    def test_udp_client_discards_valid_response_with_unexpected_interaction_id(self) -> None:
        result = self._send_script([[
            (signed_response("unexpected", status="accepted"), ("127.0.0.1", 26001)),
            (signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001)),
        ]])
        self.assertEqual(result["interaction_id"], "udp-regression")

    def test_udp_client_continues_receiving_after_unrelated_datagram(self) -> None:
        result = self._send_script([[
            (b"unrelated", ("127.0.0.1", 26001)),
            (signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001)),
        ]])
        self.assertEqual(result["status"], "accepted")

    def test_udp_client_rejects_datagram_from_unexpected_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "unexpected endpoint"):
            self._send_script([[(signed_response("udp-regression", status="accepted"), ("127.0.0.2", 26001))]])

    def test_udp_client_rejects_oversized_datagram_before_parsing(self) -> None:
        oversized = b"x" * 65536
        with patch("trade_gui.udp_client.decode_udp_response", side_effect=AssertionError("oversized datagram reached parser")):
            with self.assertRaisesRegex(ValueError, "too large"):
                self._send_script([[(oversized, ("127.0.0.1", 26001))]])

    def test_udp_client_rejects_invalid_signature_without_accepting_response(self) -> None:
        response = json.loads(signed_response("udp-regression", status="accepted"))
        response["auth"]["signature"] = "invalid"
        with self.assertRaisesRegex(ValueError, "signature"):
            self._send_script([[(json.dumps(response).encode(), ("127.0.0.1", 26001))]])

    def test_udp_client_limits_malformed_datagrams_per_request(self) -> None:
        malformed = [(b"not-json", ("127.0.0.1", 26001))] * 100
        factory = SocketFactory([malformed])
        with patch("trade_gui.udp_client.socket.socket", factory), patch("trade_gui.udp_client.time.sleep"):
            with self.assertRaisesRegex(ValueError, "malformed datagram limit"):
                udp_client.send_gui_packet(valid_packet())
        self.assertLessEqual(len(ControlledSocket.instances[0].sent), 1)

    def test_udp_client_stops_receiving_at_monotonic_deadline(self) -> None:
        monotonic_values = iter((100.0, 100.01, 100.049, 100.051))
        with patch("trade_gui.udp_client.time.monotonic", side_effect=lambda: next(monotonic_values)):
            with self.assertRaises(TimeoutError):
                self._send_script([[(b"unrelated", ("127.0.0.1", 26001))]])
        self.assertTrue(ControlledSocket.instances[0].timeout_history)
        self.assertLessEqual(ControlledSocket.instances[0].timeout_history[-1], 0.05)

    def test_udp_transport_timeout_rotates_session_before_retry(self) -> None:
        result = self._send_script([
            [socket.timeout("lost")],
            [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))],
        ])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ControlledSocket.created, 2)
        self.assertTrue(ControlledSocket.instances[0].is_closed)

    def test_udp_transport_error_rotates_session_before_retry(self) -> None:
        result = self._send_script([
            [ConnectionResetError("reset")],
            [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))],
        ])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ControlledSocket.created, 2)

    def test_udp_backend_unavailable_response_rotates_session_before_retry(self) -> None:
        result = self._send_script([
            [(signed_response("udp-regression", code="downstream_unavailable"), ("127.0.0.1", 26001))],
            [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))],
        ])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ControlledSocket.created, 2)

    def test_udp_backend_overload_response_rotates_session_before_retry(self) -> None:
        result = self._send_script([
            [(signed_response("udp-regression", code="resource_exhausted"), ("127.0.0.1", 26001))],
            [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))],
        ])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ControlledSocket.created, 2)

    def test_udp_retry_can_fail_over_to_healthy_backend(self) -> None:
        with self.settings(QUILKIN_UDP_HOSTS=("127.0.0.2", "127.0.0.1")):
            result = self._send_script([
                [OSError("backend one unavailable")],
                [(signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001))],
            ])
        destinations = [sock.sent[0][1][0] for sock in ControlledSocket.instances if sock.sent]
        self.assertEqual(destinations, ["127.0.0.2", "127.0.0.1"])
        self.assertEqual(result["status"], "accepted")

    def test_udp_application_transient_retry_reuses_session_only_when_safe(self) -> None:
        result = self._send_script([[
            (signed_response("udp-regression", code="request_in_progress"), ("127.0.0.1", 26001)),
            (signed_response("udp-regression", status="accepted"), ("127.0.0.1", 26001)),
        ]])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ControlledSocket.created, 1)

    @override_settings(QUILKIN_UDP_SESSION_POOL_SIZE=0)
    def test_configuration_rejects_udp_session_pool_size_below_minimum(self) -> None:
        with self.assertRaisesRegex(ValueError, "QUILKIN_UDP_SESSION_POOL_SIZE"):
            udp_client._get_udp_session_pool()

    @override_settings(QUILKIN_UDP_SESSION_POOL_SIZE=1000000)
    def test_configuration_rejects_udp_session_pool_size_above_maximum(self) -> None:
        with self.assertRaisesRegex(ValueError, "QUILKIN_UDP_SESSION_POOL_SIZE"):
            udp_client._get_udp_session_pool()
