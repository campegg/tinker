"""Public routes for the Tinker microblog.

Provides the public-facing HTTP endpoints including the actor profile page
(with content negotiation for ActivityPub), WebFinger discovery, and NodeInfo
metadata. These routes do not require authentication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quart import Blueprint, Response, abort, current_app, g, request

from app.federation.actor import build_actor_document
from app.repositories.note import NoteRepository
from app.services.settings import SettingsService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

public = Blueprint("public", __name__)

_AP_CONTENT_TYPE = "application/activity+json; charset=utf-8"
_JRD_CONTENT_TYPE = "application/jrd+json; charset=utf-8"

# Cache the profile template in memory after first read.
_profile_template_cache: str | None = None

# Cache the home template in memory after first read.
_home_template_cache: str | None = None


def _load_home_template() -> str:
    """Load the home page HTML template from disk, caching after first read.

    Returns:
        The raw HTML string for the home page.

    Raises:
        FileNotFoundError: If the template file does not exist at the
            expected path.
    """
    global _home_template_cache
    if _home_template_cache is not None:
        return _home_template_cache

    template_path = Path(current_app.static_folder or "static") / "pages" / "home.html"
    _home_template_cache = template_path.read_text(encoding="utf-8")
    return _home_template_cache


def _load_profile_template() -> str:
    """Load the profile HTML template from disk, caching after first read.

    Returns:
        The raw HTML string with ``{{placeholder}}`` markers.

    Raises:
        FileNotFoundError: If the template file does not exist at the
            expected path.
    """
    global _profile_template_cache
    if _profile_template_cache is not None:
        return _profile_template_cache

    template_path = Path(current_app.static_folder or "static") / "pages" / "profile.html"
    _profile_template_cache = template_path.read_text(encoding="utf-8")
    return _profile_template_cache


def _wants_json_ld(accept_header: str | None) -> bool:
    """Determine whether the client prefers an ActivityPub JSON-LD response.

    Checks whether the ``Accept`` header contains a media type associated
    with ActivityPub (``application/activity+json`` or
    ``application/ld+json``).

    Args:
        accept_header: The raw value of the HTTP ``Accept`` header, or
            ``None`` if the header was not sent.

    Returns:
        ``True`` if the client appears to want a JSON-LD response,
        ``False`` otherwise.
    """
    if accept_header is None:
        return False
    accept_lower = accept_header.lower()
    return "application/activity+json" in accept_lower or "application/ld+json" in accept_lower


def _render_links_html(links: list[str]) -> str:
    """Render a list of URL strings as HTML ``<li>`` elements.

    Each link is wrapped in an ``<a>`` tag with ``rel="noopener noreferrer"``
    and ``target="_blank"`` for security and UX.

    Args:
        links: A list of URL strings.

    Returns:
        A concatenated HTML string of ``<li>`` elements, or an empty
        string if the list is empty.
    """
    parts: list[str] = []
    for url in links:
        parts.append(
            f'<li><a href="{url}" rel="noopener noreferrer" target="_blank">{url}</a></li>'
        )
    return "\n        ".join(parts)


def _render_profile_html(
    template: str,
    *,
    display_name: str,
    bio: str,
    avatar_url: str,
    handle: str,
    links_html: str,
    domain: str,
) -> str:
    """Inject profile data into the HTML template via string interpolation.

    Replaces ``{{placeholder}}`` markers in the template with the
    corresponding profile values. This is deliberate simple string
    replacement — not a template engine.

    Args:
        template: The raw HTML template string.
        display_name: The author's display name.
        bio: The author's biography text.
        avatar_url: The URL to the avatar image, or an empty string.
        handle: The full fediverse handle (e.g. ``@user@domain``).
        links_html: Pre-rendered HTML ``<li>`` elements for profile links.
        domain: The instance domain name.

    Returns:
        The fully rendered HTML string ready to serve to the client.
    """
    html = template
    html = html.replace("{{display_name}}", display_name)
    html = html.replace("{{bio}}", bio)
    html = html.replace("{{avatar_url}}", avatar_url)
    html = html.replace("{{handle}}", handle)
    html = html.replace("{{links}}", links_html)
    html = html.replace("{{domain}}", domain)
    return html


@public.route("/", methods=["GET"])
async def home() -> Response:
    """Serve the home page.

    Returns:
        The static home HTML page.
    """
    return Response(
        response=_load_home_template(),
        status=200,
        content_type="text/html; charset=utf-8",
    )


@public.route("/<username>", methods=["GET"])
async def actor_profile(username: str) -> Response:
    """Serve the actor profile page or ActivityPub actor document.

    Performs content negotiation on the ``Accept`` header: ActivityPub
    consumers receive a JSON-LD actor document, while browsers receive
    the rendered HTML profile page.

    Args:
        username: The username path segment from the URL.

    Returns:
        Either a JSON-LD ``application/activity+json`` response or an
        HTML profile page.
    """
    configured_username: str = current_app.config["TINKER_USERNAME"]
    if username != configured_username:
        abort(404)

    domain: str = current_app.config["TINKER_DOMAIN"]
    session: AsyncSession = g.db_session

    if _wants_json_ld(request.headers.get("Accept")):
        actor_doc: dict[str, Any] = await build_actor_document(
            domain=domain,
            username=username,
            session=session,
        )
        return Response(
            response=_json_dumps(actor_doc),
            status=200,
            content_type=_AP_CONTENT_TYPE,
        )

    # Browser request — serve the profile HTML page.
    settings = SettingsService(session)
    display_name = await settings.get_display_name()
    bio = await settings.get_bio()
    avatar = await settings.get_avatar()
    links = await settings.get_links()

    template = _load_profile_template()
    html = _render_profile_html(
        template,
        display_name=display_name,
        bio=bio,
        avatar_url=avatar or "",
        handle=f"@{username}@{domain}",
        links_html=_render_links_html(links),
        domain=domain,
    )
    return Response(response=html, status=200, content_type="text/html; charset=utf-8")


@public.route("/.well-known/webfinger", methods=["GET"])
async def webfinger() -> Response:
    """Handle WebFinger discovery requests.

    Responds to ``acct:`` resource queries, allowing remote servers to
    discover the ActivityPub actor URI for a given handle.

    Returns:
        A JRD+JSON response with the actor's self link, or an
        appropriate error status.
    """
    resource = request.args.get("resource")
    if resource is None:
        abort(400)

    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]

    expected_resource = f"acct:{username}@{domain}"
    if resource != expected_resource:
        abort(404)

    body: dict[str, Any] = {
        "subject": expected_resource,
        "links": [
            {
                "rel": "self",
                "type": "application/activity+json",
                "href": f"https://{domain}/{username}",
            }
        ],
    }
    return Response(
        response=_json_dumps(body),
        status=200,
        content_type=_JRD_CONTENT_TYPE,
    )


@public.route("/.well-known/nodeinfo", methods=["GET"])
async def nodeinfo_discovery() -> Response:
    """Serve the NodeInfo well-known discovery document.

    Points clients to the full NodeInfo 2.0 document URL so that
    federation software can discover instance metadata.

    Returns:
        A JSON response containing the NodeInfo discovery links.
    """
    domain: str = current_app.config["TINKER_DOMAIN"]

    body: dict[str, Any] = {
        "links": [
            {
                "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                "href": f"https://{domain}/nodeinfo/2.0",
            }
        ]
    }
    return Response(
        response=_json_dumps(body),
        status=200,
        content_type="application/json; charset=utf-8",
    )


@public.route("/nodeinfo/2.0", methods=["GET"])
async def nodeinfo() -> Response:
    """Serve the NodeInfo 2.0 metadata document.

    Returns instance-level metadata including software identity, supported
    protocols, usage statistics, and registration status.

    Returns:
        A JSON response conforming to the NodeInfo 2.0 schema.
    """
    session: AsyncSession = g.db_session
    note_repo = NoteRepository(session)
    local_posts = await note_repo.count()

    body: dict[str, Any] = {
        "version": "2.0",
        "software": {
            "name": "tinker",
            "version": "0.1.0",
        },
        "protocols": ["activitypub"],
        "usage": {
            "users": {
                "total": 1,
                "activeMonth": 1,
                "activeHalfyear": 1,
            },
            "localPosts": local_posts,
        },
        "openRegistrations": False,
    }
    return Response(
        response=_json_dumps(body),
        status=200,
        content_type="application/json; charset=utf-8",
    )


def _json_dumps(obj: dict[str, Any]) -> str:
    """Serialise a dictionary to a JSON string.

    Uses the standard library ``json`` module with ``ensure_ascii=False``
    for clean Unicode output.

    Args:
        obj: The dictionary to serialise.

    Returns:
        A JSON-encoded string.
    """
    return json.dumps(obj, ensure_ascii=False)
