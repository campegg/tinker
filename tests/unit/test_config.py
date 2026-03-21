"""Tests for the configuration module."""

import os
from pathlib import Path
from unittest.mock import patch

from app.core.config import load_config


class TestLoadConfig:
    """Tests for the load_config function."""

    def test_returns_dict(self) -> None:
        """load_config returns a dictionary."""
        config = load_config()
        assert isinstance(config, dict)

    def test_default_values(self) -> None:
        """Config uses sensible defaults when env vars are missing."""
        env_vars = {
            "TINKER_DOMAIN",
            "TINKER_DB_PATH",
            "TINKER_MEDIA_PATH",
            "TINKER_SECRET_KEY",
            "TINKER_USERNAME",
        }
        cleaned = {k: v for k, v in os.environ.items() if k not in env_vars}
        with patch("app.core.config.load_dotenv"), patch.dict(os.environ, cleaned, clear=True):
            config = load_config()

        assert config["TINKER_DOMAIN"] == "localhost"
        assert config["TINKER_DB_PATH"] == "db/tinker.db"
        assert config["TINKER_MEDIA_PATH"] == "media/"
        assert config["TINKER_SECRET_KEY"] == "change-me-in-production"
        assert config["TINKER_USERNAME"] == "admin"

    def test_reads_environment_variables(self) -> None:
        """Config picks up TINKER_* environment variables."""
        overrides = {
            "TINKER_DOMAIN": "example.com",
            "TINKER_DB_PATH": "/tmp/test.db",
            "TINKER_MEDIA_PATH": "/tmp/media/",
            "TINKER_SECRET_KEY": "super-secret",
            "TINKER_USERNAME": "alice",
        }
        with patch.dict(os.environ, overrides):
            config = load_config()

        for key, value in overrides.items():
            assert config[key] == value

    def test_secret_key_forwarded(self) -> None:
        """SECRET_KEY is set from TINKER_SECRET_KEY for Quart."""
        with patch.dict(os.environ, {"TINKER_SECRET_KEY": "my-secret"}):
            config = load_config()

        assert config["SECRET_KEY"] == "my-secret"

    def test_loads_dotenv_file(self, tmp_path: Path) -> None:
        """Config loads a .env file from the project root when present."""
        # Create a fake .env file in a directory structure that mimics
        # the real project layout: <root>/app/core/config.py
        # load_config resolves .env relative to config.py's grandparent.
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("TINKER_DOMAIN=from-dotenv.example\n")

        # Patch Path so the resolved project root points to tmp_path
        with patch("app.core.config.Path") as mock_path_cls:
            mock_file = mock_path_cls.return_value
            mock_resolved = mock_file.resolve.return_value
            mock_resolved.parent.parent.parent = tmp_path

            # Also clear the env var so default would be used without .env
            env_without = {k: v for k, v in os.environ.items() if k != "TINKER_DOMAIN"}
            with patch.dict(os.environ, env_without, clear=True):
                config = load_config()

        assert config["TINKER_DOMAIN"] == "from-dotenv.example"

    def test_all_required_keys_present(self) -> None:
        """Config dictionary contains all expected keys."""
        config = load_config()
        expected_keys = {
            "TINKER_DOMAIN",
            "TINKER_DB_PATH",
            "TINKER_MEDIA_PATH",
            "TINKER_SECRET_KEY",
            "TINKER_USERNAME",
            "SECRET_KEY",
        }
        assert expected_keys.issubset(config.keys())
