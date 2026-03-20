"""HTTP Signatures for ActivityPub federation.

Implements the draft-cavage-http-signatures specification with RSA-SHA256
for signing outgoing requests and verifying incoming requests. This module
provides the cryptographic foundation for secure server-to-server
communication in the ActivityPub federation protocol.

References:
    - https://datatracker.ietf.org/doc/html/draft-cavage-http-signatures
    - https://docs.joinmastodon.org/spec/security/
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from email.utils import formatdate
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS_TO_SIGN: list[str] = [
    "(request-target)",
    "host",
    "date",
    "digest",
]


def _compute_digest(body: bytes) -> str:
    """Compute the SHA-256 digest header value for a request body.

    Args:
        body: The raw request body bytes.

    Returns:
        The digest string in the format ``SHA-256=<base64-encoded hash>``.
    """
    digest_bytes = hashlib.sha256(body).digest()
    return "SHA-256=" + base64.b64encode(digest_bytes).decode("ascii")


def _build_signature_string(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    signed_headers: list[str],
) -> str:
    """Build the signature string from request components.

    Constructs the string to be signed according to the
    draft-cavage-http-signatures specification. Each header contributes
    one line in the format ``header-name: value``, with lines separated
    by newline characters.

    The pseudo-header ``(request-target)`` is formatted as
    ``(request-target): <method> <path>``.

    Args:
        method: The HTTP method (lowercased in output).
        path: The request path including any query string.
        headers: A dictionary of header names to values. Keys are
            matched case-insensitively.
        signed_headers: The ordered list of header names to include
            in the signature string.

    Returns:
        The assembled signature string ready for signing or verification.

    Raises:
        KeyError: If a required header is not found in the headers dict.
    """
    # Build a lowercase-keyed lookup for case-insensitive header matching.
    lower_headers = {k.lower(): v for k, v in headers.items()}

    lines: list[str] = []
    for header_name in signed_headers:
        lower_name = header_name.lower()
        if lower_name == "(request-target)":
            lines.append(f"(request-target): {method.lower()} {path}")
        else:
            if lower_name not in lower_headers:
                raise KeyError(
                    f"Header '{header_name}' required for signature but not present in request"
                )
            lines.append(f"{lower_name}: {lower_headers[lower_name]}")

    return "\n".join(lines)


def _load_private_key(private_key_pem: str) -> RSAPrivateKey:
    """Load an RSA private key from a PEM-encoded string.

    Args:
        private_key_pem: The PEM-encoded private key string.

    Returns:
        The loaded RSA private key object.

    Raises:
        TypeError: If the loaded key is not an RSA private key.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey as RSAPrivateKeyType

    key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    if not isinstance(key, RSAPrivateKeyType):
        raise TypeError(f"Expected RSA private key, got {type(key).__name__}")
    return key


