"""Media upload processing, storage, and remote avatar proxying.

Handles image validation, metadata stripping, format conversion (HEIC → JPEG),
optimisation, local file storage, and transparent fetching of remote avatars
to prevent the client from loading remote URLs directly.

Supported input formats: JPEG, PNG, WebP, GIF, HEIC/HEIF.
HEIC/HEIF images are converted to JPEG on upload.
All other formats are re-encoded in their original format.

All saved images have their EXIF, IPTC, and XMP metadata stripped.
Single file per upload — no derivative sizes are generated.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from pathlib import Path

import httpx
from PIL import Image

from app.core.config import USER_AGENT

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIF_SUPPORT = True
except Exception:
    _HEIF_SUPPORT = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum permitted upload size: 10 MiB.
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

# MIME types accepted for upload. Keyed on the string clients send in
# Content-Type; values are the canonical Pillow format string.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/heic",
        "image/heif",
    }
)

# Map Pillow format → MIME type of the saved file.
# HEIF input is always saved as JPEG.
_FORMAT_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "HEIF": "image/jpeg",
}

# Extension to use for each MIME type when writing to disk.
_MIME_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_AVATAR_FETCH_TIMEOUT: float = 10.0

# Subdirectory names under TINKER_MEDIA_PATH.
_UPLOADS_SUBDIR = "uploads"
_AVATARS_SUBDIR = "avatars"


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------


def process_image(data: bytes) -> tuple[bytes, str]:
    """Validate, strip metadata, and re-encode an uploaded image.

    Opens the image with Pillow (which also validates that the data is a
    real image), determines the output format, strips all metadata by
    reconstructing the image from raw pixel data, and encodes to the
    output format.

    HEIC/HEIF input is converted to JPEG. All other formats are preserved.

    Args:
        data: The raw bytes of the uploaded file.

    Returns:
        A tuple of ``(processed_bytes, output_mime_type)``.

    Raises:
        ValueError: If the image cannot be opened or is not an allowed format.
    """
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Cannot decode image data: {exc}") from exc

    fmt = img.format
    if fmt not in _FORMAT_MIME:
        raise ValueError(
            f"Unsupported image format {fmt!r}. Allowed: {', '.join(sorted(_FORMAT_MIME))}."
        )

    output_format = "JPEG" if fmt == "HEIF" else fmt
    output_mime = _FORMAT_MIME[fmt]

    processed = _strip_and_encode(img, output_format)
    return processed, output_mime


def _strip_and_encode(img: Image.Image, output_format: str) -> bytes:
    """Strip all metadata from ``img`` and encode to ``output_format``.

    Creates a fresh :class:`~PIL.Image.Image` from the raw pixel data so
    that no EXIF, IPTC, XMP, ICC profile, or other metadata blocks survive
    into the output file.

    Animated GIFs are saved frame-by-frame using Pillow's ``save_all``
    option to preserve animation; their comment and extension blocks are
    dropped.

    Args:
        img: The source Pillow image (opened from uploaded bytes).
        output_format: Pillow format string for the output
            (``"JPEG"``, ``"PNG"``, ``"WEBP"``, or ``"GIF"``).

    Returns:
        The re-encoded image as raw bytes.
    """
    buf = io.BytesIO()

    if output_format == "GIF":
        _save_gif(img, buf)
        return buf.getvalue()

    # Normalize colour mode for the target format.
    if output_format == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    elif output_format in ("PNG", "WEBP") and img.mode == "P":
        img = img.convert("RGBA")

    # Build a fresh image with only raw pixel data — no info dict, no EXIF.
    clean = Image.new(img.mode, img.size)
    clean.putdata(img.getdata())

    if output_format == "JPEG":
        clean.save(buf, format="JPEG", quality=85, optimize=True)
    elif output_format == "PNG":
        clean.save(buf, format="PNG", optimize=True)
    elif output_format == "WEBP":
        clean.save(buf, format="WEBP", quality=85, method=6)

    return buf.getvalue()


def _save_gif(img: Image.Image, buf: io.BytesIO) -> None:
    """Save a GIF (possibly animated) into ``buf`` without metadata.

    For animated GIFs, collects all frames and saves with ``save_all``.
    For static GIFs, saves a single clean frame.

    Args:
        img: The source GIF image.
        buf: The output buffer.
    """
    n_frames: int = getattr(img, "n_frames", 1)

    if n_frames == 1:
        clean = Image.new(img.mode, img.size)
        clean.putdata(img.getdata())
        clean.save(buf, format="GIF")
        return

    frames: list[Image.Image] = []
    durations: list[int] = []

    for i in range(n_frames):
        img.seek(i)
        frame = img.copy()
        frames.append(frame)
        durations.append(img.info.get("duration", 100))

    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
    )


# ---------------------------------------------------------------------------
# File storage
# ---------------------------------------------------------------------------


def save_upload(data: bytes, mime_type: str, media_path: str) -> str:
    """Write processed image bytes to the uploads directory.

    Creates the uploads subdirectory if it does not exist.  The output
    filename is a random UUID with the appropriate extension.

    Args:
        data: Processed image bytes (output of :func:`process_image`).
        mime_type: MIME type of the processed data (used to choose extension).
        media_path: The configured media root directory path.

    Returns:
        The path relative to ``media_path``
        (e.g. ``"uploads/abc123.jpg"``).

    Raises:
        KeyError: If ``mime_type`` is not in the known extension map.
    """
    ext = _MIME_EXT[mime_type]
    uploads_dir = Path(media_path) / _UPLOADS_SUBDIR
    uploads_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4()}{ext}"
    dest = uploads_dir / filename
    dest.write_bytes(data)
    logger.debug("Saved upload: %s (%d bytes)", dest, len(data))
    return f"{_UPLOADS_SUBDIR}/{filename}"


# ---------------------------------------------------------------------------
# Avatar proxying
# ---------------------------------------------------------------------------


async def proxy_avatar(remote_url: str, media_path: str) -> str | None:
    """Fetch a remote avatar URL and cache it locally.

    Downloads the image at ``remote_url``, strips its metadata via
    :func:`process_image`, and saves it under
    ``{media_path}/avatars/{hash}.{ext}``.  Filename is derived from a
    SHA-256 hash of the URL so the same remote URL always maps to the
    same local file and re-downloading is idempotent.

    Returns the cached file if it already exists on disk (skip download).

    Args:
        remote_url: The full URL of the remote avatar image.
        media_path: The configured media root directory path.

    Returns:
        The path relative to ``media_path``
        (e.g. ``"avatars/abc123.jpg"``), or ``None`` if the download
        or processing fails.
    """
    avatars_dir = Path(media_path) / _AVATARS_SUBDIR
    avatars_dir.mkdir(parents=True, exist_ok=True)

    url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:24]

    # Check for any existing cached file for this URL.
    for ext in _MIME_EXT.values():
        candidate = avatars_dir / f"{url_hash}{ext}"
        if candidate.exists():
            logger.debug("Avatar cache hit for %r: %s", remote_url, candidate.name)
            return f"{_AVATARS_SUBDIR}/{candidate.name}"

    try:
        async with httpx.AsyncClient(
            timeout=_AVATAR_FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(remote_url)
            response.raise_for_status()
            raw = response.content
    except Exception as exc:
        logger.warning("Failed to fetch avatar from %r: %s", remote_url, exc)
        return None

    if len(raw) > MAX_FILE_SIZE_BYTES:
        logger.warning(
            "Remote avatar at %r exceeds size limit (%d bytes) — skipping",
            remote_url,
            len(raw),
        )
        return None

    try:
        processed, mime_type = process_image(raw)
    except ValueError as exc:
        logger.warning("Cannot process remote avatar from %r: %s", remote_url, exc)
        return None

    ext = _MIME_EXT.get(mime_type, ".jpg")
    dest = avatars_dir / f"{url_hash}{ext}"
    dest.write_bytes(processed)
    local_path = f"{_AVATARS_SUBDIR}/{dest.name}"
    logger.info("Proxied avatar %r → %s", remote_url, local_path)
    return local_path
