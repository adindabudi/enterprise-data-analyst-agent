from __future__ import annotations

import argparse
import sys
from typing import Protocol, TextIO, cast

from azure.containerapps.sandbox import RegistryCredentials, SandboxGroupClient, endpoint_for_region
from azure.identity import DefaultAzureCredential

ACR_LOGIN_SENTINEL = "00000000-0000-0000-0000-000000000000"


class CreatedDiskImage(Protocol):
    id: str


class DiskImagePoller(Protocol):
    def result(self) -> CreatedDiskImage: ...


class DiskImageCreator(Protocol):
    def begin_create_disk_image(
        self,
        base_image: str,
        *,
        name: str,
        registry_credentials: RegistryCredentials | None,
        managed_identity_resource_id: str | None,
    ) -> DiskImagePoller: ...


def read_registry_credentials(stream: TextIO) -> RegistryCredentials:
    token = stream.read().strip()
    if not token:
        raise ValueError("registry token is empty")
    return RegistryCredentials(username=ACR_LOGIN_SENTINEL, token=token)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--sandbox-group", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--managed-identity-resource-id")
    parser.add_argument("--registry-token-stdin", action="store_true")
    arguments = parser.parse_args()
    if arguments.registry_token_stdin and arguments.managed_identity_resource_id:
        parser.error("registry token and managed identity authentication are mutually exclusive")

    registry_credentials = read_registry_credentials(sys.stdin) if arguments.registry_token_stdin else None

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(arguments.region),
        credential,
        subscription_id=arguments.subscription_id,
        resource_group=arguments.resource_group,
        sandbox_group=arguments.sandbox_group,
    )
    try:
        image = (
            cast(DiskImageCreator, client)
            .begin_create_disk_image(
                arguments.base_image,
                name=arguments.name,
                registry_credentials=registry_credentials,
                managed_identity_resource_id=arguments.managed_identity_resource_id,
            )
            .result()
        )
        print(image.id)
    finally:
        client.close()
        credential.close()


if __name__ == "__main__":
    main()
