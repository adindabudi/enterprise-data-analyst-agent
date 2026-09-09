from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import which
from typing import cast

from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential
from azure.keyvault.keys.crypto import CryptographyClient, KeyWrapAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eda_worker.acceptance.fabric import FabricAcceptanceInput, acceptance_input_aad

AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encrypt and publish a one-time Fabric acceptance input.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--provider-contract-sha256", required=True)
    parser.add_argument("--cosmos-endpoint", required=True)
    parser.add_argument("--database", default="enterprise-data-analyst")
    parser.add_argument("--container", default="runtime")
    parser.add_argument("--cache-key-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--expected-principal-id", required=True)
    return parser.parse_args()


def active_principal_id() -> str:
    result = subprocess.run(  # noqa: S603
        [AZURE_CLI, "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv", "--only-show-errors"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def load_input(arguments: argparse.Namespace) -> FabricAcceptanceInput:
    fixture_path = cast(Path, arguments.fixture)
    if stat.S_IMODE(fixture_path.stat().st_mode) != 0o600:
        raise ValueError("Fabric acceptance fixture must have mode 0600")
    raw: object = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Fabric acceptance fixture must contain only fixtures")
    raw_mapping = cast(dict[object, object], raw)
    if set(raw_mapping) != {"fixtures"}:
        raise ValueError("Fabric acceptance fixture must contain only fixtures")
    return FabricAcceptanceInput.model_validate(
        {
            "runId": arguments.run_id,
            "deploymentId": arguments.deployment_id,
            "providerContractSha256": arguments.provider_contract_sha256,
            "fixtures": raw_mapping["fixtures"],
        }
    )


def publish(arguments: argparse.Namespace) -> str:
    if active_principal_id().casefold() != cast(str, arguments.expected_principal_id).casefold():
        raise ValueError("active principal is not the Fabric acceptance principal")
    acceptance_input = load_input(arguments)
    record_id = f"fabric-acceptance-input:{acceptance_input.run_id}"
    plaintext = acceptance_input.model_dump_json(by_alias=True).encode("utf-8")
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aad = acceptance_input_aad(
        record_id=record_id,
        deployment_id=acceptance_input.deployment_id,
        provider_contract_sha256=acceptance_input.provider_contract_sha256,
    )
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)

    credential = AzureCliCredential(tenant_id=cast(str, arguments.tenant_id))
    crypto = CryptographyClient(cast(str, arguments.cache_key_id), credential)
    cosmos = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    try:
        wrapped = crypto.wrap_key(KeyWrapAlgorithm.rsa_oaep_256, data_key)
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        document = {
            "id": record_id,
            "recordType": "fabricAcceptanceInput",
            "schemaVersion": 1,
            "runId": acceptance_input.run_id,
            "deploymentId": acceptance_input.deployment_id,
            "providerContractSha256": acceptance_input.provider_contract_sha256,
            "keyId": cast(str, arguments.cache_key_id),
            "algorithm": "RSA-OAEP-256",
            "wrappedDek": encode(bytes(wrapped.encrypted_key)),
            "nonce": encode(nonce),
            "ciphertext": encode(ciphertext),
            "expiresAt": expires_at.isoformat(),
            "ttl": 3600,
        }
        container = cosmos.get_database_client(cast(str, arguments.database)).get_container_client(
            cast(str, arguments.container)
        )
        container.create_item(document)
        return record_id
    finally:
        crypto.close()
        cosmos.close()
        credential.close()


def main() -> int:
    arguments = parse_arguments()
    try:
        record_id = publish(arguments)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        print("FAIL: Fabric acceptance input preparation failed", file=sys.stderr)
        return 1
    print(f"PASS: Fabric acceptance input prepared id={record_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
