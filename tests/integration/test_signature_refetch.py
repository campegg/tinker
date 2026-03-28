"""Integration test for HTTP Signature verification with re-fetch fallback.

Verifies the full flow: when signature verification fails against a cached
public key (simulating remote key rotation), the system re-fetches the actor
document to obtain the new key and retries verification successfully.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from app import create_app
from app.federation.signatures import sign_request, verify_signature
from app.models.remote_actor import RemoteActor
from app.services.remote_actor import RemoteActorService


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with a temporary database."""
    import os

    db_path = str(tmp_path / "test.db")
    os.environ["TINKER_DOMAIN"] = "local.example.com"
    os.environ["TINKER_DB_PATH"] = db_path
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = "testuser"

    application = create_app()

    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        yield application


@pytest.fixture
def _old_keypair() -> tuple[str, str]:
    """Generate an 'old' RSA keypair (the one cached locally)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return public_pem, private_pem


@pytest.fixture
def _new_keypair() -> tuple[str, str]:
    """Generate a 'new' RSA keypair (the rotated key on the remote server)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return public_pem, private_pem


REMOTE_ACTOR_URI = "https://remote.example.com/users/alice"
REMOTE_KEY_ID = f"{REMOTE_ACTOR_URI}#main-key"


def _build_actor_doc(public_key_pem: str) -> dict[str, Any]:
    """Build a minimal remote actor document with the given public key."""
    return {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": REMOTE_ACTOR_URI,
        "type": "Person",
        "preferredUsername": "alice",
        "name": "Alice",
        "inbox": f"{REMOTE_ACTOR_URI}/inbox",
        "outbox": f"{REMOTE_ACTOR_URI}/outbox",
        "endpoints": {"sharedInbox": "https://remote.example.com/inbox"},
        "publicKey": {
            "id": REMOTE_KEY_ID,
            "owner": REMOTE_ACTOR_URI,
            "publicKeyPem": public_key_pem,
        },
    }


