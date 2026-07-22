from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import socket
import threading
from typing import Any
from unittest.mock import patch

import pytest

from helpers import (
    EDGE_RESPONSE_SCHEMA,
    HMAC_SHA256_ALGORITHM,
    AuthenticatedEdgeClient,
    response_signing_bytes,
)


class PoolSocket:
    created = 0
    closed = 0

    def __init__(self, script: list[Any] | None = None):
        type(self).created += 1
        self.script = list(script or [])
        self.is_closed = False
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeout: float | None = None

    @classmethod
    def reset(cls) -> None:
        cls.created = 0
        cls.closed = 0

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def close(self) -> None:
        if not self.is_closed:
            type(self).closed += 1
            self.is_closed = True

    def sendto(self, payload: bytes, endpoint: tuple[str, int]) -> int:
        if self.is_closed:
            raise OSError("socket closed")
        self.sent.append((payload, endpoint))
        return len(payload)

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.script:
            raise socket.timeout("script exhausted")
        value = self.script.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class BlockingSocket(PoolSocket):
    def __init__(self, entered: threading.Event, release: threading.Event):
        super().__init__()
        self.entered = entered
        self.release = release

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        self.entered.set()
        if not self.release.wait(0.5):
            raise TimeoutError("test release barrier expired")
        return signed_response("pool-request", status="accepted"), ("127.0.0.1", 26001)


class SocketFactory:
    def __init__(self, scripts: list[list[Any]] | None = None):
        self.scripts = list(scripts or [])
        self.instances: list[PoolSocket] = []

    def __call__(self, *_args: Any, **_kwargs: Any) -> PoolSocket:
        script = self.scripts.pop(0) if self.scripts else []
        result = PoolSocket(script)
        self.instances.append(result)
        return result


@pytest.fixture(autouse=True)
def reset_pool_socket_counts() -> None:
    PoolSocket.reset()


