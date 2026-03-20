"""Auth-gated admin page routes.

Serves the static HTML shells for admin views. All routes in this blueprint
require an authenticated session via the :func:`~app.admin.auth.require_auth`
decorator. The HTML shells load Web Components that fetch data from the JSON
API endpoints defined in ``app/admin/api.py``.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, redirect

from app.admin.auth import require_auth

admin = Blueprint("admin", __name__, url_prefix="/admin")


@admin.route("/")
@require_auth
async def index() -> Any:
    """Redirect to the timeline — the primary admin view.

    Returns:
        A redirect to the timeline page.
    """
    return redirect("/admin/timeline")
