"""HTTP Signatures for ActivityPub federation.

Provides signing and verification of outgoing and incoming HTTP requests
using the draft-cavage-http-signatures-12 specification, delegated entirely
to the ``apsig`` library. This module must not implement any cryptographic
signature construction or verification directly.

References:
    - https://datatracker.ietf.org/doc/html/draft-cavage-http-signatures
    - https://docs.joinmastodon.org/spec/security/
"""

from __future__ import annotations

import logging
import re
from email.utils import formatdate

from apsig.draft import Signer, Verifier
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)


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
    outgoing ActivityPub request using ``apsig``. The returned headers
    should be merged into the HTTP request before sending.

    Args:
        method: The HTTP method (e.g. ``"POST"``).
        url: The full target URL (e.g.
            ``"https://remote.example.com/inbox"``).
        body: The request body bytes, or ``None`` for bodiless requests.
        private_key_pem: The PEM-encoded RSA private key for signing.
        key_id: The key ID URI to include in the Signature header
            (typically ``"https://domain/username#main-key"``).
        headers_to_sign: An optional list of header names to include in
            the signature. Defaults to apsig's secure default set.

    Returns:
        A dictionary of HTTP headers to add to the outgoing request,
        including ``Signature``, ``Date``, ``Host``, and optionally
        ``Digest`` and ``Content-Type``.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import (
        RSAPrivateKey as RSAPrivateKeyType,
    )

    raw_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    if not isinstance(raw_key, RSAPrivateKeyType):
        raise TypeError(f"Expected RSA private key, got {type(raw_key).__name__}")

    date_str = formatdate(timeval=None, localtime=False, usegmt=True)
    initial_headers: dict[str, str] = {"Date": date_str}

    actual_body: bytes = body if body is not None else b""
    if body is not None:
        initial_headers["Content-Type"] = "application/activity+json"

    # If a custom headers_to_sign list includes "digest" but there is no
    # body, remove it — apsig will error if digest is requested but absent.
    effective_headers_to_sign = headers_to_sign
    if (
        effective_headers_to_sign is not None
        and body is None
        and "digest" in [h.lower() for h in effective_headers_to_sign]
    ):
        effective_headers_to_sign = [h for h in effective_headers_to_sign if h.lower() != "digest"]

    signer = Signer(
        headers=initial_headers,
        private_key=raw_key,
        method=method,
        url=url,
        key_id=key_id,
        body=actual_body,
        signed_headers=effective_headers_to_sign,
    )
    raw_result = signer.sign()

    # Normalise to Title-Case and drop the Authorization alias that apsig
    # adds alongside Signature (we send Signature only).
    result: dict[str, str] = {}
    for k, v in raw_result.items():
        if k == "Authorization":
            continue
        result[k.title()] = str(v)
    return result


def verify_signature(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    public_key_pem: str,
) -> bool:
    """Verify an incoming HTTP request's signature using RSA-SHA256.

    Delegates verification entirely to ``apsig.draft.Verifier``. Returns
    ``False`` on any verification failure rather than raising, so callers
    can treat the result as a simple boolean gate.

    Args:
        method: The HTTP method (e.g. ``"POST"``).
        url: The full request URL including scheme and host
            (e.g. ``"https://local.example.com/inbox"``).
        headers: A dictionary of the request's HTTP headers.
        body: The raw request body bytes, or ``None`` if no body.
        public_key_pem: The PEM-encoded RSA public key of the remote
            actor who allegedly signed the request.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    try:
        verifier = Verifier(
            public_pem=public_key_pem,
            method=method,
            url=url,
            headers=headers,
            body=body,
        )
        verifier.verify(raise_on_fail=True)
        return True
    except Exception as exc:
        logger.info("Signature verification failed: %s", exc)
        return False
