"""Integration tests for public routes: actor, WebFinger, NodeInfo, and profile."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from quart import Quart

from app import create_app


@pytest.fixture(autouse=True)
def _reset_template_cache() -> None:
    """Reset the template caches between tests."""
    import sys

    routes_module = sys.modules.get("app.public.routes")
    if routes_module is not None:
        routes_module._profile_template_cache = None  # type: ignore[attr-defined]
        routes_module._home_template_cache = None  # type: ignore[attr-defined]


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with a temporary database."""
    import os

    db_path = str(tmp_path / "test.db")
    os.environ["TINKER_DOMAIN"] = "test.example.com"
    os.environ["TINKER_DB_PATH"] = db_path
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = "testuser"

    application = create_app()

    # Create tables in the temp database
    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        yield application


@pytest.fixture
async def client(app: Quart) -> Any:
    """Create a test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Keypair mock helper
# ---------------------------------------------------------------------------

_FAKE_PUBLIC_KEY = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Rdj53hR4AdsiRcqt1zd
fake+key+for+testing+only+xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
xwIDAQAB
-----END PUBLIC KEY-----"""


def _mock_keypair_get_public_key() -> AsyncMock:
    mock = AsyncMock(return_value=_FAKE_PUBLIC_KEY)
    return mock


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------


class TestHomePage:
    """Tests for GET /."""

    async def test_returns_200(self, client: Any) -> None:
        """GET / returns 200 OK."""
        async with client as c:
            resp = await c.get("/")
        assert resp.status_code == 200

    async def test_returns_html(self, client: Any) -> None:
        """GET / returns an HTML response."""
        async with client as c:
            resp = await c.get("/")
        assert "text/html" in resp.content_type

    async def test_contains_heading(self, client: Any) -> None:
        """GET / response contains the main heading."""
        async with client as c:
            resp = await c.get("/")
        body = await resp.get_data(as_text=True)
        assert "home-heading" in body

    async def test_contains_icons(self, client: Any) -> None:
        """GET / response contains the icon buttons."""
        async with client as c:
            resp = await c.get("/")
        body = await resp.get_data(as_text=True)
        assert 'data-icon="mail"' in body
        assert 'data-icon="mastodon"' in body
        assert 'data-icon="bluesky"' in body


# ---------------------------------------------------------------------------
# WebFinger
# ---------------------------------------------------------------------------


class TestWebFinger:
    async def test_returns_jrd_json_for_valid_resource(self, client: Any) -> None:
        response = await client.get(
            "/.well-known/webfinger?resource=acct:testuser@test.example.com"
        )
        assert response.status_code == 200
        assert "application/jrd+json" in response.content_type

        data = await response.get_json()
        assert data["subject"] == "acct:testuser@test.example.com"
        assert data["aliases"] == ["https://test.example.com/testuser"]
        assert len(data["links"]) == 2
        self_link = data["links"][0]
        assert self_link["rel"] == "self"
        assert self_link["type"] == "application/activity+json"
        assert self_link["href"] == "https://test.example.com/testuser"
        profile_link = data["links"][1]
        assert profile_link["rel"] == "http://webfinger.net/rel/profile-page"
        assert profile_link["type"] == "text/html"
        assert profile_link["href"] == "https://test.example.com/@testuser"

    async def test_returns_404_for_unknown_user(self, client: Any) -> None:
        response = await client.get("/.well-known/webfinger?resource=acct:nobody@test.example.com")
        assert response.status_code == 404

    async def test_returns_404_for_wrong_domain(self, client: Any) -> None:
        response = await client.get(
            "/.well-known/webfinger?resource=acct:testuser@wrong.example.com"
        )
        assert response.status_code == 404

    async def test_returns_400_when_resource_missing(self, client: Any) -> None:
        response = await client.get("/.well-known/webfinger")
        assert response.status_code == 400

    async def test_self_link_matches_actor_endpoint(self, client: Any) -> None:
        response = await client.get(
            "/.well-known/webfinger?resource=acct:testuser@test.example.com"
        )
        data = await response.get_json()
        actor_href = data["links"][0]["href"]
        assert actor_href == "https://test.example.com/testuser"


# ---------------------------------------------------------------------------
# NodeInfo
# ---------------------------------------------------------------------------


class TestNodeInfoDiscovery:
    async def test_returns_discovery_document(self, client: Any) -> None:
        response = await client.get("/.well-known/nodeinfo")
        assert response.status_code == 200
        assert "application/json" in response.content_type

        data = await response.get_json()
        assert "links" in data
        assert len(data["links"]) == 1

        link = data["links"][0]
        assert link["rel"] == "http://nodeinfo.diaspora.software/ns/schema/2.0"
        assert link["href"] == "https://test.example.com/nodeinfo/2.0"


