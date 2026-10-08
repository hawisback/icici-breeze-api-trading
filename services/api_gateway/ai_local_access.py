"""Anonymous AI API access restricted to the local workstation.

No Bearer credentials are needed on /api/v1/ai. This is intentionally NOT
an authentication bypass for the platform's other HTTP endpoints.

With Docker's loopback-published host port, the TCP peer seen inside the
container is usually the bridge gateway rather than 127.0.0.1. Operators
can explicitly allow exactly that gateway using AI_TRUST_LOCAL_DOCKER_GATEWAY,
provided they publish the port on 127.0.0.1 only.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from pathlib import Path

from fastapi import HTTPException, Request

from services.api_gateway.service_container import get_services


def _docker_default_gateway() -> str | None:
    """Find the Docker bridge gateway; never trust arbitrary private subnets."""
    if not Path("/.dockerenv").exists():
        return None
    try:
        with Path("/proc/net/route").open(encoding="ascii") as routes:
            next(routes, None)  # header
            for line in routes:
                fields = line.split()
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                # Flag bit 0x2 indicates a gateway route.
                if not (int(fields[3], 16) & 0x2):
                    continue
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError, IndexError, struct.error):
        return None
    return None


def _loopback_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def require_local_ai_client(request: Request) -> None:
    """Allow unauthenticated local AI reads and PAPER trade submissions only.

    Host, Origin, X-Forwarded-For and other caller-controlled HTTP headers
    never prove loopback origin. Evaluate the actual ASGI TCP peer instead.
    """
    peer = request.client.host if request.client else ""
    if _loopback_peer(peer):
        return

    settings = get_services().settings
    if (
        settings.ai_trust_local_docker_gateway
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        and peer == _docker_default_gateway()
    ):
        return

    raise HTTPException(
        status_code=403,
        detail="AI_API_LOCAL_ONLY: /api/v1/ai accepts local workstation requests only.",
    )
