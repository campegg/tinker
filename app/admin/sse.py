"""Server-Sent Events endpoint for real-time notification push (WP-16).

Streams notification events from the in-process ``asyncio.Queue`` to the
connected admin client over a persistent HTTP connection using the SSE
protocol (``text/event-stream``).

**Single-consumer caveat:** The notification queue is an ``asyncio.Queue``.
Each event is consumed by exactly one ``get()`` call. If multiple SSE
connections are open simultaneously (unlikely for a single-user admin),
events will be split between them. This is an accepted limitation.

The endpoint also sends a ``retry: 3000`` directive so the browser's
native ``EventSource`` will reconnect 3 s after any disconnect, and emits
a keep-alive comment every ~25 s to prevent proxy timeouts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from quart import Blueprint, Response, current_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from app.admin.auth import require_auth

logger = logging.getLogger(__name__)

sse_bp = Blueprint("sse", __name__, url_prefix="")

# Guard against multiple simultaneous SSE connections. The notification queue
# is a single-consumer asyncio.Queue: events are consumed by exactly one
# get() call, so a second open tab would steal events from the first.
# Note: Last-Event-ID replay is not supported; reconnections may miss events
# fired during the disconnect window.
_sse_connected: bool = False


async def _event_stream() -> AsyncGenerator[bytes, None]:
    r"""Yield SSE-formatted byte frames from the notification queue.

    Sets the :data:`_sse_connected` guard on entry and clears it on exit
    so that a second connection attempt is rejected with ``409`` rather
    than silently splitting events.

    Yields:
        Raw bytes for each SSE frame:

        - ``retry: 3000\n\n`` once at the start.
        - ``data: {json}\n\n`` for each notification event.
        - ``: ping\n\n`` (comment) when the 25 s timeout elapses with no event.
    """
    global _sse_connected
    _sse_connected = True
    queue: asyncio.Queue[Any] = current_app.config["NOTIFICATION_QUEUE"]
    try:
        yield b"retry: 3000\n\n"

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
                if event is None:
                    # Sentinel value — close the stream cleanly. Used in tests to
                    # terminate the generator without waiting for a keep-alive timeout.
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode()
            except TimeoutError:
                # Keep-alive: SSE comment so the connection stays open through
                # reverse-proxy idle timeouts.
                yield b": ping\n\n"
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in SSE event stream")
                break
    finally:
        _sse_connected = False


@sse_bp.route("/admin/api/notifications/events", methods=["GET"])
@require_auth
async def notification_events() -> Response:
    """Stream notification events to the admin client via Server-Sent Events.

    Keeps the HTTP connection open and pushes a ``data:`` frame for each
    notification event that arrives on the ``NOTIFICATION_QUEUE``.  A
    keep-alive comment is sent every ~25 s to prevent proxy timeout.

    The browser's native ``EventSource`` will reconnect automatically after
    3 s (set by the ``retry:`` directive emitted at stream start).

    Returns:
        A streaming ``text/event-stream`` response.
    """
    if _sse_connected:
        return Response(
            "SSE stream already open in another tab.",
            status=409,
            content_type="text/plain; charset=utf-8",
        )
    return Response(
        _event_stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
