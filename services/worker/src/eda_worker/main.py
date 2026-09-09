from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import os
from pathlib import Path

from eda_worker.acceptance.fabric import acceptance_passed
from eda_worker.acceptance.fabric_ontology import AcceptanceEvidence, validate_ontology_acceptance
from eda_worker.acceptance.runtime import run_semantic_acceptance_job
from eda_worker.cleanup import (
    AzureBlobCleanup,
    CleanupResult,
    CleanupService,
    CosmosCleanupWorkspace,
    parse_cleanup_before,
)
from eda_worker.config import WorkerSettings
from eda_worker.documents.publication import publish_document_contract
from eda_worker.hosted_server import run_hosted_server
from eda_worker.telemetry import configure_logging, configure_telemetry


def health() -> dict[str, str]:
    return {"status": "alive", "component": "worker"}


async def run_cleanup(*, before: str, limit: int, dry_run: bool) -> CleanupResult:
    required_settings = (
        "EDA_COSMOS_ENDPOINT",
        "EDA_COSMOS_DATABASE",
        "EDA_COSMOS_WORKSPACE_CONTAINER",
        "EDA_BLOB_ACCOUNT_URL",
        "EDA_BLOB_SESSIONS_CONTAINER",
    )
    missing_settings = [setting for setting in required_settings if not os.environ.get(setting)]
    if missing_settings:
        raise RuntimeError(f"missing cleanup configuration: {', '.join(missing_settings)}")

    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    managed_identity_client_id = os.environ.get("EDA_MANAGED_IDENTITY_CLIENT_ID")
    credential = None
    cosmos_client = None
    blob_service = None
    try:
        credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_client_id)
        cosmos_client = CosmosClient(os.environ["EDA_COSMOS_ENDPOINT"], credential=credential)
        blob_service = BlobServiceClient(account_url=os.environ["EDA_BLOB_ACCOUNT_URL"], credential=credential)
        database = cosmos_client.get_database_client(os.environ["EDA_COSMOS_DATABASE"])
        workspace = CosmosCleanupWorkspace(database.get_container_client(os.environ["EDA_COSMOS_WORKSPACE_CONTAINER"]))
        blobs = AzureBlobCleanup(blob_service.get_container_client(os.environ["EDA_BLOB_SESSIONS_CONTAINER"]))
        return await CleanupService(workspace=workspace, blobs=blobs).run(
            now=parse_cleanup_before(before),
            limit=limit,
            dry_run=dry_run,
        )
    finally:
        if blob_service is not None:
            await blob_service.close()
        if cosmos_client is not None:
            await cosmos_client.close()
        if credential is not None:
            await credential.close()


async def run_document_contract_publication() -> str:
    required_settings = (
        "EDA_COSMOS_ENDPOINT",
        "EDA_COSMOS_DATABASE",
        "EDA_COSMOS_RUNTIME_CONTAINER",
        "EDA_DOCUMENT_IMAGE_CONTRACT_BASE64",
    )
    missing_settings = [setting for setting in required_settings if not os.environ.get(setting)]
    if missing_settings:
        raise RuntimeError(f"missing document publication configuration: {', '.join(missing_settings)}")

    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import ManagedIdentityCredential

    from eda_worker.acceptance.documents import DocumentImageContract

    encoded = os.environ["EDA_DOCUMENT_IMAGE_CONTRACT_BASE64"]
    if len(encoded) > 32_768:
        raise ValueError("document image contract exceeds the encoded size limit")
    try:
        contract_payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("document image contract encoding is invalid") from error
    contract = DocumentImageContract.model_validate_json(contract_payload)
    client_id = os.environ.get("EDA_MANAGED_IDENTITY_CLIENT_ID")
    credential = ManagedIdentityCredential(client_id=client_id)
    client = CosmosClient(os.environ["EDA_COSMOS_ENDPOINT"], credential=credential)
    container = client.get_database_client(os.environ["EDA_COSMOS_DATABASE"]).get_container_client(
        os.environ["EDA_COSMOS_RUNTIME_CONTAINER"]
    )
    try:
        return await publish_document_contract(container, contract)  # type: ignore[arg-type]
    finally:
        await client.close()
        await credential.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="eda-worker")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("run")
    subcommands.add_parser("publish-documents")
    cleanup = subcommands.add_parser("cleanup")
    cleanup.add_argument("--before", required=True)
    cleanup.add_argument("--limit", type=int, default=100)
    cleanup.add_argument("--dry-run", action="store_true")
    fabric_acceptance = subcommands.add_parser("accept-fabric")
    fabric_acceptance.add_argument("--provider", required=True, choices=("semantic_model", "ontology"))
    fabric_acceptance.add_argument("--evidence", type=Path)
    fabric_acceptance.add_argument("--run-id", default=os.getenv("EDA_FABRIC_ACCEPTANCE_RUN_ID"))
    arguments = parser.parse_args()
    configure_logging()
    configure_telemetry()
    if arguments.command == "run":
        run_hosted_server(WorkerSettings.model_validate({}))
        return
    if arguments.command == "publish-documents":
        try:
            digest = asyncio.run(run_document_contract_publication())
        except (OSError, RuntimeError, ValueError) as error:
            parser.error(str(error))
        print({"feature": "documents", "state": "configured", "contractSha256": digest})
        return
    if arguments.command == "accept-fabric":
        if arguments.provider == "semantic_model":
            if arguments.evidence is not None or not arguments.run_id:
                parser.error("semantic-model acceptance requires --run-id and does not accept --evidence")
            try:
                result = asyncio.run(run_semantic_acceptance_job(arguments.run_id))
            except (OSError, RuntimeError, ValueError) as error:
                parser.error(str(error))
            print({"provider": result.provider, "state": "passed" if acceptance_passed(result) else "failed"})
            if not acceptance_passed(result):
                raise SystemExit(1)
            return
        if arguments.evidence is None:
            parser.error("ontology acceptance requires --evidence")
        try:
            evidence = validate_ontology_acceptance(
                AcceptanceEvidence.model_validate_json(arguments.evidence.read_text())
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(
            {
                "provider": evidence.provider,
                "state": evidence.state,
                "topology": evidence.topology,
                "runId": evidence.run_id,
            }
        )
        return
    try:
        result = asyncio.run(run_cleanup(before=arguments.before, limit=arguments.limit, dry_run=arguments.dry_run))
    except ValueError as error:
        parser.error(str(error))
    print(
        {"deletedSessions": result.deleted_sessions, "failedSessions": result.failed_sessions, "dryRun": result.dry_run}
    )
    if result.failed_sessions:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