class TestNodeInfoDocument:
    async def test_returns_valid_nodeinfo(self, client: Any) -> None:
        response = await client.get("/nodeinfo/2.0")
        assert response.status_code == 200
        assert "application/json" in response.content_type

        data = await response.get_json()
        assert data["version"] == "2.0"
        assert data["software"]["name"] == "tinker"
        assert data["software"]["version"] == "0.1.0"
        assert "activitypub" in data["protocols"]
        assert data["openRegistrations"] is False

    async def test_reports_correct_user_counts(self, client: Any) -> None:
        response = await client.get("/nodeinfo/2.0")
        data = await response.get_json()
        users = data["usage"]["users"]
        assert users["total"] == 1
        assert users["activeMonth"] == 1
        assert users["activeHalfyear"] == 1

    async def test_reports_zero_posts_initially(self, client: Any) -> None:
        response = await client.get("/nodeinfo/2.0")
        data = await response.get_json()
        assert data["usage"]["localPosts"] == 0


# ---------------------------------------------------------------------------
# Actor document (JSON-LD)
# ---------------------------------------------------------------------------


class TestActorDocument:
    async def test_returns_json_ld_for_ap_accept_header(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        assert response.status_code == 200
        assert "application/activity+json" in response.content_type

    async def test_returns_json_ld_for_ld_json_accept(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={
                    "Accept": 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"'
                },
            )

        assert response.status_code == 200
        assert "application/activity+json" in response.content_type

    async def test_actor_document_has_required_fields(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        data = await response.get_json()

        # Required ActivityPub Person fields
        assert data["@context"] == [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ]
        assert data["type"] == "Person"
        assert data["id"] == "https://test.example.com/testuser"
        assert data["preferredUsername"] == "testuser"
        assert data["inbox"] == "https://test.example.com/testuser/inbox"
        assert data["outbox"] == "https://test.example.com/testuser/outbox"
        assert data["followers"] == "https://test.example.com/testuser/followers"
        assert data["following"] == "https://test.example.com/testuser/following"
        assert data["url"] == "https://test.example.com/testuser"

    async def test_actor_document_includes_public_key(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        data = await response.get_json()
        pk = data["publicKey"]
        assert pk["id"] == "https://test.example.com/testuser#main-key"
        assert pk["owner"] == "https://test.example.com/testuser"
        assert pk["publicKeyPem"] == _FAKE_PUBLIC_KEY

    async def test_actor_document_includes_name_and_summary(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        data = await response.get_json()
        # Settings are empty by default but should be present
        assert "name" in data
        assert "summary" in data

    async def test_actor_document_omits_icon_when_no_avatar(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        data = await response.get_json()
        assert "icon" not in data

    async def test_actor_document_includes_icon_when_avatar_set(
        self, client: Any, app: Quart
    ) -> None:
        # Seed an avatar setting
        from app.repositories.settings import SettingsRepository

        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                repo = SettingsRepository(session)
                await repo.set_value("avatar", "uploads/avatar.jpg")
                await repo.commit()
            finally:
                await session.close()

        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        data = await response.get_json()
        assert "icon" in data
        assert data["icon"]["type"] == "Image"
        assert data["icon"]["url"] == "https://test.example.com/media/uploads/avatar.jpg"

    async def test_returns_404_for_wrong_username(self, client: Any) -> None:
        response = await client.get(
            "/wronguser",
            headers={"Accept": "application/activity+json"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Profile HTML page
# ---------------------------------------------------------------------------


class TestProfileHTMLPage:
    async def test_returns_html_for_browser_request(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        assert response.status_code == 200
        assert "text/html" in response.content_type

    async def test_returns_html_when_no_accept_header(self, client: Any) -> None:
        response = await client.get("/testuser")
        assert response.status_code == 200
        assert "text/html" in response.content_type

    async def test_html_contains_handle(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "@testuser@test.example.com" in body

    async def test_html_contains_domain(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "test.example.com" in body

    async def test_html_contains_display_name_when_set(self, client: Any, app: Quart) -> None:
        # Seed a display name
        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                from app.repositories.settings import SettingsRepository

                repo = SettingsRepository(session)
                await repo.set_value("display_name", "Alice Tester")
                await repo.commit()
            finally:
                await session.close()

        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "Alice Tester" in body

    async def test_html_contains_bio_when_set(self, client: Any, app: Quart) -> None:
        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                from app.repositories.settings import SettingsRepository

                repo = SettingsRepository(session)
                await repo.set_value("bio", "I build things on the web.")
                await repo.commit()
            finally:
                await session.close()

        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "I build things on the web." in body

    async def test_html_contains_links_when_set(self, client: Any, app: Quart) -> None:
        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                from app.repositories.settings import SettingsRepository

                repo = SettingsRepository(session)
                await repo.set_value(
                    "links", json.dumps(["https://github.com/alice", "https://alice.blog"])
                )
                await repo.commit()
            finally:
                await session.close()

        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "https://github.com/alice" in body
        assert "https://alice.blog" in body

    async def test_html_contains_follow_me_link(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        # The follow link must be present and point to the actor's AP URI.
        assert 'href="https://test.example.com/testuser"' in body
        assert "Follow me" in body

    async def test_returns_404_for_wrong_username_html(self, client: Any) -> None:
        response = await client.get(
            "/wronguser",
            headers={"Accept": "text/html"},
        )
        assert response.status_code == 404

    async def test_html_escapes_display_name_xss(self, client: Any, app: Quart) -> None:
        """display_name containing HTML tags must be escaped, not injected raw."""
        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                from app.repositories.settings import SettingsRepository

                repo = SettingsRepository(session)
                await repo.set_value("display_name", "<script>alert(1)</script>")
                await repo.commit()
            finally:
                await session.close()

        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

    async def test_html_drops_javascript_scheme_links(self, client: Any, app: Quart) -> None:
        """Links with a javascript: scheme must be dropped; safe https: links must survive."""
        async with app.app_context():
            session = app.config["DB_SESSION_FACTORY"]()
            try:
                from app.repositories.settings import SettingsRepository

                repo = SettingsRepository(session)
                await repo.set_value(
                    "links",
                    json.dumps(["javascript:alert(1)", "https://safe.example.com"]),
                )
                await repo.commit()
            finally:
                await session.close()

        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        body = await response.get_data(as_text=True)
        assert "javascript:" not in body
        assert "https://safe.example.com" in body


# ---------------------------------------------------------------------------
# Content negotiation
# ---------------------------------------------------------------------------


class TestContentNegotiation:
    async def test_ap_accept_gets_json(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )
        assert "application/activity+json" in response.content_type

    async def test_html_accept_gets_html(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )
        assert "text/html" in response.content_type

    async def test_wildcard_accept_gets_html(self, client: Any) -> None:
        response = await client.get(
            "/testuser",
            headers={"Accept": "*/*"},
        )
        assert "text/html" in response.content_type

    async def test_no_accept_header_gets_html(self, client: Any) -> None:
        response = await client.get("/testuser")
        assert "text/html" in response.content_type

    async def test_mixed_accept_with_ap_gets_json(self, client: Any) -> None:
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "text/html, application/activity+json;q=0.9"},
            )
        # Our implementation treats any mention of activity+json as AP
        assert "application/activity+json" in response.content_type


# ---------------------------------------------------------------------------
# WebFinger → Actor round-trip
# ---------------------------------------------------------------------------


class TestWebFingerActorRoundTrip:
    async def test_webfinger_self_link_resolves_to_actor(self, client: Any) -> None:
        """Verify the full discovery flow: WebFinger → Actor document."""
        # Step 1: Discover via WebFinger
        wf_response = await client.get(
            "/.well-known/webfinger?resource=acct:testuser@test.example.com"
        )
        wf_data = await wf_response.get_json()
        actor_url = wf_data["links"][0]["href"]

        # The actor URL path is /{username}
        path = "/" + actor_url.split("/")[-1]

        # Step 2: Fetch the actor document
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            actor_response = await client.get(
                path,
                headers={"Accept": "application/activity+json"},
            )

        assert actor_response.status_code == 200
        actor_data = await actor_response.get_json()
        assert actor_data["id"] == actor_url
        assert actor_data["type"] == "Person"
        assert "publicKey" in actor_data


# ---------------------------------------------------------------------------
# Vary: Accept header (content negotiation correctness)
# ---------------------------------------------------------------------------


class TestVaryHeader:
    """Verify that content-negotiated actor profile responses include Vary: Accept.

    Without ``Vary: Accept``, an intermediate cache (e.g. Caddy) may serve a
    cached HTML response to a Mastodon AP fetch, or vice versa — a silent
    federation failure with no error logged anywhere.
    """

    async def test_json_ld_response_includes_vary_accept(self, client: Any) -> None:
        """AP JSON-LD actor response carries Vary: Accept."""
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={"Accept": "application/activity+json"},
            )

        assert response.status_code == 200
        vary = response.headers.get("Vary", "")
        assert "Accept" in vary, f"Expected 'Accept' in Vary header, got: {vary!r}"

    async def test_html_response_includes_vary_accept(self, client: Any) -> None:
        """Browser HTML actor response carries Vary: Accept."""
        response = await client.get(
            "/testuser",
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 200
        vary = response.headers.get("Vary", "")
        assert "Accept" in vary, f"Expected 'Accept' in Vary header, got: {vary!r}"

    async def test_vary_header_present_with_no_accept(self, client: Any) -> None:
        """Actor profile response without an Accept header still carries Vary: Accept."""
        response = await client.get("/testuser")

        assert response.status_code == 200
        vary = response.headers.get("Vary", "")
        assert "Accept" in vary, f"Expected 'Accept' in Vary header, got: {vary!r}"

    async def test_vary_header_present_for_ld_json_accept(self, client: Any) -> None:
        """AP ld+json actor response also carries Vary: Accept."""
        with patch(
            "app.services.keypair.KeypairService.get_public_key",
            _mock_keypair_get_public_key(),
        ):
            response = await client.get(
                "/testuser",
                headers={
                    "Accept": 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"'
                },
            )

        assert response.status_code == 200
        vary = response.headers.get("Vary", "")
        assert "Accept" in vary, f"Expected 'Accept' in Vary header, got: {vary!r}"
