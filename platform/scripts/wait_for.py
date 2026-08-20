#!/usr/bin/env python3
"""Block until dependencies are reachable, then exit 0.

Used by service entrypoints and by `make verify` so a container does not start
racing an infrastructure component that is still opening its socket. Standard
library only, so it can run in any image without a pip install.

Targets:
    tcp://host:port          a listening socket
    http://host:port/path    a 2xx/3xx response
    https://host/path

Usage:
    python platform/scripts/wait_for.py tcp://postgres:5432 http://minio:9000/minio/health/live
    python platform/scripts/wait_for.py --timeout 120 tcp://kafka:9092
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 60.0
PROBE_TIMEOUT = 5.0


def _probe_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _probe_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as response:  # noqa: S310
            return 200 <= int(response.status) < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def probe(target: str) -> bool:
    parsed = urlparse(target)
    if parsed.scheme == "tcp":
        if parsed.hostname is None or parsed.port is None:
            raise SystemExit(f"wait_for: '{target}' must look like tcp://host:port")
        return _probe_tcp(parsed.hostname, parsed.port)
    if parsed.scheme in {"http", "https"}:
        return _probe_http(target)
    raise SystemExit(
        f"wait_for: unsupported scheme in '{target}' (use tcp://, http:// or https://)"
    )


def wait_for(targets: list[str], timeout: float, interval: float) -> int:
    deadline = time.monotonic() + timeout
    remaining = list(targets)

    while remaining:
        ready = [t for t in remaining if probe(t)]
        for target in ready:
            print(f"up    {target}")
            remaining.remove(target)
        if not remaining:
            break
        if time.monotonic() >= deadline:
            for target in remaining:
                print(f"TIMEOUT {target} after {timeout:.0f}s", file=sys.stderr)
            return 1
        time.sleep(interval)

    print(f"all {len(targets)} target(s) reachable")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="+", help="tcp://host:port or http(s)://url")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds (60)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between probes")
    args = parser.parse_args(argv)
    return wait_for(args.targets, args.timeout, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
