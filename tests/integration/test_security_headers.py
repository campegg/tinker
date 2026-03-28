"""Integration tests for security headers (CSP, X-Frame-Options, etc.)."""

from __future__ import annotations

from typing import Any


class TestSecurityHeaders:
    """Verify security headers are present on responses."""

    async def test_admin_page_has_csp_with_nonce(self, client: Any, app: Any) -> None:
        """Admin HTML pages include a CSP header with a script nonce."""
        async with client.session_transaction() as sess:
            sess["authenticated"] = True

        resp = await client.get("/admin/timeline")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self' 'nonce-" in csp
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp

    async def test_public_page_has_csp(self, client: Any) -> None:
        """Public HTML pages include a CSP header."""
        resp = await client.get("/")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp

    async def test_json_response_has_no_csp(self, client: Any) -> None:
        """JSON API responses do not include CSP headers."""
        resp = await client.get("/.well-known/nodeinfo")
        assert resp.status_code == 200
        assert "Content-Security-Policy" not in resp.headers

    async def test_x_content_type_options(self, client: Any) -> None:
        """All responses include X-Content-Type-Options: nosniff."""
        resp = await client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_x_frame_options(self, client: Any) -> None:
        """All responses include X-Frame-Options: DENY."""
        resp = await client.get("/")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    async def test_login_page_has_public_csp(self, client: Any) -> None:
        """The login page uses the public CSP (no nonce needed)."""
        resp = await client.get("/login")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self'" in csp
        # Login page should not have a nonce since it has no inline scripts
        assert "nonce-" not in csp
