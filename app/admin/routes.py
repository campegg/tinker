"""Auth-gated admin page routes.

Serves the Jinja2 HTML templates for admin views. All routes in this
blueprint require an authenticated session via the
:func:`~app.admin.auth.require_auth` decorator. Templates live in
``templates/admin/`` and extend a shared base (``admin/base.html``) that
owns the ``<head>``, ``<nav-bar>``, and foundation ``<script>`` tags.

Per-request context (display name, handle, avatar, CSRF token) is built
by :func:`_shell_context` and passed to :func:`~quart.render_template`
as keyword arguments. Jinja2 HTML auto-escaping is enabled by default for
``.html`` templates, so injected values are safe against XSS without
additional filtering.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, current_app, g, redirect, render_template

from app.admin.auth import get_or_create_csrf_token, require_auth
from app.services.settings import SettingsService

admin = Blueprint("admin", __name__, url_prefix="/admin")


async def _shell_context(nav_active: str) -> dict[str, str]:
    """Build the Jinja2 template context shared by all admin views.

    Reads display name, handle, and avatar from the settings table, and
    retrieves (or creates) the session CSRF token.

    Args:
        nav_active: The key of the currently active nav item — one of
            ``"timeline"``, ``"notifications"``, ``"profile"``,
            ``"likes"``, ``"following"``, ``"followers"``.

    Returns:
        A dict suitable for unpacking as ``render_template`` keyword
        arguments, with keys ``nav_active``, ``user_name``,
        ``user_handle``, ``user_avatar``, and ``csrf_token``.
    """
    settings_svc = SettingsService(g.db_session)
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]

    display_name = await settings_svc.get_display_name() or username
    handle = f"@{username}@{domain}"
    raw_avatar = await settings_svc.get_avatar()
    avatar_url = f"/media/{raw_avatar}" if raw_avatar else ""
    csrf_token = get_or_create_csrf_token()

    return {
        "nav_active": nav_active,
        "user_name": display_name,
        "user_handle": handle,
        "user_avatar": avatar_url,
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@admin.route("/")
@require_auth
async def index() -> Any:
    """Redirect to the timeline — the primary admin view.

    Returns:
        A redirect to ``/admin/timeline``.
    """
    return redirect("/admin/timeline")


@admin.route("/timeline")
@require_auth
async def timeline() -> Any:
    """Serve the timeline admin view.

    Returns:
        Rendered ``admin/timeline.html`` with the shared template context.
    """
    return await render_template("admin/timeline.html", **await _shell_context("timeline"))


@admin.route("/notifications")
@require_auth
async def notifications() -> Any:
    """Serve the notifications admin view.

    Returns:
        Rendered ``admin/notifications.html`` with the shared template context.
    """
    return await render_template(
        "admin/notifications.html", **await _shell_context("notifications")
    )


@admin.route("/profile")
@require_auth
async def profile() -> Any:
    """Serve the profile admin view.

    Returns:
        Rendered ``admin/profile.html`` with the shared template context.
    """
    return await render_template("admin/profile.html", **await _shell_context("profile"))


@admin.route("/likes")
@require_auth
async def likes() -> Any:
    """Serve the liked posts admin view.

    Returns:
        Rendered ``admin/likes.html`` with the shared template context.
    """
    return await render_template("admin/likes.html", **await _shell_context("likes"))


@admin.route("/following")
@require_auth
async def following() -> Any:
    """Serve the following list admin view.

    Returns:
        Rendered ``admin/following.html`` with the shared template context.
    """
    return await render_template("admin/following.html", **await _shell_context("following"))


@admin.route("/followers")
@require_auth
async def followers() -> Any:
    """Serve the followers list admin view.

    Returns:
        Rendered ``admin/followers.html`` with the shared template context.
    """
    return await render_template("admin/followers.html", **await _shell_context("followers"))
