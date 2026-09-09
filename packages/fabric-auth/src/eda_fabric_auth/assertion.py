from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from azure.keyvault.keys.crypto import SignatureAlgorithm


class FabricClientAssertionError(RuntimeError):
    """Raised when a client assertion cannot be produced safely."""


class CertificatePublicMetadata(Protocol):
    cer: bytes | None
    key_id: str | None
    id: str | None


class CertificateReader(Protocol):
    def get_certificate(self, certificate_name: str, /) -> CertificatePublicMetadata: ...


class SignatureResult(Protocol):
    signature: bytes


class SigningClient(Protocol):
    def sign(self, algorithm: SignatureAlgorithm, digest: bytes) -> SignatureResult: ...


class SigningClientFactory(Protocol):
    def __call__(self, key_id: str, /) -> SigningClient: ...


@dataclass(frozen=True)
class _CachedCertificateMetadata:
    key_id: str
    x5t_s256: str
    expires_at: datetime


class FabricClientAssertionFactory:
    """Builds PS256 client assertions using only certificate public metadata and KV signing."""

    def __init__(
        self,
        *,
        fabric_tenant_id: UUID,
        fabric_client_id: UUID,
        certificate_name: str,
        certificate_reader: CertificateReader,
        signing_client_factory: SigningClientFactory,
        now: Callable[[], datetime] | None = None,
        metadata_ttl: timedelta = timedelta(minutes=5),
        assertion_lifetime: timedelta = timedelta(minutes=5),
    ) -> None:
        self._fabric_tenant_id = fabric_tenant_id
        self._fabric_client_id = fabric_client_id
        self._certificate_name = certificate_name
        self._certificate_reader = certificate_reader
        self._signing_client_factory = signing_client_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._metadata_ttl = _bound_ttl(metadata_ttl)
        self._assertion_lifetime = _bound_assertion_lifetime(assertion_lifetime)
        self._cached_metadata: _CachedCertificateMetadata | None = None

    @property
    def audience(self) -> str:
        return f"https://login.microsoftonline.com/{self._fabric_tenant_id}/oauth2/v2.0/token"

    def build(self) -> str:
        try:
            now = self._utc_now()
            metadata = self._load_certificate_metadata(now)
            assertion_exp = min(now + self._assertion_lifetime, now + timedelta(minutes=5))

            header = {
                "alg": "PS256",
                "typ": "JWT",
                "x5t#S256": metadata.x5t_s256,
            }
            claims = {
                "aud": self.audience,
                "iss": str(self._fabric_client_id),
                "sub": str(self._fabric_client_id),
                "iat": int(now.timestamp()),
                "nbf": int(now.timestamp()),
                "exp": int(assertion_exp.timestamp()),
                "jti": str(uuid.uuid4()),
            }

            encoded_header = _b64url_unpadded(_json_bytes(header))
            encoded_claims = _b64url_unpadded(_json_bytes(claims))
            signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
            digest = hashlib.sha256(signing_input).digest()

            signer = self._signing_client_factory(metadata.key_id)
            signature = bytes(signer.sign(SignatureAlgorithm.ps256, digest).signature)
            encoded_signature = _b64url_unpadded(signature)
            return f"{encoded_header}.{encoded_claims}.{encoded_signature}"
        except FabricClientAssertionError:
            raise
        except Exception as exc:  # pragma: no cover - defensive hardening
            raise FabricClientAssertionError("fabric client assertion unavailable") from exc

    def callback(self) -> Callable[[], str]:
        return self.build

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _load_certificate_metadata(self, now: datetime) -> _CachedCertificateMetadata:
        cached = self._cached_metadata
        if cached is not None and cached.expires_at > now:
            return cached

        certificate = self._certificate_reader.get_certificate(self._certificate_name)
        der = certificate.cer
        key_id = _extract_key_id(certificate)
        if not isinstance(der, bytes) or not der:
            raise FabricClientAssertionError("fabric client assertion unavailable")
        if not key_id:
            raise FabricClientAssertionError("fabric client assertion unavailable")

        thumbprint = _b64url_unpadded(hashlib.sha256(der).digest())
        refreshed = _CachedCertificateMetadata(
            key_id=key_id,
            x5t_s256=thumbprint,
            expires_at=now + self._metadata_ttl,
        )
        self._cached_metadata = refreshed
        return refreshed


def _extract_key_id(certificate: CertificatePublicMetadata) -> str | None:
    key_id = certificate.key_id
    if isinstance(key_id, str) and key_id:
        return key_id

    certificate_id = certificate.id
    if not isinstance(certificate_id, str) or "/certificates/" not in certificate_id:
        return None
    return certificate_id.replace("/certificates/", "/keys/", 1)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _b64url_unpadded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _bound_ttl(value: timedelta) -> timedelta:
    seconds = int(value.total_seconds())
    if seconds < 1:
        return timedelta(seconds=1)
    if seconds > 3600:
        return timedelta(seconds=3600)
    return timedelta(seconds=seconds)


def _bound_assertion_lifetime(value: timedelta) -> timedelta:
    seconds = int(value.total_seconds())
    if seconds < 60:
        return timedelta(seconds=60)
    if seconds > 300:
        return timedelta(seconds=300)
    return timedelta(seconds=seconds)