def _load_public_key(public_key_pem: str) -> RSAPublicKey:
    """Load an RSA public key from a PEM-encoded string.

    Args:
        public_key_pem: The PEM-encoded public key string.

    Returns:
        The loaded RSA public key object.

    Raises:
        TypeError: If the loaded key is not an RSA public key.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey as RSAPublicKeyType

    key = serialization.load_pem_public_key(
        public_key_pem.encode("utf-8"),
    )
    if not isinstance(key, RSAPublicKeyType):
        raise TypeError(f"Expected RSA public key, got {type(key).__name__}")
    return key


def parse_signature_header(signature_header: str) -> dict[str, str]:
    """Parse an HTTP Signature header into its component fields.

    Extracts the ``keyId``, ``algorithm``, ``headers``, and ``signature``
    fields from a Signature header value string.

    Example input::

        keyId="https://example.com/user#main-key",algorithm="rsa-sha256",
        headers="(request-target) host date digest",signature="base64data..."

    Args:
        signature_header: The raw Signature header value string.

    Returns:
        A dictionary mapping field names to their values (with surrounding
        quotes stripped). Expected keys are ``keyId``, ``algorithm``,
        ``headers``, and ``signature``.
    """
    result: dict[str, str] = {}
    # Match key="value" pairs, allowing for base64 characters including
    # +, /, and = within quoted values.
    pattern = re.compile(r'(\w+)="([^"]*)"')
    for match in pattern.finditer(signature_header):
        result[match.group(1)] = match.group(2)
    return result


def extract_key_id(signature_header: str) -> str | None:
    """Extract the keyId from an HTTP Signature header.

    This is a lightweight extraction that avoids full parsing when only
    the ``keyId`` is needed (e.g., to look up the remote actor's public
    key before performing full signature verification).

    Args:
        signature_header: The raw Signature header value string.

    Returns:
        The ``keyId`` value if found, or ``None`` if the header does not
        contain a ``keyId`` field.
    """
    match = re.search(r'keyId="([^"]*)"', signature_header)
    if match:
        return match.group(1)
    return None


def sign_request(
    *,
    method: str,
    url: str,
    body: bytes | None,
    private_key_pem: str,
    key_id: str,
    headers_to_sign: list[str] | None = None,
) -> dict[str, str]:
    """Sign an outgoing HTTP request using HTTP Signatures (RSA-SHA256).

    Generates the cryptographic signature and all required headers for an
    outgoing ActivityPub request. The returned headers should be merged
    into the HTTP request before sending.

    Args:
        method: The HTTP method (e.g. ``"POST"``).
        url: The full target URL (e.g.
            ``"https://remote.example.com/inbox"``).
        body: The request body bytes, or ``None`` for bodiless requests.
        private_key_pem: The PEM-encoded RSA private key for signing.
        key_id: The key ID URI to include in the Signature header
            (typically ``"https://domain/username#main-key"``).
        headers_to_sign: An optional list of header names to include in
            the signature. Defaults to ``["(request-target)", "host",
            "date", "digest"]``.

    Returns:
        A dictionary of HTTP headers to add to the outgoing request,
        including ``Signature``, ``Date``, ``Host``, and optionally
        ``Digest`` and ``Content-Type``.
    """
    if headers_to_sign is None:
        headers_to_sign = list(_DEFAULT_HEADERS_TO_SIGN)

    parsed_url = urlparse(url)
    host = parsed_url.hostname or ""
    if parsed_url.port and parsed_url.port not in (80, 443):
        host = f"{host}:{parsed_url.port}"

    path = parsed_url.path or "/"
    if parsed_url.query:
        path = f"{path}?{parsed_url.query}"

    date_str = formatdate(timeval=None, localtime=False, usegmt=True)

    # Assemble the headers dict that the signature string will reference.
    request_headers: dict[str, str] = {
        "host": host,
        "date": date_str,
    }

    # Build the output headers dict to return to the caller.
    output_headers: dict[str, str] = {
        "Host": host,
        "Date": date_str,
    }

    if body is not None:
        digest_value = _compute_digest(body)
        request_headers["digest"] = digest_value
        output_headers["Digest"] = digest_value
        output_headers["Content-Type"] = "application/activity+json"

    # If digest is in the signed headers list but there's no body,
    # remove it to avoid signing a nonexistent header.
    if body is None and "digest" in [h.lower() for h in headers_to_sign]:
        headers_to_sign = [h for h in headers_to_sign if h.lower() != "digest"]

    signature_string = _build_signature_string(
        method=method,
        path=path,
        headers=request_headers,
        signed_headers=headers_to_sign,
    )

    private_key = _load_private_key(private_key_pem)
    signature_bytes = private_key.sign(
        signature_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode("ascii")

    signed_headers_str = " ".join(h.lower() for h in headers_to_sign)
    signature_header = (
        f'keyId="{key_id}",'
        f'algorithm="rsa-sha256",'
        f'headers="{signed_headers_str}",'
        f'signature="{signature_b64}"'
    )
    output_headers["Signature"] = signature_header

    return output_headers


def verify_signature(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None,
    public_key_pem: str,
) -> bool:
    """Verify an incoming HTTP request's signature using RSA-SHA256.

    Parses the ``Signature`` header, reconstructs the signature string
    from the request's headers, and verifies the cryptographic signature
    against the provided public key.

    If a ``Digest`` header is present and a body is provided, the digest
    is also verified to ensure the body has not been tampered with.

    Args:
        method: The HTTP method (e.g. ``"POST"``).
        path: The request path including any query string
            (e.g. ``"/inbox"``).
        headers: A dictionary of the request's HTTP headers.
        body: The raw request body bytes, or ``None`` if no body.
        public_key_pem: The PEM-encoded RSA public key of the remote
            actor who allegedly signed the request.

    Returns:
        ``True`` if the signature and optional digest are valid,
        ``False`` otherwise. Returns ``False`` on any cryptographic
        error rather than raising exceptions.
    """
    # Find the Signature header (case-insensitive lookup).
    lower_headers = {k.lower(): v for k, v in headers.items()}
    signature_header_value = lower_headers.get("signature")

    if not signature_header_value:
        logger.warning("No Signature header found in request")
        return False

    try:
        sig_parts = parse_signature_header(signature_header_value)
    except Exception:
        logger.warning("Failed to parse Signature header")
        return False

    if "signature" not in sig_parts or "headers" not in sig_parts:
        logger.warning(
            "Signature header missing required fields: %s",
            list(sig_parts.keys()),
        )
        return False

    # Verify the digest if present.
    if body is not None and "digest" in lower_headers:
        expected_digest = _compute_digest(body)
        actual_digest = lower_headers["digest"]
        if expected_digest != actual_digest:
            logger.warning(
                "Digest mismatch: expected %s, got %s",
                expected_digest,
                actual_digest,
            )
            return False

    signed_header_names = sig_parts["headers"].split()

    try:
        signature_string = _build_signature_string(
            method=method,
            path=path,
            headers=headers,
            signed_headers=signed_header_names,
        )
    except KeyError as exc:
        logger.warning("Missing header required for signature verification: %s", exc)
        return False

    try:
        signature_bytes = base64.b64decode(sig_parts["signature"])
    except Exception:
        logger.warning("Failed to base64-decode signature value")
        return False

    try:
        public_key = _load_public_key(public_key_pem)
        public_key.verify(
            signature_bytes,
            signature_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        logger.info("Signature verification failed: invalid signature")
        return False
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        logger.warning("Signature verification error: %s", exc)
        return False
    except Exception:
        logger.exception("Unexpected error during signature verification")
        return False

    return True
