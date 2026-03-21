from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.federation.signatures import (
    extract_key_id,
    parse_signature_header,
    sign_request,
    verify_signature,
)


@pytest.fixture
def keypair() -> tuple[str, str]:
    """Generate a real RSA keypair for testing."""
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
def other_keypair() -> tuple[str, str]:
    """Generate a second RSA keypair (different from ``keypair``)."""
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


class TestParseSignatureHeader:
    def test_parse_typical_mastodon_style_header(self) -> None:
        header = (
            'keyId="https://mastodon.social/users/alice#main-key",'
            'algorithm="rsa-sha256",'
            'headers="(request-target) host date digest",'
            'signature="dGVzdHNpZw=="'
        )
        result = parse_signature_header(header)

        assert result["keyId"] == "https://mastodon.social/users/alice#main-key"
        assert result["algorithm"] == "rsa-sha256"
        assert result["headers"] == "(request-target) host date digest"
        assert result["signature"] == "dGVzdHNpZw=="

    def test_parse_minimal_header_keyid_and_signature_only(self) -> None:
        header = 'keyId="https://example.com/key",signature="YWJj"'
        result = parse_signature_header(header)

        assert result["keyId"] == "https://example.com/key"
        assert result["signature"] == "YWJj"
        assert "algorithm" not in result
        assert "headers" not in result

    def test_parse_empty_string_returns_empty_dict(self) -> None:
        result = parse_signature_header("")
        assert result == {}

    def test_parse_malformed_input_no_quotes(self) -> None:
        result = parse_signature_header("keyId=no-quotes,signature=bad")
        assert result == {}

    def test_parse_malformed_input_garbage(self) -> None:
        result = parse_signature_header("this is not a valid header at all!!!")
        assert result == {}

    def test_parse_header_with_spaces_around_commas(self) -> None:
        header = (
            'keyId="https://example.com/k", '
            'algorithm="rsa-sha256", '
            'headers="(request-target) host date", '
            'signature="c2ln"'
        )
        result = parse_signature_header(header)

        assert result["keyId"] == "https://example.com/k"
        assert result["algorithm"] == "rsa-sha256"
        assert result["signature"] == "c2ln"


class TestExtractKeyId:
    def test_extract_key_id_from_valid_header(self) -> None:
        header = (
            'keyId="https://remote.example.com/users/bob#main-key",'
            'algorithm="rsa-sha256",'
            'headers="(request-target) host date digest",'
            'signature="abc123=="'
        )
        result = extract_key_id(header)
        assert result == "https://remote.example.com/users/bob#main-key"

    def test_returns_none_when_no_key_id(self) -> None:
        header = 'algorithm="rsa-sha256",signature="abc123=="'
        result = extract_key_id(header)
        assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        assert extract_key_id("") is None

    def test_extract_key_id_with_fragment(self) -> None:
        header = 'keyId="https://example.com/user#main-key",signature="sig"'
        result = extract_key_id(header)
        assert result == "https://example.com/user#main-key"

    def test_extract_key_id_with_path_segments(self) -> None:
        header = 'keyId="https://example.com/users/alice/keys/primary",signature="sig"'
        result = extract_key_id(header)
        assert result == "https://example.com/users/alice/keys/primary"

    def test_extract_key_id_with_port(self) -> None:
        header = 'keyId="https://example.com:8443/user#main-key",signature="sig"'
        result = extract_key_id(header)
        assert result == "https://example.com:8443/user#main-key"


