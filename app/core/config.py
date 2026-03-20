"""Configuration module for loading environment variables.

Loads infrastructure-level configuration from environment variables,
with .env file support for local development.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


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
    }

    # Quart uses SECRET_KEY for session signing
    config["SECRET_KEY"] = config["TINKER_SECRET_KEY"]

    return config
