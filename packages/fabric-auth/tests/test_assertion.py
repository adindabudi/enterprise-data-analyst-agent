from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from azure.keyvault.keys.crypto import SignatureAlgorithm
from eda_fabric_auth.assertion import FabricClientAssertionError, FabricClientAssertionFactory


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass
class _FakeCertificate:
    cer: bytes | None
    key_id: str | None = "https://vault.example/keys/fabric-signing/version-1"
    id: str | None = "https://vault.example/certificates/fabric-signing/version-1"


class _RecordingCertificateReader:
    def __init__(self, certificate: _FakeCertificate) -> None:
        self._certificate = certificate
        self.get_calls = 0
        self.secret_calls = 0

    def get_certificate(self, certificate_name: str, /) -> _FakeCertificate:
        assert certificate_name == "fabric-oauth-signing"
        self.get_calls += 1
        return self._certificate

    def get_secret(self, _name: str) -> None:
        self.secret_calls += 1


class _RecordingSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[SignatureAlgorithm, bytes]] = []

    def sign(self, algorithm: SignatureAlgorithm, digest: bytes) -> SimpleNamespace:
        self.calls.append((algorithm, digest))
        return SimpleNamespace(signature=b"signature-bytes")


class _RecordingSignerFactory:
    def __init__(self, signer: _RecordingSigner) -> None:
        self._signer = signer
        self.key_ids: list[str] = []

    def __call__(self, key_id: str, /) -> _RecordingSigner:
        self.key_ids.append(key_id)
        return self._signer


class _MutableNow:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_assertion_uses_expected_header_claims_thumbprint_and_ps256_digest() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    der = b"public-der-certificate"
    reader = _RecordingCertificateReader(_FakeCertificate(cer=der))
    signer = _RecordingSigner()

    factory = FabricClientAssertionFactory(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        certificate_name="fabric-oauth-signing",
        certificate_reader=reader,
        signing_client_factory=_RecordingSignerFactory(signer),
        now=lambda: now,
    )

    assertion = factory.build()
    header_b64, claims_b64, signature_b64 = assertion.split(".")
    header = json.loads(_b64url_decode(header_b64))
    claims = json.loads(_b64url_decode(claims_b64))

    expected_thumbprint = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode("ascii")
    assert header == {"alg": "PS256", "typ": "JWT", "x5t#S256": expected_thumbprint}
    assert claims["aud"] == "https://login.microsoftonline.com/33333333-3333-3333-3333-333333333333/oauth2/v2.0/token"
    assert claims["iss"] == claims["sub"] == "44444444-4444-4444-4444-444444444444"
    assert claims["iat"] == claims["nbf"] == int(now.timestamp())
    assert claims["exp"] - claims["iat"] <= 300

    algorithm, digest = signer.calls[0]
    assert algorithm is SignatureAlgorithm.ps256
    assert digest == hashlib.sha256(f"{header_b64}.{claims_b64}".encode("ascii")).digest()
    assert _b64url_decode(signature_b64) == b"signature-bytes"


def test_assertion_generates_unique_jti_and_never_reads_secret_material() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    reader = _RecordingCertificateReader(_FakeCertificate(cer=b"public-der-certificate"))
    factory = FabricClientAssertionFactory(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        certificate_name="fabric-oauth-signing",
        certificate_reader=reader,
        signing_client_factory=_RecordingSignerFactory(_RecordingSigner()),
        now=lambda: now,
    )

    first = json.loads(_b64url_decode(factory.build().split(".")[1]))
    second = json.loads(_b64url_decode(factory.build().split(".")[1]))

    assert first["jti"] != second["jti"]
    assert reader.secret_calls == 0


def test_public_metadata_cache_is_bounded_and_refreshes_after_ttl() -> None:
    now = _MutableNow(datetime(2026, 7, 27, 12, 0, tzinfo=UTC))
    reader = _RecordingCertificateReader(_FakeCertificate(cer=b"public-der-certificate"))
    factory = FabricClientAssertionFactory(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        certificate_name="fabric-oauth-signing",
        certificate_reader=reader,
        signing_client_factory=_RecordingSignerFactory(_RecordingSigner()),
        now=now,
        metadata_ttl=timedelta(seconds=2),
    )

    factory.build()
    now.value += timedelta(seconds=1)
    factory.build()
    now.value += timedelta(seconds=2)
    factory.build()

    assert reader.get_calls == 2


def test_malformed_certificate_or_signing_failure_is_sanitized() -> None:
    class FailingSigner:
        def sign(self, algorithm: SignatureAlgorithm, digest: bytes) -> SimpleNamespace:
            del algorithm, digest
            raise RuntimeError("private key failure")

    reader = _RecordingCertificateReader(_FakeCertificate(cer=None))
    factory = FabricClientAssertionFactory(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        certificate_name="fabric-oauth-signing",
        certificate_reader=reader,
        signing_client_factory=lambda _key_id: FailingSigner(),
    )
    with pytest.raises(FabricClientAssertionError, match="fabric client assertion unavailable"):
        factory.build()

    failing_factory = FabricClientAssertionFactory(
        fabric_tenant_id=UUID("33333333-3333-3333-3333-333333333333"),
        fabric_client_id=UUID("44444444-4444-4444-4444-444444444444"),
        certificate_name="fabric-oauth-signing",
        certificate_reader=_RecordingCertificateReader(_FakeCertificate(cer=b"public-der-certificate")),
        signing_client_factory=lambda _key_id: FailingSigner(),
    )
    with pytest.raises(FabricClientAssertionError, match="fabric client assertion unavailable"):
        failing_factory.build()