class TestSignRequest:
    def test_returns_dict_with_required_headers(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=b'{"type":"Create"}',
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert "Signature" in result
        assert "Date" in result
        assert "Host" in result

    def test_includes_digest_when_body_provided(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        body = b'{"type":"Create"}'
        result = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert "Digest" in result

    def test_omits_digest_when_body_is_none(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="GET",
            url="https://remote.example.com/users/alice",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert "Digest" not in result

    def test_includes_content_type_when_body_provided(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=b'{"type":"Follow"}',
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert "Content-Type" in result
        assert result["Content-Type"] == "application/activity+json"

    def test_omits_content_type_when_body_is_none(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="GET",
            url="https://remote.example.com/users/alice",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert "Content-Type" not in result

    def test_handles_url_with_path_and_query_string(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="GET",
            url="https://remote.example.com/users/alice?page=1&filter=notes",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert result["Host"] == "remote.example.com"
        assert "Signature" in result

    def test_host_includes_non_standard_port(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="GET",
            url="https://remote.example.com:8443/inbox",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert result["Host"] == "remote.example.com:8443"

    def test_signature_header_contains_expected_fields(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=b'{"type":"Create"}',
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        sig_header = result["Signature"]
        parsed = parse_signature_header(sig_header)

        assert parsed["keyId"] == "https://local.example.com/user#main-key"
        assert parsed["algorithm"] == "rsa-sha256"
        assert "headers" in parsed
        assert "signature" in parsed
        assert "(request-target)" in parsed["headers"]

    def test_signed_headers_in_signature_match_defaults_with_body(
        self, keypair: tuple[str, str]
    ) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=b"body",
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )
        parsed = parse_signature_header(result["Signature"])
        signed = parsed["headers"].split()

        assert "(request-target)" in signed
        assert "host" in signed
        assert "date" in signed
        assert "digest" in signed

    def test_signed_headers_exclude_digest_when_no_body(self, keypair: tuple[str, str]) -> None:
        _public_pem, private_pem = keypair
        result = sign_request(
            method="GET",
            url="https://remote.example.com/users/alice",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )
        parsed = parse_signature_header(result["Signature"])
        signed = parsed["headers"].split()

        assert "digest" not in signed


class TestVerifySignature:
    def test_verify_correctly_signed_request(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type":"Follow"}'
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://remote.example.com/user#main-key",
        )

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_reject_tampered_signature(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type":"Follow"}'
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://remote.example.com/user#main-key",
        )

        # Tamper with the signature value inside the Signature header
        sig_parts = parse_signature_header(signed_headers["Signature"])
        original_sig = sig_parts["signature"]
        # Flip bits in the base64-decoded signature
        tampered_bytes = base64.b64decode(original_sig)
        tampered_bytes = bytes([b ^ 0xFF for b in tampered_bytes])
        tampered_sig = base64.b64encode(tampered_bytes).decode("ascii")

        tampered_header = signed_headers["Signature"].replace(original_sig, tampered_sig)
        signed_headers["Signature"] = tampered_header

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is False

    def test_reject_body_digest_mismatch(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type":"Follow"}'
        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://remote.example.com/user#main-key",
        )

        # Provide a different body than what was signed
        wrong_body = b'{"type":"Undo"}'
        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=signed_headers,
            body=wrong_body,
            public_key_pem=public_pem,
        )
        assert result is False

    def test_reject_wrong_public_key(
        self,
        keypair: tuple[str, str],
        other_keypair: tuple[str, str],
    ) -> None:
        _public_pem, private_pem = keypair
        other_public_pem, _other_private_pem = other_keypair
        body = b'{"type":"Follow"}'

        signed_headers = sign_request(
            method="POST",
            url="https://local.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://remote.example.com/user#main-key",
        )

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=other_public_pem,
        )
        assert result is False

    def test_reject_missing_signature_header(self, keypair: tuple[str, str]) -> None:
        public_pem, _private_pem = keypair
        headers: dict[str, str] = {
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem=public_pem,
        )
        assert result is False

    def test_returns_false_on_empty_signature_header(self, keypair: tuple[str, str]) -> None:
        public_pem, _private_pem = keypair
        headers: dict[str, str] = {
            "Signature": "",
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem=public_pem,
        )
        assert result is False

    def test_returns_false_on_signature_missing_required_fields(
        self, keypair: tuple[str, str]
    ) -> None:
        public_pem, _private_pem = keypair
        headers: dict[str, str] = {
            "Signature": 'keyId="https://example.com/key"',
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem=public_pem,
        )
        assert result is False

    def test_returns_false_on_invalid_base64_signature(self, keypair: tuple[str, str]) -> None:
        public_pem, _private_pem = keypair
        headers: dict[str, str] = {
            "Signature": (
                'keyId="https://example.com/key",'
                'algorithm="rsa-sha256",'
                'headers="(request-target) host date",'
                'signature="!!!not-valid-base64!!!"'
            ),
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem=public_pem,
        )
        assert result is False

    def test_returns_false_on_invalid_public_key_pem(self) -> None:
        headers: dict[str, str] = {
            "Signature": (
                'keyId="https://example.com/key",'
                'algorithm="rsa-sha256",'
                'headers="(request-target) host date",'
                'signature="dGVzdA=="'
            ),
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem="not-a-real-pem-key",
        )
        assert result is False

    def test_returns_false_when_signed_header_missing_from_request(
        self, keypair: tuple[str, str]
    ) -> None:
        public_pem, _private_pem = keypair
        # Signature says it signed "digest" but no Digest header is present
        headers: dict[str, str] = {
            "Signature": (
                'keyId="https://example.com/key",'
                'algorithm="rsa-sha256",'
                'headers="(request-target) host date digest",'
                'signature="dGVzdA=="'
            ),
            "Host": "local.example.com",
            "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        result = verify_signature(
            method="POST",
            url="https://local.example.com/inbox",
            headers=headers,
            body=b"body",
            public_key_pem=public_pem,
        )
        assert result is False


class TestSignVerifyRoundTrip:
    def test_sign_post_with_body_then_verify(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Follow"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        result = verify_signature(
            method="POST",
            url="https://remote.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_sign_then_tamper_body_verify_fails(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Follow"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        tampered_body = b'{"type": "Delete"}'
        result = verify_signature(
            method="POST",
            url="https://remote.example.com/inbox",
            headers=signed_headers,
            body=tampered_body,
            public_key_pem=public_pem,
        )
        assert result is False

    def test_sign_then_verify_with_different_key_fails(
        self,
        keypair: tuple[str, str],
        other_keypair: tuple[str, str],
    ) -> None:
        _public_pem, private_pem = keypair
        other_public_pem, _other_private_pem = other_keypair
        body = b'{"type": "Create"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        result = verify_signature(
            method="POST",
            url="https://remote.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=other_public_pem,
        )
        assert result is False

    def test_sign_get_no_body_then_verify(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair

        signed_headers = sign_request(
            method="GET",
            url="https://remote.example.com/users/alice",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        result = verify_signature(
            method="GET",
            url="https://remote.example.com/users/alice",
            headers=signed_headers,
            body=None,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_sign_with_custom_headers_to_sign_then_verify(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Like"}'

        custom_headers = ["(request-target)", "host", "date", "digest"]
        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
            headers_to_sign=custom_headers,
        )

        result = verify_signature(
            method="POST",
            url="https://remote.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_sign_with_minimal_headers_to_sign_then_verify(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair

        signed_headers = sign_request(
            method="GET",
            url="https://remote.example.com/users/bob",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
            headers_to_sign=["(request-target)", "host", "date"],
        )

        result = verify_signature(
            method="GET",
            url="https://remote.example.com/users/bob",
            headers=signed_headers,
            body=None,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_sign_url_with_query_string_then_verify(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair

        signed_headers = sign_request(
            method="GET",
            url="https://remote.example.com/outbox?page=true&max_id=123",
            body=None,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        result = verify_signature(
            method="GET",
            url="https://remote.example.com/outbox?page=true&max_id=123",
            headers=signed_headers,
            body=None,
            public_key_pem=public_pem,
        )
        assert result is True

    def test_sign_post_verify_with_wrong_method_fails(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Create"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        # Verify with wrong method -- the (request-target) will differ
        result = verify_signature(
            method="PUT",
            url="https://remote.example.com/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is False

    def test_sign_post_verify_with_wrong_path_fails(self, keypair: tuple[str, str]) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Create"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        # Verify with wrong path -- the (request-target) will differ
        result = verify_signature(
            method="POST",
            url="https://remote.example.com/wrong-path",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is False

    def test_sign_request_with_non_standard_port_then_verify(
        self, keypair: tuple[str, str]
    ) -> None:
        public_pem, private_pem = keypair
        body = b'{"type": "Announce"}'

        signed_headers = sign_request(
            method="POST",
            url="https://remote.example.com:8443/inbox",
            body=body,
            private_key_pem=private_pem,
            key_id="https://local.example.com/user#main-key",
        )

        assert signed_headers["Host"] == "remote.example.com:8443"

        result = verify_signature(
            method="POST",
            url="https://remote.example.com:8443/inbox",
            headers=signed_headers,
            body=body,
            public_key_pem=public_pem,
        )
        assert result is True
