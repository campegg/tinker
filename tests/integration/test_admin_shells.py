"""Integration tests for admin Jinja2 shell rendering.

Tests that every admin shell route:

- Correctly injects the live session CSRF token into ``window.__TINKER__``
  (not the raw Jinja2 source expression ``{{ csrf_token }}``).
- HTML-escapes injected user values so that a display name containing
  ``<script>`` cannot appear unescaped in the rendered page.

These tests target the shared ``admin/base.html`` template and the
``_shell_context`` helper in :mod:`app.admin.routes`, rather than any
specific view's content.  All six routes are exercised because each has
its own child template that extends the base; covering all of them
ensures that no route accidentally bypasses the base or overrides the
shared blocks in a way that breaks injection or escaping.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from quart import Quart

import app.admin.auth as auth_module
from app import create_app
from app.admin.auth import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"

#: All admin shell routes — each served by a child template that extends
#: ``templates/admin/base.html``.
SHELL_ROUTES = [
    "/admin/timeline",
    "/admin/notifications",
    "/admin/profile",
    "/admin/following",
    "/admin/followers",
    "/admin/likes",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def application(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Test application with schema and seeded admin password."""
    os.environ["TINKER_DOMAIN"] = LOCAL_DOMAIN
    os.environ["TINKER_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = LOCAL_USERNAME

    quart_app = create_app()

    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth_module._login_attempts.clear()

    async with quart_app.test_app():
        session_factory = quart_app.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_admin_password_hash(hash_password("pass"))

        yield quart_app

    auth_module._login_attempts.clear()


async def _login(client: Any) -> None:
    """Authenticate the test client with the seeded credentials."""
    resp = await client.get("/login")
    body = await resp.get_data(as_text=True)
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    end = body.index('"', start)
    login_csrf = body[start:end]

    await client.post(
        "/login",
        form={
            "username": LOCAL_USERNAME,
            "password": "pass",
            "csrf_token": login_csrf,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCsrfTokenInjection:
    """The session CSRF token is rendered into ``window.__TINKER__`` on every shell."""

    @pytest.mark.parametrize("route", SHELL_ROUTES)
    async def test_token_is_present_and_non_empty(self, application: Quart, route: str) -> None:
        """Rendered shell contains a real token, not the raw Jinja2 expression.

        Two things are verified:

        1. The literal string ``{{ csrf_token }}`` does not appear in the
           output — confirming the template was actually rendered rather than
           served as plain text.
        2. The value injected into ``window.__TINKER__.csrf`` is a non-empty
           string — confirming the session token was resolved and passed to
           the template context.
        """
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get(route)

        assert resp.status_code == 200
        body = await resp.get_data(as_text=True)

        # The raw Jinja2 expression must never leak into the rendered output.
        assert "{{ csrf_token }}" not in body, (
            f"{route}: Jinja2 expression was not rendered — template may not have been found"
        )

        # Locate the bootstrap script block and extract the token value.
        marker = 'window.__TINKER__ = { csrf: "'
        assert marker in body, f"{route}: bootstrap script block not found in response body"
        token_start = body.index(marker) + len(marker)
        token_end = body.index('"', token_start)
        token = body[token_start:token_end]

        assert len(token) > 0, f"{route}: CSRF token in window.__TINKER__ is empty"


class TestHtmlAutoEscaping:
    """Jinja2 auto-escaping prevents injected user values from breaking HTML context."""

    @pytest.mark.parametrize("route", SHELL_ROUTES)
    async def test_display_name_containing_html_is_escaped(
        self, application: Quart, route: str
    ) -> None:
        """A display name with ``<script>`` tags is escaped, never rendered raw.

        The previous ``_inject()`` mechanism used plain ``str.replace()`` with
        no HTML escaping, meaning a crafted display name could inject arbitrary
        markup into the page.  Jinja2's HTML auto-escaping (enabled by default
        for ``.html`` templates) converts ``<`` to ``&lt;`` and ``>`` to
        ``&gt;``, confining the value safely within its attribute context
        regardless of its content.
        """
        xss_name = "<script>alert(1)</script>"

        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_display_name(xss_name)

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get(route)

        assert resp.status_code == 200
        body = await resp.get_data(as_text=True)

        # The unescaped string must not appear anywhere in the response.
        assert xss_name not in body, (
            f"{route}: raw '<script>' found in response — auto-escaping is not working"
        )

        # The content must still be present, in its HTML-escaped form,
        # confirming the value was injected (just safely).
        assert "&lt;script&gt;" in body, (
            f"{route}: escaped display name not found — value may have been dropped entirely"
        )
