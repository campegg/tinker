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
from app.federation.outbox import AP_CONTEXT, build_create_activity, build_note_object
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

    In debug mode the cache is bypassed so that changes to the HTML file
    are visible immediately without restarting the server.

    Returns:
        The raw HTML string for the home page.

    Raises:
        FileNotFoundError: If the template file does not exist at the
            expected path.
    """
    global _home_template_cache
    if not current_app.debug and _home_template_cache is not None:
        return _home_template_cache

    template_path = Path(current_app.static_folder or "static") / "pages" / "home.html"
    content = template_path.read_text(encoding="utf-8")
    if not current_app.debug:
        _home_template_cache = content
    return content


def _load_profile_template() -> str:
    """Load the profile HTML template from disk, caching after first read.

    In debug mode the cache is bypassed so that changes to the HTML file
    are visible immediately without restarting the server.

    Returns:
        The raw HTML string with ``{{placeholder}}`` markers.

    Raises:
        FileNotFoundError: If the template file does not exist at the
            expected path.
    """
    global _profile_template_cache
    if not current_app.debug and _profile_template_cache is not None:
        return _profile_template_cache

    template_path = Path(current_app.static_folder or "static") / "pages" / "profile.html"
    content = template_path.read_text(encoding="utf-8")
    if not current_app.debug:
        _profile_template_cache = content
    return content


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
    actor_uri: str,
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
        actor_uri: The canonical ActivityPub actor URI, used as the href
            for the "Follow me" link.

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
    html = html.replace("{{actor_uri}}", actor_uri)
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
        actor_uri=f"https://{domain}/{username}",
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


_OUTBOX_PAGE_SIZE = 20


@public.route("/notes/<note_id>", methods=["GET"])
async def note_object(note_id: str) -> Response:
    """Serve the ActivityPub Note object or redirect browsers to home.

    Performs content negotiation on the ``Accept`` header: ActivityPub
    consumers receive a JSON-LD Note document while browsers are redirected
    to ``/`` (notes have no human-readable page — see §2.4 of the spec).

    Args:
        note_id: The UUID string of the note from the URL path.

    Returns:
        A JSON-LD ``application/activity+json`` response for AP consumers,
        or a ``302 Found`` redirect to ``/`` for browsers.
    """
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]
    ap_id = f"https://{domain}/notes/{note_id}"

    session: AsyncSession = g.db_session
    note_repo = NoteRepository(session)
    note = await note_repo.get_by_ap_id(ap_id)

    if note is None:
        abort(404)

    if not _wants_json_ld(request.headers.get("Accept")):
        return Response(
            response="",
            status=302,
            headers={"Location": "/"},
        )

    actor_uri = f"https://{domain}/{username}"
    note_doc = build_note_object(note, actor_uri)
    note_doc["@context"] = AP_CONTEXT

    return Response(
        response=_json_dumps(note_doc),
        status=200,
        content_type=_AP_CONTENT_TYPE,
    )


@public.route("/<username>/outbox", methods=["GET"])
async def outbox(username: str) -> Response:
    """Serve the ActivityPub outbox as an OrderedCollection.

    Returns the root collection (total item count and first-page link) when
    no pagination parameters are present, or an ``OrderedCollectionPage`` of
    ``Create{Note}`` activities when ``?page=true`` (first page) or
    ``?max_id=<ap_id>&page=true`` (subsequent pages) is set.

    Args:
        username: The username path segment from the URL.

    Returns:
        A JSON-LD ``application/activity+json`` response.
    """
    configured_username: str = current_app.config["TINKER_USERNAME"]
    if username != configured_username:
        abort(404)

    domain: str = current_app.config["TINKER_DOMAIN"]
    actor_uri = f"https://{domain}/{username}"
    outbox_url = f"{actor_uri}/outbox"

    session: AsyncSession = g.db_session
    note_repo = NoteRepository(session)

    page_param = request.args.get("page")
    max_id = request.args.get("max_id")

    if page_param is None and max_id is None:
        # Root collection: total count and first-page pointer only.
        # Per AP spec, the root does not include the items themselves.
        total = await note_repo.count()
        body: dict[str, Any] = {
            "@context": AP_CONTEXT,
            "id": outbox_url,
            "type": "OrderedCollection",
            "totalItems": total,
            "first": f"{outbox_url}?page=true",
        }
        return Response(
            response=_json_dumps(body),
            status=200,
            content_type=_AP_CONTENT_TYPE,
        )

    # Paginated page — first page or a cursor-based subsequent page.
    notes = await note_repo.get_page(
        limit=_OUTBOX_PAGE_SIZE,
        before_ap_id=max_id,  # None → first page; ap_id → subsequent page
    )

    activities: list[dict[str, Any]] = []
    for note in notes:
        activity = build_create_activity(note, actor_uri)
        # Strip the top-level @context from each activity: the page itself
        # carries the context, so embedding it on every item is redundant.
        activity.pop("@context", None)
        activities.append(activity)

    page_id = (
        f"{outbox_url}?page=true" if max_id is None else f"{outbox_url}?max_id={max_id}&page=true"
    )
    page_doc: dict[str, Any] = {
        "@context": AP_CONTEXT,
        "id": page_id,
        "type": "OrderedCollectionPage",
        "partOf": outbox_url,
        "orderedItems": activities,
    }

    # Include a "next" link when there may be more items beyond this page.
    if len(notes) == _OUTBOX_PAGE_SIZE:
        oldest = notes[-1]
        page_doc["next"] = f"{outbox_url}?max_id={oldest.ap_id}&page=true"

    return Response(
        response=_json_dumps(page_doc),
        status=200,
        content_type=_AP_CONTENT_TYPE,
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
