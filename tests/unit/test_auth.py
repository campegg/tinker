"""Unit tests for the admin authentication module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch


class TestPasswordHashing:
    """Tests for argon2 password hashing and verification."""

    def test_hash_returns_string(self) -> None:
        """hash_password returns a non-empty string."""
        from app.admin.auth import hash_password

        result = hash_password("secret")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_includes_argon2_prefix(self) -> None:
        """hash_password produces an argon2 hash (starts with $argon2)."""
        from app.admin.auth import hash_password

        result = hash_password("secret")
        assert result.startswith("$argon2")

    def test_verify_correct_password(self) -> None:
        """verify_password returns True for the correct password."""
        from app.admin.auth import hash_password, verify_password

        h = hash_password("correct-horse")
        assert verify_password("correct-horse", h) is True

    def test_verify_wrong_password(self) -> None:
        """verify_password returns False for an incorrect password."""
        from app.admin.auth import hash_password, verify_password

        h = hash_password("correct-horse")
        assert verify_password("wrong-horse", h) is False

    def test_verify_invalid_hash(self) -> None:
        """verify_password returns False for a malformed hash string."""
        from app.admin.auth import verify_password

        assert verify_password("password", "not-a-valid-hash") is False

    def test_two_hashes_of_same_password_differ(self) -> None:
        """hash_password produces unique hashes (argon2 uses random salt)."""
        from app.admin.auth import hash_password

        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_both_hashes_verify(self) -> None:
        """Both unique hashes for the same password verify correctly."""
        from app.admin.auth import hash_password, verify_password

        h1 = hash_password("same")
        h2 = hash_password("same")
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


class TestCsrf:
    """Tests for CSRF token generation and validation."""

    def test_generate_token_length(self) -> None:
        """generate_csrf_token produces a 64-character hex string."""
        from app.admin.auth import generate_csrf_token

        token = generate_csrf_token()
        assert len(token) == 64

    def test_generate_token_uniqueness(self) -> None:
        """generate_csrf_token produces distinct tokens each call."""
        from app.admin.auth import generate_csrf_token

        tokens = {generate_csrf_token() for _ in range(10)}
        assert len(tokens) == 10

    def test_validate_csrf_matching_token(self) -> None:
        """validate_csrf returns True when the token matches the session."""
        from app.admin.auth import validate_csrf

        token = "abc123"
        with patch("app.admin.auth.session", {"csrf_token": token}):
            assert validate_csrf(token) is True

    def test_validate_csrf_wrong_token(self) -> None:
        """validate_csrf returns False when the token does not match."""
        from app.admin.auth import validate_csrf

        with patch("app.admin.auth.session", {"csrf_token": "stored-token"}):
            assert validate_csrf("different-token") is False

    def test_validate_csrf_none_token(self) -> None:
        """validate_csrf returns False when None is provided."""
        from app.admin.auth import validate_csrf

        with patch("app.admin.auth.session", {"csrf_token": "stored"}):
            assert validate_csrf(None) is False

    def test_validate_csrf_no_session_token(self) -> None:
        """validate_csrf returns False when no token is in the session."""
        from app.admin.auth import validate_csrf

        with patch("app.admin.auth.session", {}):
            assert validate_csrf("some-token") is False

    def test_get_or_create_returns_existing(self) -> None:
        """get_or_create_csrf_token returns the existing session token."""
        from app.admin.auth import get_or_create_csrf_token

        existing = "existing-csrf-token"
        mock_session: dict[str, str] = {"csrf_token": existing}
        with patch("app.admin.auth.session", mock_session):
            result = get_or_create_csrf_token()
        assert result == existing

    def test_get_or_create_generates_when_absent(self) -> None:
        """get_or_create_csrf_token creates a new token when none exists."""
        from app.admin.auth import get_or_create_csrf_token

        mock_session: dict[str, str] = {}
        with patch("app.admin.auth.session", mock_session):
            result = get_or_create_csrf_token()
        assert len(result) == 64
        assert mock_session["csrf_token"] == result


class TestRateLimiting:
    """Tests for in-memory login rate limiting."""

    def setup_method(self) -> None:
        """Clear the rate limit state before each test."""
        import app.admin.auth as auth_module

        auth_module._login_attempts.clear()

    async def test_allows_initial_requests(self) -> None:
        """First attempts within the limit are allowed."""
        from app.admin.auth import check_rate_limit

        for _ in range(5):
            allowed = await check_rate_limit("127.0.0.1")
            assert allowed is True

    async def test_blocks_after_max_attempts(self) -> None:
        """Requests exceeding the limit are rejected."""
        from app.admin.auth import check_rate_limit

        for _ in range(5):
            await check_rate_limit("10.0.0.1")

        blocked = await check_rate_limit("10.0.0.1")
        assert blocked is False

    async def test_different_ips_are_independent(self) -> None:
        """Rate limits are tracked per IP address."""
        from app.admin.auth import check_rate_limit

        for _ in range(5):
            await check_rate_limit("192.168.1.1")

        # A different IP is unaffected.
        allowed = await check_rate_limit("192.168.1.2")
        assert allowed is True

    async def test_window_expires_old_attempts(self) -> None:
        """Attempts older than the window do not count against the limit."""
        import app.admin.auth as auth_module
        from app.admin.auth import check_rate_limit

        ip = "172.16.0.1"

        # Manually plant 5 old attempts (outside the window).
        old_ts = time.monotonic() - auth_module._RATE_LIMIT_WINDOW - 1.0
        auth_module._login_attempts[ip] = [old_ts] * 5

        # A new request should be allowed since old attempts have expired.
        allowed = await check_rate_limit(ip)
        assert allowed is True


class TestRequireAuth:
    """Tests for the require_auth decorator."""

    def test_decorated_function_is_callable(self) -> None:
        """require_auth returns a callable wrapper."""
        from app.admin.auth import require_auth

        async def handler() -> str:
            return "ok"

        wrapped = require_auth(handler)
        assert callable(wrapped)

    def test_preserves_function_name(self) -> None:
        """require_auth preserves the original function's __name__."""
        from app.admin.auth import require_auth

        async def my_handler() -> str:
            return "ok"

        wrapped = require_auth(my_handler)
        assert wrapped.__name__ == "my_handler"

    async def test_redirects_when_not_authenticated(self) -> None:
        """require_auth redirects to /login when not authenticated."""
        from app.admin.auth import require_auth

        async def handler() -> str:
            return "secret"

        wrapped = require_auth(handler)
        mock_redirect = MagicMock(return_value="redirect-response")

        with (
            patch("app.admin.auth.session", {}),
            patch("app.admin.auth.redirect", mock_redirect),
        ):
            result = await wrapped()

        mock_redirect.assert_called_once_with("/login")
        assert result == "redirect-response"

    async def test_calls_handler_when_authenticated(self) -> None:
        """require_auth calls the wrapped handler when authenticated."""
        from app.admin.auth import require_auth

        async def handler() -> str:
            return "secret"

        wrapped = require_auth(handler)

        with patch("app.admin.auth.session", {"authenticated": True}):
            result = await wrapped()

        assert result == "secret"
