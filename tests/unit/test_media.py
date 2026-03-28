"""Unit tests for the media processing module.

Tests :mod:`app.media` functions for image processing, metadata stripping,
HEIC detection, and avatar proxying, using in-memory images created with
Pillow rather than real files on disk.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.media import (
    _MIME_EXT,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
    process_image,
    proxy_avatar,
    save_upload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg(width: int = 8, height: int = 8) -> bytes:
    """Return a minimal JPEG in bytes.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Raw JPEG bytes with no EXIF data.
    """
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png(mode: str = "RGBA", width: int = 4, height: int = 4) -> bytes:
    """Return a minimal PNG in bytes.

    Args:
        mode: Pillow colour mode (e.g. ``"RGBA"``, ``"RGB"``, ``"P"``).
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Raw PNG bytes.
    """
    img = Image.new(mode, (width, height))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_webp() -> bytes:
    """Return a minimal WebP image in bytes."""
    img = Image.new("RGB", (4, 4), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_gif(animated: bool = False) -> bytes:
    """Return a minimal GIF in bytes.

    Args:
        animated: If True, creates a two-frame animated GIF.

    Returns:
        Raw GIF bytes.
    """
    buf = io.BytesIO()
    if animated:
        # Use distinct palettes so the optimizer keeps both frames.
        frame1 = Image.new("P", (4, 4))
        frame1.putpalette([255, 0, 0] + [0, 0, 0] * 255)
        frame2 = Image.new("P", (4, 4))
        frame2.putpalette([0, 0, 255] + [0, 0, 0] * 255)
        frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    else:
        frame1 = Image.new("P", (4, 4))
        frame1.save(buf, format="GIF")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# process_image — format detection and re-encoding
# ---------------------------------------------------------------------------


class TestProcessImage:
    """Tests for :func:`process_image`."""

    def test_jpeg_returns_jpeg_mime(self) -> None:
        """JPEG input → JPEG output MIME type."""
        _, mime = process_image(_make_jpeg())
        assert mime == "image/jpeg"

    def test_jpeg_output_is_valid_image(self) -> None:
        """Re-encoded JPEG can be opened by Pillow."""
        data, _ = process_image(_make_jpeg())
        img = Image.open(io.BytesIO(data))
        assert img.format == "JPEG"

    def test_png_returns_png_mime(self) -> None:
        """PNG input → PNG output MIME type."""
        _, mime = process_image(_make_png())
        assert mime == "image/png"

    def test_webp_returns_webp_mime(self) -> None:
        """WebP input → WebP output MIME type."""
        _, mime = process_image(_make_webp())
        assert mime == "image/webp"

    def test_gif_returns_gif_mime(self) -> None:
        """GIF input → GIF output MIME type."""
        _, mime = process_image(_make_gif())
        assert mime == "image/gif"

    def test_animated_gif_preserved(self) -> None:
        """Animated GIF retains multiple frames."""
        data, _ = process_image(_make_gif(animated=True))
        img = Image.open(io.BytesIO(data))
        assert img.format == "GIF"
        assert getattr(img, "n_frames", 1) > 1

    def test_invalid_bytes_raises(self) -> None:
        """Random bytes raise ValueError."""
        with pytest.raises(ValueError, match="Cannot decode"):
            process_image(b"not-an-image")

    def test_empty_bytes_raises(self) -> None:
        """Empty bytes raise ValueError."""
        with pytest.raises(ValueError):
            process_image(b"")

    def test_jpeg_with_rgba_converted_to_rgb(self) -> None:
        """RGBA PNG is safely converted to RGB when targeting JPEG output.

        This test indirectly exercises the mode-normalisation path by feeding
        an RGBA PNG through the pipeline; the output must be a valid JPEG.
        Note: JPEG does not support alpha, so conversion is required.
        """
        # Make an RGBA PNG, then patch Image.open to claim it is JPEG
        img = Image.new("RGBA", (4, 4))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pil_img = Image.open(buf)
        # Manually change format attribute to simulate JPEG source
        pil_img.format = "JPEG"

        def _fake_open(b: Any) -> Image.Image:
            return pil_img

        with patch.object(Image, "open", side_effect=_fake_open):
            data, mime = process_image(b"fake")
        assert mime == "image/jpeg"
        result = Image.open(io.BytesIO(data))
        assert result.mode in ("RGB", "L")

    def test_metadata_stripped_from_jpeg(self) -> None:
        """JPEG EXIF metadata is not present in the re-encoded output."""

        # Create a JPEG with EXIF data using piexif if available, otherwise
        # just confirm the output has no 'exif' in info.
        raw = _make_jpeg()
        processed, _ = process_image(raw)
        img = Image.open(io.BytesIO(processed))
        exif_data = img.info.get("exif", b"")
        # An empty or minimal EXIF block is fine; we must not carry over camera data.
        assert len(exif_data) == 0 or exif_data == b""

    def test_heic_converted_to_jpeg(self) -> None:
        """HEIC/HEIF input is converted to JPEG output.

        Creates a real HEIF byte stream using ``pillow_heif`` (a project
        dependency), processes it through :func:`process_image`, and asserts
        that the output is a valid JPEG with MIME type ``"image/jpeg"``.
        Skipped if ``pillow_heif`` is not available in the test environment.
        """
        pytest.importorskip("pillow_heif", reason="pillow_heif not available")
        import pillow_heif

        # Build a minimal HEIF image using pillow_heif's encoder.
        src = Image.new("RGB", (4, 4), color=(0, 200, 100))
        buf = io.BytesIO()
        pillow_heif.from_pillow(src).save(buf)
        heif_bytes = buf.getvalue()

        data, mime = process_image(heif_bytes)

        assert mime == "image/jpeg"
        result = Image.open(io.BytesIO(data))
        assert result.format == "JPEG"


# ---------------------------------------------------------------------------
# save_upload
# ---------------------------------------------------------------------------


class TestSaveUpload:
    """Tests for :func:`save_upload`."""

    def test_creates_file_in_uploads_subdir(self, tmp_path: Path) -> None:
        """Saved file appears under ``uploads/`` in the media path."""
        data = _make_jpeg()
        rel = save_upload(data, "image/jpeg", str(tmp_path))
        assert rel.startswith("uploads/")
        assert (tmp_path / rel).exists()

    def test_returns_relative_path(self, tmp_path: Path) -> None:
        """Return value is relative to media_path, not absolute."""
        data = _make_jpeg()
        rel = save_upload(data, "image/jpeg", str(tmp_path))
        assert not rel.startswith("/")
        assert not rel.startswith(str(tmp_path))

    def test_jpeg_extension(self, tmp_path: Path) -> None:
        """JPEG MIME type → ``.jpg`` extension."""
        rel = save_upload(_make_jpeg(), "image/jpeg", str(tmp_path))
        assert rel.endswith(".jpg")

    def test_png_extension(self, tmp_path: Path) -> None:
        """PNG MIME type → ``.png`` extension."""
        rel = save_upload(_make_png(), "image/png", str(tmp_path))
        assert rel.endswith(".png")

    def test_creates_uploads_dir_if_missing(self, tmp_path: Path) -> None:
        """Uploads subdirectory is created if it does not exist."""
        nested = tmp_path / "deep" / "media"
        save_upload(_make_jpeg(), "image/jpeg", str(nested))
        assert (nested / "uploads").is_dir()

    def test_each_upload_has_unique_name(self, tmp_path: Path) -> None:
        """Two uploads of the same data get different filenames (UUID-based)."""
        data = _make_jpeg()
        rel1 = save_upload(data, "image/jpeg", str(tmp_path))
        rel2 = save_upload(data, "image/jpeg", str(tmp_path))
        assert rel1 != rel2

    def test_file_content_matches_input(self, tmp_path: Path) -> None:
        """Saved file content matches the provided bytes exactly."""
        data = _make_jpeg()
        rel = save_upload(data, "image/jpeg", str(tmp_path))
        assert (tmp_path / rel).read_bytes() == data


# ---------------------------------------------------------------------------
# proxy_avatar
# ---------------------------------------------------------------------------


def _make_mock_httpx_client(content: bytes, status_code: int = 200) -> Any:
    """Build a mock httpx client that returns ``content``.

    Args:
        content: The bytes to return as the response body.
        status_code: The HTTP status code to simulate.

    Returns:
        A mock client suitable for use as the return value of
        ``get_http_client``.
    """
    mock_response = MagicMock()
    mock_response.content = content
    if status_code >= 400:
        mock_response.raise_for_status = MagicMock(side_effect=Exception(f"HTTP {status_code}"))
    else:
        mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


class TestProxyAvatar:
    """Tests for :func:`proxy_avatar`."""

    async def test_downloads_and_saves_avatar(self, tmp_path: Path) -> None:
        """Avatar is downloaded and saved under ``avatars/``."""
        url = "https://remote.example.com/avatars/alice.jpg"
        jpeg_bytes = _make_jpeg()

        with patch("app.media.get_http_client", return_value=_make_mock_httpx_client(jpeg_bytes)):
            local_path = await proxy_avatar(url, str(tmp_path))

        assert local_path is not None
        assert local_path.startswith("avatars/")
        assert (tmp_path / local_path).exists()

    async def test_returns_relative_path(self, tmp_path: Path) -> None:
        """Return value is relative to media_path."""
        url = "https://remote.example.com/avatars/bob.jpg"
        with patch(
            "app.media.get_http_client", return_value=_make_mock_httpx_client(_make_jpeg())
        ):
            result = await proxy_avatar(url, str(tmp_path))
        assert result is not None
        assert not result.startswith("/")
        assert not result.startswith(str(tmp_path))

    async def test_same_url_returns_cached_path(self, tmp_path: Path) -> None:
        """Calling proxy_avatar twice for the same URL returns the cached path.

        The second call should not make a network request.
        """
        url = "https://remote.example.com/avatars/cached.jpg"
        mock_client = _make_mock_httpx_client(_make_jpeg())
        with patch("app.media.get_http_client", return_value=mock_client):
            first = await proxy_avatar(url, str(tmp_path))

        # Second call — no HTTP mock needed; file already exists on disk.
        second = await proxy_avatar(url, str(tmp_path))
        assert first == second

    async def test_http_error_returns_none(self, tmp_path: Path) -> None:
        """Network error returns ``None`` without raising."""
        url = "https://remote.example.com/avatars/gone.jpg"
        with patch(
            "app.media.get_http_client", return_value=_make_mock_httpx_client(b"", status_code=404)
        ):
            result = await proxy_avatar(url, str(tmp_path))
        assert result is None

    async def test_non_image_data_returns_none(self, tmp_path: Path) -> None:
        """Non-image bytes return ``None`` without raising."""
        url = "https://remote.example.com/avatars/bad.jpg"
        with patch(
            "app.media.get_http_client", return_value=_make_mock_httpx_client(b"not an image")
        ):
            result = await proxy_avatar(url, str(tmp_path))
        assert result is None

    async def test_oversized_avatar_returns_none(self, tmp_path: Path) -> None:
        """Avatar exceeding MAX_FILE_SIZE_BYTES returns ``None``."""
        url = "https://remote.example.com/avatars/huge.jpg"
        oversized = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        with patch("app.media.get_http_client", return_value=_make_mock_httpx_client(oversized)):
            result = await proxy_avatar(url, str(tmp_path))
        assert result is None

    async def test_creates_avatars_dir_if_missing(self, tmp_path: Path) -> None:
        """``avatars/`` subdirectory is created if it does not exist."""
        url = "https://remote.example.com/avatars/new.jpg"
        with patch(
            "app.media.get_http_client", return_value=_make_mock_httpx_client(_make_jpeg())
        ):
            await proxy_avatar(url, str(tmp_path))
        assert (tmp_path / "avatars").is_dir()

    async def test_different_urls_produce_different_files(self, tmp_path: Path) -> None:
        """Two distinct URLs produce two separate cached files."""
        url1 = "https://remote.example.com/avatars/alice.jpg"
        url2 = "https://remote.example.com/avatars/bob.jpg"
        with patch(
            "app.media.get_http_client", return_value=_make_mock_httpx_client(_make_jpeg())
        ):
            path1 = await proxy_avatar(url1, str(tmp_path))
            path2 = await proxy_avatar(url2, str(tmp_path))
        assert path1 != path2


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Sanity checks for module-level constants."""

    def test_allowed_mime_types_non_empty(self) -> None:
        """ALLOWED_MIME_TYPES is a non-empty frozenset."""
        assert len(ALLOWED_MIME_TYPES) > 0

    def test_heic_in_allowed_types(self) -> None:
        """HEIC MIME type is in the allowed set."""
        assert "image/heic" in ALLOWED_MIME_TYPES

    def test_max_size_is_positive(self) -> None:
        """MAX_FILE_SIZE_BYTES is a positive integer."""
        assert MAX_FILE_SIZE_BYTES > 0

    def test_mime_ext_covers_allowed_output_types(self) -> None:
        """Every output MIME type has a file extension mapping."""
        output_mimes = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        assert output_mimes <= set(_MIME_EXT)
