"""Application constants and environment-variable configuration.

This module is the single home for two distinct kinds of configuration:

1. **App-level constants** — values that are fixed at build time and do not
   vary between deployments (e.g. ``USER_AGENT``, ``PAGE_SIZE``).  Add new
   application constants here; never scatter them as module-level literals in
   the modules that use them.

2. **Infrastructure config** — deployment-specific values loaded from
   environment variables at startup via :func:`load_config`.  ``.env`` is
   reserved for these values only; application constants belong here, not
   in ``.env``.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# HTTP User-Agent header sent on all outbound federation and media requests.
USER_AGENT = "Tinker/0.1.0"

# Number of items returned per page by the admin JSON API endpoints
# (timeline, notifications, likes, followers, following).
PAGE_SIZE = 50

# Number of activities returned per page in the ActivityPub outbox collection.
OUTBOX_PAGE_SIZE = 20

# Number of actors returned per page in the ActivityPub followers/following collections.
COLLECTION_PAGE_SIZE = 50


def make_actor_uri(domain: str, username: str) -> str:
    """Return the canonical ActivityPub actor URI for a local user.

    Args:
        domain: The instance domain (e.g. ``"example.com"``).
        username: The local actor username.

    Returns:
        The canonical actor URI, e.g. ``"https://example.com/users/alice"``.
    """
    return f"https://{domain}/users/{username}"


def load_config() -> dict[str, str]:
    """Load configuration from environment variables.

    Loads a .env file from the project root if present, then reads
    the required TINKER_* environment variables. Returns a flat
    dictionary suitable for ``app.config.from_mapping()``.

    Returns:
        Dictionary of configuration values.
    """
    # Look for .env in the project root (parent of app/)
    project_root = Path(__file__).resolve().parent.parent.parent
    dotenv_path = project_root / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path)

    config: dict[str, str] = {
        "TINKER_DOMAIN": os.environ.get("TINKER_DOMAIN", "localhost"),
        "TINKER_DB_PATH": os.environ.get("TINKER_DB_PATH", "db/tinker.db"),
        "TINKER_MEDIA_PATH": os.environ.get("TINKER_MEDIA_PATH", "media/"),
        "TINKER_SECRET_KEY": os.environ.get("TINKER_SECRET_KEY", "change-me-in-production"),
        "TINKER_USERNAME": os.environ.get("TINKER_USERNAME", "admin"),
        # Optional: set an initial admin password on first run.  Once hashed and
        # stored in the database this variable is no longer consulted.
        "TINKER_ADMIN_PASSWORD": os.environ.get("TINKER_ADMIN_PASSWORD", ""),
    }

    # Quart uses SECRET_KEY for session signing
    config["SECRET_KEY"] = config["TINKER_SECRET_KEY"]

    return config
