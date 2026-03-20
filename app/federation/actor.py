"""Federation actor document builder.

Constructs the JSON-LD ActivityPub actor document for the local user,
including profile metadata from the settings service and the RSA public
key from the keypair service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.keypair import KeypairService
from app.services.settings import SettingsService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def build_actor_document(
    domain: str,
    username: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """Build the JSON-LD actor document for the local user.

    Assembles a full ActivityPub ``Person`` actor document by reading
    profile settings (display name, bio, avatar) and the RSA public key
    from the database via service classes.

    Args:
        domain: The instance domain name (e.g. ``"example.com"``).
        username: The local actor's username (e.g. ``"alice"``).
        session: An async database session for service layer access.

    Returns:
        A dictionary representing the JSON-LD actor document, ready to
        be serialised as JSON and returned to federation consumers.
    """
    settings = SettingsService(session)
    keypair = KeypairService(session)

    display_name = await settings.get_display_name()
    bio = await settings.get_bio()
    avatar = await settings.get_avatar()
    public_key_pem = await keypair.get_public_key()

    actor_id = f"https://{domain}/{username}"

    document: dict[str, Any] = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": actor_id,
        "type": "Person",
        "preferredUsername": username,
        "name": display_name,
        "summary": bio,
        "inbox": f"{actor_id}/inbox",
        "outbox": f"{actor_id}/outbox",
        "followers": f"{actor_id}/followers",
        "following": f"{actor_id}/following",
        "url": actor_id,
        "publicKey": {
            "id": f"{actor_id}#main-key",
            "owner": actor_id,
            "publicKeyPem": public_key_pem,
        },
    }

    if avatar:
        document["icon"] = {
            "type": "Image",
            "url": avatar,
        }

    return document