class TestSignatureVerificationWithRefetch:
    """Test the full signature verification flow including key rotation fallback."""

    async def test_verify_succeeds_with_cached_key(
        self,
        _old_keypair: tuple[str, str],
    ) -> None:
        """Verification succeeds when the cached key matches the signing key."""
        old_public, old_private = _old_keypair
        body = json.dumps({"type": "Follow"}).encode("utf-8")

        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            body=body,
            private_key_pem=old_private,
            key_id=REMOTE_KEY_ID,
        )

        result = verify_signature(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=old_public,
        )
        assert result is True

    async def test_verify_fails_with_wrong_cached_key(
        self,
        _old_keypair: tuple[str, str],
        _new_keypair: tuple[str, str],
    ) -> None:
        """Verification fails when the cached key doesn't match (key rotated)."""
        old_public, _old_private = _old_keypair
        _new_public, new_private = _new_keypair
        body = json.dumps({"type": "Follow"}).encode("utf-8")

        # Sign with the NEW private key (remote has rotated)
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            body=body,
            private_key_pem=new_private,
            key_id=REMOTE_KEY_ID,
        )

        # Verify with the OLD public key (what we have cached) → should fail
        result = verify_signature(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=old_public,
        )
        assert result is False

    async def test_refetch_fallback_succeeds_after_key_rotation(
        self,
        _old_keypair: tuple[str, str],
        _new_keypair: tuple[str, str],
    ) -> None:
        """Full re-fetch flow: fail with cached key, refresh, succeed with new key."""
        old_public, _old_private = _old_keypair
        new_public, new_private = _new_keypair
        body = json.dumps({"type": "Follow", "actor": REMOTE_ACTOR_URI}).encode("utf-8")

        # The remote server signed with their NEW key
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            body=body,
            private_key_pem=new_private,
            key_id=REMOTE_KEY_ID,
        )

        # Step 1: Try verification with the OLD (cached) key → fails
        first_attempt = verify_signature(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=old_public,
        )
        assert first_attempt is False

        # Step 2: Simulate fetching the new actor document
        # In production, RemoteActorService.refresh() would do this via HTTP.
        # Here we mock the fetch to return a doc with the NEW public key.
        mock_session = AsyncMock()
        service = RemoteActorService(mock_session)

        new_actor_doc = _build_actor_doc(new_public)
        mock_response = MagicMock()
        mock_response.json.return_value = new_actor_doc
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.services.remote_actor.get_http_client",
            return_value=mock_client,
        ):
            fetched_doc = await service.fetch_actor_document(REMOTE_ACTOR_URI)

        assert fetched_doc is not None
        refreshed_public_key = fetched_doc["publicKey"]["publicKeyPem"]
        assert refreshed_public_key == new_public

        # Step 3: Retry verification with the NEW key → succeeds
        second_attempt = verify_signature(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=refreshed_public_key,
        )
        assert second_attempt is True

    async def test_refetch_fallback_fails_when_remote_unreachable(
        self,
        _old_keypair: tuple[str, str],
        _new_keypair: tuple[str, str],
    ) -> None:
        """Re-fetch returns None when the remote server is unreachable."""
        old_public, _old_private = _old_keypair
        _new_public, new_private = _new_keypair
        body = json.dumps({"type": "Like"}).encode("utf-8")

        # Signed with new key, but we only have the old key cached
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            body=body,
            private_key_pem=new_private,
            key_id=REMOTE_KEY_ID,
        )

        # First attempt fails
        assert (
            verify_signature(
                method="POST",
                url="https://local.example.com/testuser/inbox",
                headers=signed_headers,
                body=body,
                public_key_pem=old_public,
            )
            is False
        )

        # Simulate a failed refresh (remote server down)
        import httpx

        mock_session = AsyncMock()
        service = RemoteActorService(mock_session)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.services.remote_actor.get_http_client",
            return_value=mock_client,
        ):
            result = await service.fetch_actor_document(REMOTE_ACTOR_URI)

        assert result is None

    async def test_refetch_updates_cached_actor(
        self,
        app: Quart,
        _old_keypair: tuple[str, str],
        _new_keypair: tuple[str, str],
    ) -> None:
        """After re-fetch, the cached actor record has the new public key."""
        old_public, _old_private = _old_keypair
        new_public, _new_private = _new_keypair

        # Seed a cached actor with the old key
        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            session = session_factory()
            try:
                from app.repositories.remote_actor import RemoteActorRepository

                repo = RemoteActorRepository(session)
                old_actor = RemoteActor(
                    uri=REMOTE_ACTOR_URI,
                    display_name="Alice",
                    handle="alice@remote.example.com",
                    inbox_url=f"{REMOTE_ACTOR_URI}/inbox",
                    shared_inbox_url="https://remote.example.com/inbox",
                    public_key=old_public,
                    fetched_at=datetime.now(UTC) - timedelta(hours=1),
                )
                await repo.add(old_actor)
                await repo.commit()
            finally:
                await session.close()

        # Now refresh with a mocked HTTP response containing the new key
        new_actor_doc = _build_actor_doc(new_public)
        mock_response = MagicMock()
        mock_response.json.return_value = new_actor_doc
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async with app.app_context():
            session = session_factory()
            try:
                service = RemoteActorService(session)

                with patch(
                    "app.services.remote_actor.get_http_client",
                    return_value=mock_client,
                ):
                    refreshed = await service.refresh(REMOTE_ACTOR_URI)

                assert refreshed is not None
                assert refreshed.public_key == new_public
                assert refreshed.uri == REMOTE_ACTOR_URI

                # Verify the cached version also has the new key
                cached = await service._repo.get_by_uri(REMOTE_ACTOR_URI)
                assert cached is not None
                assert cached.public_key == new_public
            finally:
                await session.close()

    async def test_end_to_end_verify_refetch_verify(
        self,
        app: Quart,
        _old_keypair: tuple[str, str],
        _new_keypair: tuple[str, str],
    ) -> None:
        """End-to-end: verify fails → refetch actor → verify succeeds."""
        old_public, _old_private = _old_keypair
        new_public, new_private = _new_keypair

        body = json.dumps(
            {
                "type": "Create",
                "actor": REMOTE_ACTOR_URI,
                "object": {"type": "Note", "content": "Hello!"},
            }
        ).encode("utf-8")

        # Remote signs with new key after rotation
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/testuser/inbox",
            body=body,
            private_key_pem=new_private,
            key_id=REMOTE_KEY_ID,
        )

        # Seed a cached actor with the OLD key
        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            session = session_factory()
            try:
                from app.repositories.remote_actor import RemoteActorRepository

                repo = RemoteActorRepository(session)
                cached_actor = RemoteActor(
                    uri=REMOTE_ACTOR_URI,
                    display_name="Alice",
                    handle="alice@remote.example.com",
                    inbox_url=f"{REMOTE_ACTOR_URI}/inbox",
                    public_key=old_public,
                    fetched_at=datetime.now(UTC),
                )
                await repo.add(cached_actor)
                await repo.commit()
            finally:
                await session.close()

        # Step 1: Attempt verification with cached (old) key → FAIL
        assert (
            verify_signature(
                method="POST",
                url="https://local.example.com/testuser/inbox",
                headers=signed_headers,
                body=body,
                public_key_pem=old_public,
            )
            is False
        )

        # Step 2: Refresh the actor (simulate the re-fetch)
        new_actor_doc = _build_actor_doc(new_public)
        mock_response = MagicMock()
        mock_response.json.return_value = new_actor_doc
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async with app.app_context():
            session = session_factory()
            try:
                service = RemoteActorService(session)

                with patch(
                    "app.services.remote_actor.get_http_client",
                    return_value=mock_client,
                ):
                    refreshed_actor = await service.refresh(REMOTE_ACTOR_URI)

                assert refreshed_actor is not None
                refreshed_key = refreshed_actor.public_key
            finally:
                await session.close()

        # Step 3: Retry verification with refreshed key → SUCCESS
        assert (
            verify_signature(
                method="POST",
                url="https://local.example.com/testuser/inbox",
                headers=signed_headers,
                body=body,
                public_key_pem=refreshed_key,
            )
            is True
        )