def signed_response(interaction_id: str, **values: Any) -> bytes:
    payload = {"interaction_id": interaction_id, **values}
    canonical = response_signing_bytes(EDGE_RESPONSE_SCHEMA, "primary", payload)
    signature = base64.urlsafe_b64encode(
        hmac.new(b"edge-secret", canonical, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return json.dumps(
        {
            "schema_version": EDGE_RESPONSE_SCHEMA,
            "payload": payload,
            "auth": {
                "algorithm": HMAC_SHA256_ALGORITHM,
                "key_id": "primary",
                "signature": signature,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def packet(interaction_id: str = "pool-request") -> dict[str, Any]:
    return {
        "schema_version": "eve-trade-gui.v1",
        "interaction_id": interaction_id,
        "ui": {"action": "market_place_sell_order"},
        "input": {"issued_by_capsuleer_id": 1001, "quantity": 1},
    }


def client(factory: Any | None = None) -> tuple[AuthenticatedEdgeClient, Any]:
    factory = factory or SocketFactory()
    with patch("helpers.socket.socket", factory):
        result = AuthenticatedEdgeClient(
            "127.0.0.1",
            26001,
            "edge-secret",
            "primary",
            checkout_timeout=0.025,
        )
    return result, factory


def submit(edge: AuthenticatedEdgeClient, interaction_id: str = "pool-request") -> dict[str, Any]:
    return edge.submit(packet(interaction_id), "seller", "edge-secret")


def test_e2e_udp_pool_checkout_has_timeout() -> None:
    edge, _ = client()
    borrowed = [edge.sockets.get_nowait() for _ in range(10)]
    outcome: queue.Queue[BaseException | dict[str, Any]] = queue.Queue()
    thread = threading.Thread(target=lambda: outcome.put(_capture(lambda: submit(edge))))
    thread.start()
    completed_before_release = thread.join(0.05) is None and not thread.is_alive()
    edge.sockets.put_nowait(borrowed.pop())
    thread.join(0.2)
    for sock in borrowed:
        sock.close()
    edge.close()
    assert completed_before_release, "E2E UDP checkout blocked beyond its request deadline"
    assert isinstance(outcome.get_nowait(), TimeoutError)


def _capture(operation: Any) -> Any:
    try:
        return operation()
    except BaseException as exc:
        return exc


def test_e2e_udp_pool_shutdown_closes_idle_sockets() -> None:
    edge, _ = client()
    first = edge.sockets.get_nowait()
    second = edge.sockets.get_nowait()
    edge.sockets.put_nowait(first)
    edge.sockets.put_nowait(second)
    edge.close()
    assert PoolSocket.created == PoolSocket.closed == 2
    assert edge.sockets.empty()


def test_e2e_udp_pool_shutdown_closes_returned_borrowed_sockets() -> None:
    edge, _ = client()
    borrowed = edge.sockets.get_nowait()
    edge.close()
    edge.sockets.put_nowait(borrowed)
    assert borrowed.is_closed
    assert edge.sockets.empty(), "borrowed socket returned into a closed E2E pool"


def test_e2e_udp_pool_shutdown_does_not_miss_concurrently_returned_sockets() -> None:
    edge, _ = client()
    borrowed = edge.sockets.get_nowait()
    barrier = threading.Barrier(2)

    def return_socket() -> None:
        barrier.wait()
        edge.sockets.put_nowait(borrowed)

    thread = threading.Thread(target=return_socket)
    thread.start()
    barrier.wait()
    edge.close()
    thread.join(0.2)
    assert not thread.is_alive()
    assert borrowed.is_closed
    assert edge.sockets.empty()


def test_e2e_udp_pool_return_after_shutdown_closes_socket() -> None:
    edge, _ = client()
    borrowed = edge.sockets.get_nowait()
    edge.close()
    edge.sockets.put_nowait(borrowed)
    assert borrowed.is_closed
    assert edge.sockets.empty()


def test_e2e_udp_pool_return_never_masks_original_exception() -> None:
    factory = SocketFactory([[OSError("original receive failure")]])
    edge, _ = client(factory)
    with pytest.raises(OSError, match="original receive failure"):
        submit(edge)
    edge.close()


def test_e2e_udp_pool_failed_replacement_does_not_requeue_closed_socket() -> None:
    factory = SocketFactory([[OSError("transport failed")]])
    edge, _ = client(factory)
    primed = edge.sockets.get_nowait()
    edge.sockets.put_nowait(primed)
    original_new_socket = edge._new_socket

    def fail_replacement() -> PoolSocket:
        raise OSError("replacement failed")

    edge._new_socket = fail_replacement  # type: ignore[method-assign]
    failure = _capture(lambda: submit(edge))
    edge._new_socket = original_new_socket  # type: ignore[method-assign]
    queued = list(edge.sockets.queue)
    edge.close()
    assert isinstance(failure, OSError)
    assert "transport failed" in str(failure), f"replacement error masked original transport failure: {failure}"
    assert all(not sock.is_closed for sock in queued), "failed replacement requeued a closed socket"


def test_e2e_udp_pool_concurrent_shutdown_and_checkout_does_not_deadlock() -> None:
    edge, _ = client()
    barrier = threading.Barrier(2)
    done = threading.Event()
    outcome: queue.Queue[Any] = queue.Queue()

    def checkout() -> None:
        try:
            barrier.wait()
            sock = edge.sockets.get(timeout=0.1)
            sock.close()
            outcome.put("closed")
        except BaseException as exc:
            outcome.put(exc)
        finally:
            done.set()

    thread = threading.Thread(target=checkout)
    thread.start()
    barrier.wait()
    edge.close()
    assert done.wait(0.2), "checkout deadlocked with E2E pool shutdown"
    thread.join(0.2)
    assert not thread.is_alive()
    assert not outcome.empty()


def test_e2e_udp_pool_concurrent_shutdown_and_return_does_not_leak_descriptors() -> None:
    edge, _ = client()
    borrowed = edge.sockets.get_nowait()
    barrier = threading.Barrier(2)
    thread = threading.Thread(target=lambda: (barrier.wait(), edge.sockets.put_nowait(borrowed)))
    thread.start()
    barrier.wait()
    edge.close()
    thread.join(0.2)
    assert PoolSocket.created == PoolSocket.closed


def test_e2e_udp_pool_does_not_eagerly_open_more_sockets_than_required() -> None:
    edge, _ = client()
    try:
        assert PoolSocket.created == 0, "E2E client eagerly opened its full ten-socket capacity"
    finally:
        edge.close()


def test_udp_pool_handles_more_concurrent_callers_than_capacity() -> None:
    entered = [threading.Event() for _ in range(10)]
    release = threading.Event()
    sockets = [BlockingSocket(event, release) for event in entered]
    factory = iter(sockets)
    edge, _ = client(lambda *_args, **_kwargs: next(factory))
    outcomes: queue.Queue[Any] = queue.Queue()
    threads = [threading.Thread(target=lambda: outcomes.put(_capture(lambda: submit(edge)))) for _ in range(11)]
    for thread in threads:
        thread.start()
    assert all(event.wait(0.2) for event in entered), "pool did not utilize its configured capacity"
    release.set()
    for thread in threads:
        thread.join(0.5)
    edge.close()
    assert all(not thread.is_alive() for thread in threads)
    assert outcomes.qsize() == 11
    observed = [outcomes.get_nowait() for _ in range(11)]
    assert all(isinstance(value, dict) for value in observed), (
        f"queued over-capacity caller did not complete normally: {observed!r}"
    )
    assert all(value.get("status") == "accepted" for value in observed)


def test_udp_pool_waiting_checkout_is_cancelled_with_request() -> None:
    edge, _ = client()
    cancelled = threading.Event()
    cancelled.set()
    try:
        try:
            edge.submit(packet(), "seller", "edge-secret", cancelled=cancelled)  # type: ignore[call-arg]
        except TypeError as exc:
            pytest.fail(f"E2E UDP checkout has no request-cancellation contract: {exc}")
        except Exception as exc:
            assert "cancel" in str(exc).lower(), f"cancellation returned the wrong error: {exc!r}"
        else:
            pytest.fail("cancelled E2E UDP checkout completed successfully")
    finally:
        edge.close()


def test_udp_pool_reset_during_checkout_is_safe() -> None:
    edge, _ = client()
    borrowed = edge.sockets.get_nowait()
    try:
        reset = getattr(edge, "reset", None)
        assert callable(reset), "E2E UDP pool has no generation-aware reset"
        reset()
        edge.sockets.put_nowait(borrowed)
        assert borrowed.is_closed
    finally:
        borrowed.close()
        edge.close()


def test_udp_pool_close_during_receive_is_safe() -> None:
    entered = threading.Event()
    release = threading.Event()
    blocking = BlockingSocket(entered, release)
    remaining = iter([blocking, *[PoolSocket() for _ in range(9)]])
    edge, _ = client(lambda *_args, **_kwargs: next(remaining))
    outcome: queue.Queue[Any] = queue.Queue()
    thread = threading.Thread(target=lambda: outcome.put(_capture(lambda: submit(edge))))
    thread.start()
    assert entered.wait(0.2)
    edge.close()
    release.set()
    thread.join(0.2)
    assert not thread.is_alive()
    assert blocking.is_closed, "borrowed receive socket survived shutdown"


def test_udp_pool_handles_stale_datagram_after_session_reuse() -> None:
    factory = SocketFactory([[
        (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
        (signed_response("pool-request", status="accepted"), ("127.0.0.1", 26001)),
    ]])
    edge, _ = client(factory)
    try:
        assert submit(edge)["interaction_id"] == "pool-request"
    finally:
        edge.close()


def test_udp_pool_handles_delayed_duplicate_after_session_reuse() -> None:
    factory = SocketFactory([[
        (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
        (signed_response("previous", status="accepted"), ("127.0.0.1", 26001)),
        (signed_response("pool-request", status="accepted"), ("127.0.0.1", 26001)),
    ]])
    edge, _ = client(factory)
    try:
        assert submit(edge)["interaction_id"] == "pool-request"
    finally:
        edge.close()


def test_udp_pool_shutdown_with_borrowed_sessions_completes_cleanly() -> None:
    edge, _ = client()
    borrowed = [edge.sockets.get_nowait() for _ in range(3)]
    edge.close()
    for sock in borrowed:
        edge.sockets.put_nowait(sock)
    assert all(sock.is_closed for sock in borrowed)
    assert edge.sockets.empty()
