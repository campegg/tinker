"""Shared HTTP client for outbound requests.

Provides a process-level ``httpx.AsyncClient`` with HTTP/2 support,
connection pooling, and the standard Tinker User-Agent header.  The client
is created once at app startup and disposed on shutdown — all outbound
requests (federation delivery, actor fetches, avatar proxying, WebFinger
lookups) should use :func:`get_http_client` instead of constructing their
own ``AsyncClient``.
"""

from __future__ import annotations

import httpx

from app.core.config import USER_AGENT

# Module-level singleton.  Initialised by ``init_http_client()`` during app
# startup and disposed by ``close_http_client()`` on shutdown.
_client: httpx.AsyncClient | None = None


def init_http_client() -> httpx.AsyncClient:
    """Create the shared ``AsyncClient`` and store it as a module-level singleton.

    Enables HTTP/2 (the ``h2`` library is installed via the ``httpx[http2]``
    extra) and sets a default User-Agent header so callers do not need to
    include it on every request.

    Returns:
        The newly created ``AsyncClient``.
    """
    global _client
    _client = httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
    )
    return _client


async def close_http_client() -> None:
    """Close the shared ``AsyncClient`` and release its connection pool.

    Safe to call even if the client was never initialised.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_http_client() -> httpx.AsyncClient:
    """Return the shared ``AsyncClient``.

    Raises:
        RuntimeError: If called before :func:`init_http_client`.
    """
    if _client is None:
        raise RuntimeError("HTTP client not initialised — call init_http_client() first")
    return _client
