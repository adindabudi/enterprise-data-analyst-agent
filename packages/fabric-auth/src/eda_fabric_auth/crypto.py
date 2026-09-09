from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Literal, Protocol
from uuid import UUID

from azure.core.credentials_async import AsyncTokenCredential
from azure.keyvault.keys.crypto import KeyWrapAlgorithm
from azure.keyvault.keys.crypto.aio import CryptographyClient
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class CryptoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CipherEnvelope(CryptoModel):
    version: Literal[1] = 1
    algorithm: Literal["RSA-OAEP-256"] = "RSA-OAEP-256"
    key_id: str = Field(
        min_length=1,
        max_length=2_048,
        validation_alias=AliasChoices("key_id", "keyId"),
        serialization_alias="keyId",
    )
    wrapped_dek: str = Field(
        pattern=r"^[A-Za-z0-9_-]+$",
        validation_alias=AliasChoices("wrapped_dek", "wrappedDek"),
        serialization_alias="wrappedDek",
    )
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    ciphertext: str = Field(pattern=r"^[A-Za-z0-9_-]+$")


class EnvelopeContext(CryptoModel):
    schema_version: Literal[1] = 1
    product_tenant_id: UUID
    owner_object_id: UUID
    record_type: str = Field(min_length=1, max_length=80)
    record_id: str = Field(min_length=1, max_length=256)

    def associated_data(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class EnvelopeIntegrityError(ValueError):
    pass


class KeyWrapper(Protocol):
    @property
    def key_id(self) -> str: ...

    async def wrap_key(self, key: bytes) -> bytes: ...

    async def unwrap_key(self, wrapped_key: bytes, key_id: str) -> bytes: ...


class EnvelopeCipher:
    def __init__(self, wrapper: KeyWrapper) -> None:
        self._wrapper = wrapper

    async def encrypt(self, plaintext: bytes, context: EnvelopeContext) -> CipherEnvelope:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, context.associated_data())
        wrapped_dek = await self._wrapper.wrap_key(dek)
        return CipherEnvelope(
            key_id=self._wrapper.key_id,
            wrapped_dek=_encode(wrapped_dek),
            nonce=_encode(nonce),
            ciphertext=_encode(ciphertext),
        )

    async def decrypt(self, envelope: CipherEnvelope, context: EnvelopeContext) -> bytes:
        try:
            if envelope.key_id != self._wrapper.key_id:
                raise ValueError("key ID mismatch")
            wrapped_dek = _decode(envelope.wrapped_dek)
            nonce = _decode(envelope.nonce)
            ciphertext = _decode(envelope.ciphertext)
            if len(nonce) != 12:
                raise ValueError("nonce length mismatch")
            dek = await self._wrapper.unwrap_key(wrapped_dek, envelope.key_id)
            if len(dek) != 32:
                raise ValueError("DEK length mismatch")
            return AESGCM(dek).decrypt(nonce, ciphertext, context.associated_data())
        except (InvalidTag, ValueError, binascii.Error):
            raise EnvelopeIntegrityError("encrypted Fabric record failed integrity validation") from None
        except Exception:
            raise EnvelopeIntegrityError("encrypted Fabric record failed integrity validation") from None


class KeyVaultKeyWrapper:
    def __init__(self, key_id: str, credential: AsyncTokenCredential) -> None:
        self._key_id = key_id
        self._client = CryptographyClient(key_id, credential)

    @property
    def key_id(self) -> str:
        return self._key_id

    async def wrap_key(self, key: bytes) -> bytes:
        result = await self._client.wrap_key(KeyWrapAlgorithm.rsa_oaep_256, key)
        return bytes(result.encrypted_key)

    async def unwrap_key(self, wrapped_key: bytes, key_id: str) -> bytes:
        if key_id != self._key_id:
            raise ValueError("key ID mismatch")
        result = await self._client.unwrap_key(KeyWrapAlgorithm.rsa_oaep_256, wrapped_key)
        return bytes(result.key)

    async def close(self) -> None:
        await self._client.close()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = b"=" * (-len(value) % 4)
    return base64.b64decode(value.encode("ascii") + padding, altchars=b"-_", validate=True)
