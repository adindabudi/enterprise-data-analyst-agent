from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import cast
from uuid import UUID

from azure.identity.aio import AzureCliCredential
from eda_worker.fabric.mcp_client import FabricMcpClient, json_object, object_list
from eda_worker.fabric.readiness import build_provider_contract

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "services" / "worker" / "tests" / "fabric" / "fixtures" / "tools-list-six.json"
DEFAULT_OUTPUT = ROOT / ".artifacts" / "fabric-provider-contract.json"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
AZURE_CLI = which("az") or "/usr/bin/az"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and pin the direct Fabric IQ MCP provider contract.")
    parser.add_argument("--fabric-tenant-id", default=os.getenv("FABRIC_TENANT_ID"))
    parser.add_argument("--model-deployment", default=os.getenv("EDA_FOUNDRY_MODEL_DEPLOYMENT"))
    parser.add_argument("--base-model", default=os.getenv("EDA_FOUNDRY_BASE_MODEL"))
    parser.add_argument("--deployment-id", default=os.getenv("EDA_DEPLOYMENT_ID"))
    parser.add_argument("--run-id", default=os.getenv("EDA_FABRIC_CONTRACT_RUN_ID"))
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    for name in ("fabric_tenant_id", "model_deployment", "base_model", "deployment_id", "run_id"):
        if not getattr(arguments, name):
            parser.error(f"--{name.replace('_', '-')} is required")
    return arguments


async def discover_contract(arguments: argparse.Namespace) -> str:
    fabric_tenant_id = UUID(cast(str, arguments.fabric_tenant_id))
    _verify_cli_context(fabric_tenant_id)
    expected_tools = _read_tools(cast(Path, arguments.fixture))
    credential = AzureCliCredential(tenant_id=str(fabric_tenant_id))
    try:
        token = await credential.get_token(POWER_BI_SCOPE)

        async def token_provider() -> str:
            return token.token

        client = FabricMcpClient(token_provider=token_provider, expected_tools=expected_tools)
        discovered = await client.discover_tools()
        contract = build_provider_contract(
            tools=list(discovered.values()),
            expected_tools=expected_tools,
            fabric_tenant_id=fabric_tenant_id,
            model_deployment=cast(str, arguments.model_deployment),
            base_model=cast(str, arguments.base_model),
            deployment_id=cast(str, arguments.deployment_id),
            run_id=cast(str, arguments.run_id),
            probed_at=datetime.now(UTC),
        )
        _write_json(cast(Path, arguments.output), contract.model_dump(mode="json", by_alias=True))
        return contract.endpoint_sha256
    finally:
        await credential.close()


def _verify_cli_context(expected_tenant_id: UUID) -> None:
    config_dir_value = os.getenv("AZURE_CONFIG_DIR")
    if not config_dir_value:
        raise ValueError("AZURE_CONFIG_DIR must select the isolated Fabric CLI context")
    config_dir = Path(config_dir_value).resolve()
    if not config_dir.is_dir() or stat.S_IMODE(config_dir.stat().st_mode) & 0o077:
        raise ValueError("isolated Fabric CLI context must exist with mode 0700")
    result = subprocess.run(  # noqa: S603
        [AZURE_CLI, "account", "show", "--query", "tenantId", "--output", "tsv"],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip().lower() != str(expected_tenant_id):
        raise ValueError("active CLI tenant does not match the configured Fabric tenant")


def _read_tools(path: Path) -> list[dict[str, object]]:
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("unable to read the pinned Fabric tool fixture") from error
    document = json_object(payload)
    if set(document) != {"tools"}:
        raise ValueError("pinned Fabric tool fixture is malformed")
    return [json_object(item) for item in object_list(document["tools"])]


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    arguments = parse_arguments()
    try:
        endpoint_hash = asyncio.run(discover_contract(arguments))
    except (OSError, subprocess.SubprocessError, ValueError):
        cast(Path, arguments.output).unlink(missing_ok=True)
        print("FAIL: Fabric IQ provider contract discovery failed", file=sys.stderr)
        return 1
    print(f"PASS: Fabric IQ provider contract {endpoint_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
