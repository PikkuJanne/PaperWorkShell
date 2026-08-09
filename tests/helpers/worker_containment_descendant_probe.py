"""Fixed descendant probe for the Windows worker-containment security test."""

from __future__ import annotations

import json
import socket

_PROBE_PORTS = (46371, 46372, 46373, 46374, 46375)


def _tcp_connected(family: socket.AddressFamily, host: str) -> bool:
    for port in _PROBE_PORTS:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                if client.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def _send_udp(family: socket.AddressFamily, host: str) -> bool:
    reported_success = False
    for port in _PROBE_PORTS:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as client:
                reported_success = client.sendto(b"pws-egress-probe", (host, port)) > 0
        except OSError:
            continue
    return reported_success


def main() -> int:
    result = {
        "tcp4_connected": _tcp_connected(socket.AF_INET, "127.0.0.1"),
        "tcp6_connected": _tcp_connected(socket.AF_INET6, "::1"),
        "udp4_send_reported": _send_udp(socket.AF_INET, "127.0.0.1"),
        "udp6_send_reported": _send_udp(socket.AF_INET6, "::1"),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
