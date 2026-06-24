#!/usr/bin/env python
"""Small UDP proxy fallback for local simulator runs when Quilkin cannot be fetched.

This is not the primary path. It exists only so local development can keep moving
when pulling the Quilkin image stalls or fails.
"""

from __future__ import annotations

import argparse
import socket
import threading


def pump(source: socket.socket, target: tuple[str, int], reply_to: tuple[str, int] | None = None) -> None:
    while True:
        data, remote = source.recvfrom(65535)
        destination = target if reply_to is None else reply_to
        source.sendto(data, destination)
        if reply_to is None:
            threading.Thread(target=pump_once, args=(source, remote), daemon=True).start()


def pump_once(sock: socket.socket, remote: tuple[str, int]) -> None:
    data, _ = sock.recvfrom(65535)
    sock.sendto(data, remote)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=26001)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=26000)
    args = parser.parse_args()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.listen_host, args.listen_port))
        print(
            f"fallback UDP proxy listening on {args.listen_host}:{args.listen_port} "
            f"-> {args.target_host}:{args.target_port}",
            flush=True,
        )
        while True:
            data, remote = sock.recvfrom(65535)
            sock.sendto(data, (args.target_host, args.target_port))
            response, _ = sock.recvfrom(65535)
            sock.sendto(response, remote)


if __name__ == "__main__":
    main()
