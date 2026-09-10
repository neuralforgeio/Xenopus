"""Dashboard launcher — Uvicorn on 127.0.0.1 ONLY.

The production entry always binds loopback (ADR-022): the runtime is
single-user local-first; exposing the dashboard to the network is a
Phase 15+ decision requiring an authentication boundary ADR.
"""

from __future__ import annotations

import uvicorn

from xenopus.web.server import WebServices, build_app

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def run_dashboard(services: WebServices, *, port: int = DEFAULT_PORT) -> None:
    """Serve the dashboard on loopback until interrupted."""
    if not 1 <= port <= 65535:
        msg = f"port must be 1-65535, got {port}"
        raise ValueError(msg)
    uvicorn.run(
        build_app(services),
        host=LOOPBACK_HOST,
        port=port,
        log_level="warning",
        access_log=False,
    )
