#!/usr/bin/env python3
from __future__ import annotations

"""Executable NSQ restart canary for unacknowledged disk-backed messages."""

import argparse
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence


FRAME_RESPONSE = 0
FRAME_ERROR = 1
FRAME_MESSAGE = 2
TOPIC = "settlement-work"
CHANNEL = "trade-settlement-executor"


class NSQCanaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class MessageFrame:
    timestamp: int
    attempts: int
    message_id: bytes
    body: bytes


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise NSQCanaryError(f"NSQ connection closed with {remaining} frame bytes outstanding")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> tuple[int, bytes]:
    size = struct.unpack(">I", _read_exact(stream, 4))[0]
    if size < 4 or size > 16 * 1024 * 1024:
        raise NSQCanaryError(f"invalid NSQ frame size {size}")
    frame_type = struct.unpack(">I", _read_exact(stream, 4))[0]
    payload = _read_exact(stream, size - 4)
    if frame_type == FRAME_ERROR:
        raise NSQCanaryError(f"NSQ returned error frame: {payload.decode('utf-8', errors='replace')}")
    return frame_type, payload


def parse_message_frame(payload: bytes) -> MessageFrame:
    if len(payload) < 26:
        raise NSQCanaryError(f"NSQ message frame is too short: {len(payload)}")
    return MessageFrame(
        timestamp=struct.unpack(">Q", payload[:8])[0],
        attempts=struct.unpack(">H", payload[8:10])[0],
        message_id=payload[10:26],
        body=payload[26:],
    )


def _open_connection(port: int, timeout: float = 8.0) -> tuple[socket.socket, BinaryIO]:
    connection = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    connection.settimeout(timeout)
    stream = connection.makefile("rwb", buffering=0)
    stream.write(b"  V2")
    return connection, stream


def _read_non_heartbeat(stream: BinaryIO) -> tuple[int, bytes]:
    while True:
        frame_type, payload = read_frame(stream)
        if frame_type == FRAME_RESPONSE and payload == b"_heartbeat_":
            stream.write(b"NOP\n")
            continue
        return frame_type, payload


def publish(port: int, body: bytes) -> None:
    connection, stream = _open_connection(port)
    try:
        stream.write(f"PUB {TOPIC}\n".encode("ascii"))
        stream.write(struct.pack(">I", len(body)))
        stream.write(body)
        frame_type, payload = _read_non_heartbeat(stream)
        if frame_type != FRAME_RESPONSE or payload != b"OK":
            raise NSQCanaryError(f"publish was not acknowledged: type={frame_type} payload={payload!r}")
    finally:
        stream.close()
        connection.close()


def receive_one(port: int) -> tuple[socket.socket, BinaryIO, MessageFrame]:
    connection, stream = _open_connection(port)
    stream.write(f"SUB {TOPIC} {CHANNEL}\n".encode("ascii"))
    frame_type, payload = _read_non_heartbeat(stream)
    if frame_type != FRAME_RESPONSE or payload != b"OK":
        stream.close()
        connection.close()
        raise NSQCanaryError(f"subscription was not acknowledged: type={frame_type} payload={payload!r}")
    stream.write(b"RDY 1\n")
    frame_type, payload = _read_non_heartbeat(stream)
    if frame_type != FRAME_MESSAGE:
        stream.close()
        connection.close()
        raise NSQCanaryError(f"expected message frame, got type={frame_type} payload={payload!r}")
    return connection, stream, parse_message_frame(payload)


def finish(stream: BinaryIO, message_id: bytes) -> None:
    stream.write(b"FIN " + message_id + b"\n")


