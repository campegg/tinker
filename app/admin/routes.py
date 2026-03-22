"""Auth-gated admin page routes.

Serves the static HTML shells for admin views. All routes in this blueprint
require an authenticated session via the :func:`~app.admin.auth.require_auth`
decorator. The HTML shells load Web Components that fetch data from the JSON
API endpoints defined in ``app/admin/api.py``.

The HTML shells are loaded from ``static/admin/`` and served with server-side
injection of per-user values (display name, handle, avatar URL, CSRF token)
that are needed by shell-level Web Components on every page load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quart import Blueprint, Response, current_app, g, redirect

from app.admin.auth import get_or_create_csrf_token, require_auth
from app.services.settings import SettingsService

admin = Blueprint("admin", __name__, url_prefix="/admin")

# In-memory cache for the HTML shell templates.  Bypassed in debug mode so
# changes to static files are reflected without restarting the server.
_template_cache: dict[str, str] = {}


def _load_shell(name: str) -> str:
    """Load an admin HTML shell from ``static/admin/``, caching after first read.

    Args:
        name: The filename within ``static/admin/`` (e.g. ``"timeline.html"``).

    Returns:
        The raw HTML string with ``{{placeholder}}`` injection markers.

    Raises:
        FileNotFoundError: If the shell file does not exist.
    """
    if not current_app.debug and name in _template_cache:
        return _template_cache[name]
    path = Path(current_app.static_folder or "static") / "admin" / name
    content = path.read_text(encoding="utf-8")
    if not current_app.debug:
        _template_cache[name] = content
    return content


async def _shell_context() -> tuple[str, str, str, str]:
    """Build the per-request injection values shared by all admin shells.

    Reads display name, handle, and avatar from the settings table, and
    retrieves (or creates) the session CSRF token.

    Returns:
        A tuple of ``(display_name, handle, avatar_url, csrf_token)``.
    """
    settings_svc = SettingsService(g.db_session)
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]

    display_name = await settings_svc.get_display_name() or username
    handle = f"@{username}@{domain}"
    raw_avatar = await settings_svc.get_avatar()
    avatar_url = f"/media/{raw_avatar}" if raw_avatar else ""
    csrf_token = get_or_create_csrf_token()

    return display_name, handle, avatar_url, csrf_token


def _inject(html: str, display_name: str, handle: str, avatar_url: str, csrf_token: str) -> str:
    """Replace ``{{placeholder}}`` markers in the shell HTML.

    Args:
        html: The raw shell HTML with injection markers.
        display_name: The local user's display name.
        handle: The local user's full Fediverse handle (``@user@domain``).
        avatar_url: The URL path for the local user's avatar.
        csrf_token: The session CSRF token for admin API requests.

    Returns:
        The HTML string with all markers replaced.
    """
    html = html.replace("{{user_name}}", display_name)
    html = html.replace("{{user_handle}}", handle)
    html = html.replace("{{user_avatar}}", avatar_url)
    html = html.replace("{{csrf_token}}", csrf_token)
    return html


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@admin.route("/")
@require_auth
async def index() -> Any:
    """Redirect to the timeline — the primary admin view.

    Returns:
        A redirect to the timeline page.
    """
    return redirect("/admin/timeline")


@admin.route("/timeline")
@require_auth
async def timeline() -> Any:
    """Serve the timeline admin view.

    Loads ``static/admin/timeline.html``, injects user-specific values
    (display name, handle, avatar, CSRF token), and returns it as HTML.

    Returns:
        An HTML response with the timeline shell page.
    """
    html = _load_shell("timeline.html")
    display_name, handle, avatar_url, csrf_token = await _shell_context()
    html = _inject(html, display_name, handle, avatar_url, csrf_token)
    return Response(response=html, status=200, content_type="text/html; charset=utf-8")
