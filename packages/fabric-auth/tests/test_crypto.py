from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import UUID

import pytest
from azure.keyvault.keys.crypto import KeyWrapAlgorithm
from eda_fabric_auth.crypto import (
    CipherEnvelope,
    EnvelopeCipher,
    EnvelopeContext,
    EnvelopeIntegrityError,
    KeyVaultKeyWrapper,
)
from pydantic import ValidationError


class RecordingKeyWrapper:
    key_id = "https://vault.example/keys/fabric-wrap/version-1"

    def __init__(self) -> None:
        self.wrapped_keys: list[bytes] = []

    async def wrap_key(self, key: bytes) -> bytes:
        self.wrapped_keys.append(key)
        return bytes(value ^ 0xA5 for value in key)

    async def unwrap_key(self, wrapped_key: bytes, key_id: str) -> bytes:
        if key_id != self.key_id:
            raise ValueError("wrong key")
        return bytes(value ^ 0xA5 for value in wrapped_key)


def context(owner: str = "22222222-2222-2222-2222-222222222222") -> EnvelopeContext:
    return EnvelopeContext(
        product_tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_object_id=UUID(owner),
        record_type="fabricGrant",
        record_id="fabric-grant:ontology",
    )


def mutate(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    decoded = bytearray(base64.urlsafe_b64decode(value + padding))
    decoded[0] ^= 1
    return base64.urlsafe_b64encode(bytes(decoded)).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
async def test_round_trip_uses_random_dek_and_nonce_per_write() -> None:
    wrapper = RecordingKeyWrapper()
    cipher = EnvelopeCipher(wrapper)

    first = await cipher.encrypt(b'{"refresh_token":"private"}', context())
    second = await cipher.encrypt(b'{"refresh_token":"private"}', context())

    assert await cipher.decrypt(first, context()) == b'{"refresh_token":"private"}'
    assert first.key_id == wrapper.key_id
    assert first.algorithm == "RSA-OAEP-256"
    assert first.nonce != second.nonce
    assert first.wrapped_dek != second.wrapped_dek
    assert first.ciphertext != second.ciphertext
    assert len(wrapper.wrapped_keys) == 2
    assert all(len(key) == 32 for key in wrapper.wrapped_keys)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["ciphertext", "wrapped_dek", "nonce"])
async def test_tampered_envelope_fails_without_plaintext(field: str) -> None:
    cipher = EnvelopeCipher(RecordingKeyWrapper())
    envelope = await cipher.encrypt(b"sensitive", context())
    tampered = envelope.model_copy(update={field: mutate(getattr(envelope, field))})

    with pytest.raises(EnvelopeIntegrityError):
        await cipher.decrypt(tampered, context())


@pytest.mark.asyncio
async def test_wrong_owner_aad_or_key_id_fails() -> None:
    cipher = EnvelopeCipher(RecordingKeyWrapper())
    envelope = await cipher.encrypt(b"sensitive", context())

    with pytest.raises(EnvelopeIntegrityError):
        await cipher.decrypt(envelope, context("44444444-4444-4444-4444-444444444444"))
    with pytest.raises(EnvelopeIntegrityError):
        await cipher.decrypt(envelope.model_copy(update={"key_id": "https://vault.example/keys/other/v1"}), context())


def test_envelope_rejects_other_algorithm_and_plaintext_oauth_fields() -> None:
    properties = set(CipherEnvelope.model_json_schema()["properties"])
    assert not properties & {
        "access_token",
        "refresh_token",
        "id_token",
        "authorization_code",
        "code_verifier",
        "client_assertion",
        "token_cache",
    }
    with pytest.raises(ValidationError):
        CipherEnvelope.model_validate(
            {
                "version": 1,
                "algorithm": "RSA1_5",
                "key_id": "key",
                "wrapped_dek": "AA",
                "nonce": "AA",
                "ciphertext": "AA",
            }
        )


@pytest.mark.asyncio
async def test_key_vault_wrapper_uses_only_rsa_oaep_256(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object, bytes]] = []

    class FakeCryptographyClient:
        def __init__(self, key_id: str, credential: object) -> None:
            del credential
            assert key_id == "https://vault.example/keys/fabric-wrap/version-1"

        async def wrap_key(self, algorithm: object, key: bytes) -> object:
            calls.append(("wrap", algorithm, key))
            return SimpleNamespace(encrypted_key=b"wrapped")

        async def unwrap_key(self, algorithm: object, key: bytes) -> object:
            calls.append(("unwrap", algorithm, key))
            return SimpleNamespace(key=b"k" * 32)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("eda_fabric_auth.crypto.CryptographyClient", FakeCryptographyClient)
    wrapper = KeyVaultKeyWrapper("https://vault.example/keys/fabric-wrap/version-1", object())  # type: ignore[arg-type]

    assert await wrapper.wrap_key(b"k" * 32) == b"wrapped"
    assert await wrapper.unwrap_key(b"wrapped", wrapper.key_id) == b"k" * 32
    await wrapper.close()

    assert calls == [
        ("wrap", KeyWrapAlgorithm.rsa_oaep_256, b"k" * 32),
        ("unwrap", KeyWrapAlgorithm.rsa_oaep_256, b"wrapped"),
    ]