def _start_nsqd(executable: Path, data_path: Path, tcp_port: int, http_port: int) -> subprocess.Popen[bytes]:
    command = [
        str(executable),
        f"--tcp-address=127.0.0.1:{tcp_port}",
        f"--http-address=127.0.0.1:{http_port}",
        "--broadcast-address=127.0.0.1",
        f"--data-path={data_path}",
        "--mem-queue-size=0",
        "--sync-every=1",
        "--sync-timeout=10ms",
        "--msg-timeout=60s",
        "--max-msg-timeout=15m",
        "--max-req-timeout=2m",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = (process.stdout.read() if process.stdout else b"").decode("utf-8", errors="replace")
            raise NSQCanaryError(f"nsqd exited before accepting connections ({process.returncode}): {output[-2000:]}")
        try:
            with socket.create_connection(("127.0.0.1", tcp_port), timeout=0.2):
                return process
        except OSError:
            time.sleep(0.05)
    process.kill()
    process.wait(timeout=5)
    raise NSQCanaryError("nsqd did not accept TCP connections within 10 seconds")


def _stop_nsqd(process: subprocess.Popen[bytes]) -> int:
    process.terminate()
    try:
        return int(process.wait(timeout=10))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise NSQCanaryError("nsqd did not complete graceful restart shutdown")


def verify_restart_delivery(executable: Path) -> dict[str, object]:
    if not executable.is_file():
        raise NSQCanaryError(f"nsqd executable does not exist: {executable}")
    tcp_port = _free_port()
    http_port = _free_port()
    while http_port == tcp_port:
        http_port = _free_port()
    body = b'{"operation_id":"nsq-restart-canary","idempotency_key":"restart-canary"}'
    with tempfile.TemporaryDirectory(prefix="eve-trade-nsq-restart-") as temp:
        data_path = Path(temp) / "data"
        data_path.mkdir()
        first = _start_nsqd(executable, data_path, tcp_port, http_port)
        first_connection: socket.socket | None = None
        first_stream: BinaryIO | None = None
        second: subprocess.Popen[bytes] | None = None
        try:
            publish(tcp_port, body)
            first_connection, first_stream, original = receive_one(tcp_port)
            # Deliberately do not FIN or REQ.  The broker must recover this
            # in-flight message from its configured disk-backed channel state.
            first_exit = _stop_nsqd(first)
            first_stream.close()
            first_connection.close()
            first_stream = None
            first_connection = None

            second = _start_nsqd(executable, data_path, tcp_port, http_port)
            second_connection, second_stream, redelivered = receive_one(tcp_port)
            try:
                if redelivered.body != body:
                    raise NSQCanaryError("redelivered body differs from the unacknowledged message")
                if redelivered.message_id != original.message_id:
                    raise NSQCanaryError("restart delivered a different message identity")
                if redelivered.attempts <= original.attempts:
                    raise NSQCanaryError("restart did not increment the delivery attempt")
                finish(second_stream, redelivered.message_id)
            finally:
                second_stream.close()
                second_connection.close()
            second_exit = _stop_nsqd(second)
            second = None
            disk_files = sorted(path.name for path in data_path.iterdir() if path.is_file())
            if not disk_files:
                raise NSQCanaryError("disk-only restart canary produced no durable queue files")
            return {
                "schema_version": "eve-trade.nsq-restart-canary/v1",
                "topic": TOPIC,
                "channel": CHANNEL,
                "message_id": original.message_id.decode("ascii"),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "original_attempts": original.attempts,
                "redelivered_attempts": redelivered.attempts,
                "first_shutdown_exit_code": first_exit,
                "second_shutdown_exit_code": second_exit,
                "durable_files": disk_files,
                "unacknowledged_message_redelivered": True,
            }
        finally:
            if first_stream is not None:
                first_stream.close()
            if first_connection is not None:
                first_connection.close()
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nsqd", type=Path, default=Path(os.environ.get("NSQD_BIN", "nsqd")))
    args = parser.parse_args(argv)
    try:
        observation = verify_restart_delivery(args.nsqd.resolve())
    except (NSQCanaryError, OSError, subprocess.SubprocessError) as exc:
        print(f"NSQ_RESTART_CANARY_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(observation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
