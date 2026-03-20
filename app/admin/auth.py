"""Admin authentication: login, logout, session management, CSRF, and rate limiting.

Handles all authentication concerns for the admin interface including argon2
password verification, signed cookie sessions, per-session CSRF tokens, and
in-memory rate limiting on the login endpoint.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from quart import Blueprint, Response, abort, current_app, g, redirect, request, session

from app.services.settings import SettingsService

auth = Blueprint("auth", __name__)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password using argon2.

    Args:
        password: The plaintext password to hash.

    Returns:
        An argon2 hash string suitable for storage.
    """
    return _ph.hash(password)


def verify_password(password: str, hash_value: str) -> bool:
    """Verify a plaintext password against a stored argon2 hash.

    Args:
        password: The plaintext password to check.
        hash_value: The stored argon2 hash string.

    Returns:
        ``True`` if the password matches the hash, ``False`` otherwise.
    """
    try:
        return _ph.verify(hash_value, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# CSRF tokens
# ---------------------------------------------------------------------------

_CSRF_SESSION_KEY = "csrf_token"


def generate_csrf_token() -> str:
    """Generate a new cryptographically random CSRF token.

    Returns:
        A 64-character hex string suitable for use as a CSRF token.
    """
    return secrets.token_hex(32)


def get_or_create_csrf_token() -> str:
    """Return the current session's CSRF token, creating one if absent.

    The token is stored in the signed session cookie and is tied to the
    user's session.

    Returns:
        The CSRF token string for the current session.
    """
    token: str | None = session.get(_CSRF_SESSION_KEY)
    if token is None:
        token = generate_csrf_token()
        session[_CSRF_SESSION_KEY] = token
    return token


def validate_csrf(token: str | None) -> bool:
    """Validate a CSRF token from a form submission against the session.

    Uses a constant-time comparison to prevent timing attacks.

    Args:
        token: The CSRF token submitted with the form.

    Returns:
        ``True`` if the token matches the session token, ``False`` otherwise.
    """
    if not token:
        return False
    stored: str | None = session.get(_CSRF_SESSION_KEY)
    if not stored:
        return False
    return secrets.compare_digest(stored, token)


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per IP)
# ---------------------------------------------------------------------------

_rate_limit_lock: asyncio.Lock = asyncio.Lock()
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW: float = 60.0  # seconds
_RATE_LIMIT_MAX: int = 5  # attempts per window


async def check_rate_limit(ip: str) -> bool:
    """Check whether a login attempt from the given IP should be allowed.

    Implements a sliding-window rate limit: at most ``_RATE_LIMIT_MAX``
    attempts within ``_RATE_LIMIT_WINDOW`` seconds. Records a new attempt
    if allowed.

    Args:
        ip: The client IP address string.

    Returns:
        ``True`` if the request is within the rate limit, ``False`` if it
        should be rejected.
    """
    now = time.monotonic()
    async with _rate_limit_lock:
        cutoff = now - _RATE_LIMIT_WINDOW
        _login_attempts[ip] = [t for t in _login_attempts[ip] if t >= cutoff]
        if len(_login_attempts[ip]) >= _RATE_LIMIT_MAX:
            return False
        _login_attempts[ip].append(now)
        return True


# ---------------------------------------------------------------------------
# Auth guard decorator
# ---------------------------------------------------------------------------


def require_auth[F: Callable[..., Awaitable[Any]]](f: F) -> F:
    """Decorator that requires an authenticated session to access a route.

    Redirects unauthenticated requests to the login page.

    Args:
        f: The async route handler to protect.

    Returns:
        The wrapped handler that enforces authentication.
    """

    @wraps(f)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        if not session.get("authenticated"):
            return redirect("/login")
        return await f(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]  # wrapper has compatible runtime signature


# ---------------------------------------------------------------------------
# Login page rendering
# ---------------------------------------------------------------------------

_login_template_cache: str | None = None


def _load_login_template() -> str:
    """Load the login HTML template from disk, caching after first read.

    Returns:
        The raw HTML string with ``{{placeholder}}`` markers.

    Raises:
        FileNotFoundError: If the template file does not exist.
    """
    global _login_template_cache
    if _login_template_cache is not None:
        return _login_template_cache
    template_path = Path(current_app.static_folder or "static") / "pages" / "login.html"
    _login_template_cache = template_path.read_text(encoding="utf-8")
    return _login_template_cache


def _render_login_page(csrf_token: str, error: str | None = None) -> str:
    """Inject the CSRF token and an optional error into the login template.

    Args:
        csrf_token: The CSRF token to embed in the hidden form field.
        error: An optional user-visible error message.

    Returns:
        The fully rendered HTML string ready to serve.
    """
    error_html = f'<p class="login-error">{error}</p>' if error else ""
    html = _load_login_template()
    html = html.replace("{{csrf_token}}", csrf_token)
    html = html.replace("{{error_html}}", error_html)
    return html


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@auth.route("/login", methods=["GET"])
async def login_page() -> Any:
    """Serve the login page.

    Redirects to the admin area if the user is already authenticated.
    Generates a CSRF token for the form on each new session.

    Returns:
        An HTML response containing the login form.
    """
    if session.get("authenticated"):
        return redirect("/admin/")
    csrf_token = get_or_create_csrf_token()
    html = _render_login_page(csrf_token)
    return Response(response=html, status=200, content_type="text/html; charset=utf-8")


@auth.route("/login", methods=["POST"])
async def login_post() -> Any:
    """Handle login form submission.

    Enforces rate limiting, CSRF validation, and credential verification.
    On success, creates an authenticated session and redirects to the admin
    area. On failure, re-renders the login form with an error message.

    Returns:
        A redirect on success, or an HTML error response on failure.
    """
    client_ip = request.remote_addr or "unknown"
    if not await check_rate_limit(client_ip):
        abort(429)

    form = await request.form

    if not validate_csrf(form.get("csrf_token")):
        # Regenerate CSRF token after a failed check.
        session.pop(_CSRF_SESSION_KEY, None)
        csrf_token = get_or_create_csrf_token()
        html = _render_login_page(csrf_token, error="Invalid request. Please try again.")
        return Response(response=html, status=400, content_type="text/html; charset=utf-8")

    username = form.get("username", "")
    password = form.get("password", "")

    configured_username: str = current_app.config["TINKER_USERNAME"]
    settings_svc = SettingsService(g.db_session)
    stored_hash = await settings_svc.get_admin_password_hash()

    credentials_valid = (
        username == configured_username
        and stored_hash is not None
        and verify_password(password, stored_hash)
    )

    if not credentials_valid:
        # Regenerate CSRF token after a failed login attempt.
        session.pop(_CSRF_SESSION_KEY, None)
        csrf_token = get_or_create_csrf_token()
        html = _render_login_page(csrf_token, error="Invalid username or password.")
        return Response(response=html, status=401, content_type="text/html; charset=utf-8")

    # Successful login — rotate the session to prevent fixation.
    session.clear()
    session["authenticated"] = True
    session["username"] = username
    # Generate a fresh CSRF token for the authenticated session.  Admin
    # forms (including the logout button) will embed this token.
    session[_CSRF_SESSION_KEY] = generate_csrf_token()
    return redirect("/admin/")


@auth.route("/logout", methods=["POST"])
async def logout() -> Any:
    """Log out the current user.

    Requires a valid CSRF token to prevent cross-site logout attacks.
    Clears the session and redirects to the login page.

    Returns:
        A redirect to the login page.
    """
    form = await request.form
    if not validate_csrf(form.get("csrf_token")):
        abort(400)
    session.clear()
    return redirect("/login")
