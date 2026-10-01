"""Capture the schema snapshot the analyst pins in its instructions. Run it when the ontology changes.

The agent never discovers the source at run time; it writes GQL and KQL from this
snapshot. The job reads, once:

1. entity types, properties and time-series bindings from the ontology MCP
   (`list_ontology_entity_types`);
2. relationship names and directions, which that listing omits, with one GQL
   introspection query;
3. the stored values of every string property that holds at most --max-values
   values, with one grouped GQL query per property. Without them an agent filters
   on 'Cascade General' where the data says 'Lamna Healthcare Cascade General'.

The ontology MCP accepts a user token only, so run it signed in as a person
(`az login`) who can read the ontology and its graph. Each run reads the ontology
definition, which Fabric meters, and wakes the graph if it is idle.

    uv run python scripts/build-fabric-schema-snapshot.py --catalog path/to/fabric-ontologies.json
    uv run python scripts/build-fabric-schema-snapshot.py --input .artifacts/fabric-schema-snapshot.json --publish

Review the written JSON and the printed instructions before publishing: every user
who links Fabric in the deployment sees them. Exclude a property whose values must
not be shown with --exclude-values Entity.Property. After publishing, restart the
API revision so the runtime loads the new snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential
from eda_api.fabric_auth.graph import (
    FABRIC_API,
    GraphQueryError,
    graph_query_url,
    run_graph_query,
    validate_graph_statement,
)
from eda_api.fabric_auth.mcp_session import open_streamable_http_session
from eda_api.fabric_auth.ontology import list_entity_types
from eda_api.fabric_auth.snapshot import (
    MAX_STORED_VALUES,
    RELATIONSHIP_QUERY,
    SchemaSnapshot,
    build_snapshot,
    render_snapshot,
    stored_values_from_rows,
    stored_values_query,
    value_candidates,
)
from eda_api.fabric_ontology import OntologyTarget

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".artifacts" / "fabric-schema-snapshot.json"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
# The graph is shared with real users; the value reads are few and small, so a little parallelism is enough.
VALUE_QUERY_CONCURRENCY = 4
LISTING_ATTEMPTS = 3


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, review and publish the Fabric source schema snapshot.")
    parser.add_argument("--catalog", type=Path, help="FABRIC_ONTOLOGIES_JSON document; defaults to that variable")
    parser.add_argument("--alias", help="catalog entry to capture when the catalog holds more than one")
    parser.add_argument("--input", type=Path, help="publish an already reviewed snapshot instead of building one")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-values", type=int, default=MAX_STORED_VALUES, choices=range(1, MAX_STORED_VALUES + 1))
    parser.add_argument(
        "--exclude-values",
        action="append",
        default=[],
        metavar="ENTITY.PROPERTY",
        help="never list this property's stored values (repeatable)",
    )
    parser.add_argument("--tenant-id", default=os.getenv("FABRIC_TENANT_ID"))
    parser.add_argument("--publish", action="store_true", help="upsert the snapshot into the runtime container")
    parser.add_argument("--cosmos-endpoint", default=os.getenv("EDA_COSMOS_ENDPOINT"))
    parser.add_argument("--database", default=os.getenv("EDA_COSMOS_DATABASE", "enterprise-data-analyst"))
    parser.add_argument("--container", default=os.getenv("EDA_COSMOS_RUNTIME_CONTAINER", "runtime"))
    return parser.parse_args()


def load_catalog(path: Path | None, alias: str | None) -> tuple[str, OntologyTarget]:
    raw = path.read_text(encoding="utf-8") if path is not None else os.getenv("FABRIC_ONTOLOGIES_JSON")
    if not raw:
        raise ValueError("pass --catalog or set FABRIC_ONTOLOGIES_JSON")
    document = json.loads(raw)
    if not isinstance(document, dict) or not document:
        raise ValueError("the catalog must be a JSON object of alias to source")
    catalog = cast(dict[str, object], document)
    chosen = alias or (next(iter(catalog)) if len(catalog) == 1 else None)
    if chosen is None or chosen not in catalog:
        raise ValueError(f"choose one of the catalog aliases with --alias: {', '.join(sorted(catalog))}")
    return chosen, OntologyTarget.model_validate(catalog[chosen])


async def resolve_graph_model(client: httpx.AsyncClient, token: str, target: OntologyTarget) -> UUID:
    if target.graph_model_id is not None:
        return target.graph_model_id
    items = await _list_items(client, token, target.workspace_id, "GraphModel")
    marker = target.ontology_id.hex
    for item in items:
        name, item_id = item.get("displayName"), item.get("id")
        if isinstance(name, str) and isinstance(item_id, str) and marker in name.replace("-", "").lower():
            print(f"note: set graphModelId to {item_id} in the catalog so readers need not list the workspace")
            return UUID(item_id)
    raise ValueError("no graph in the workspace carries this ontology's ID; set graphModelId in the catalog")


async def _list_items(
    client: httpx.AsyncClient, token: str, workspace_id: UUID, item_type: str
) -> list[dict[str, Any]]:
    response = await client.get(
        f"{FABRIC_API}/v1/workspaces/{workspace_id}/items?type={item_type}",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    values = cast(dict[str, Any], response.json()).get("value")
    if not isinstance(values, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[object], values) if isinstance(item, dict)]


async def read_listing(target: OntologyTarget, token: str) -> list[dict[str, object]]:
    for attempt in range(1, LISTING_ATTEMPTS + 1):
        try:
            async with open_streamable_http_session(target.endpoint, token) as session:
                await session.initialize()
                return await list_entity_types(session)
        except Exception:
            # The endpoint's first session after idle sometimes drops; a real refusal repeats.
            if attempt == LISTING_ATTEMPTS:
                raise
            await asyncio.sleep(5 * attempt)
    raise RuntimeError("unreachable")


async def graph_rows(client: httpx.AsyncClient, token: str, url: str, query: str) -> list[object]:
    evidence = await run_graph_query(client, url=url, bearer_token=token, statement=validate_graph_statement(query))
    if evidence.source_incomplete:
        raise GraphQueryError("the graph truncated an introspection result")
    return cast(list[object], json.loads(evidence.complete))


async def read_stored_values(
    client: httpx.AsyncClient,
    token: str,
    url: str,
    candidates: list[tuple[str, str]],
    max_values: int,
) -> dict[tuple[str, str], tuple[str, ...] | None]:
    gate = asyncio.Semaphore(VALUE_QUERY_CONCURRENCY)

    async def one(entity: str, prop: str) -> tuple[tuple[str, str], tuple[str, ...] | None]:
        query = stored_values_query(entity, prop, max_values=max_values)
        if query is None:
            print(f"skip values: {entity}.{prop} is not a plain identifier", file=sys.stderr)
            return (entity, prop), None
        async with gate:
            try:
                rows = await graph_rows(client, token, url, query)
            except GraphQueryError as error:
                print(f"skip values: {entity}.{prop}: {error}", file=sys.stderr)
                return (entity, prop), None
        return (entity, prop), stored_values_from_rows(rows, max_values=max_values)

    return dict(await asyncio.gather(*(one(entity, prop) for entity, prop in candidates)))


async def kql_database_hints(
    client: httpx.AsyncClient, token: str, target: OntologyTarget, listing: list[dict[str, object]]
) -> None:
    """Time series are read through a KQL database item, which the catalog names; say which one the bindings use."""
    databases: set[str] = set()
    for entity in listing:
        for mapping in cast(list[object], entity.get("mappings") or []):
            if not isinstance(mapping, dict):
                continue
            configuration = cast(dict[str, Any], mapping).get("mappingConfiguration")
            if not isinstance(configuration, dict):
                continue
            settings = cast(dict[str, Any], configuration)
            if settings.get("mappingType") == "TimeSeries":
                name = cast(dict[str, Any], settings.get("sourceTableProperties") or {}).get("databaseName")
                if isinstance(name, str):
                    databases.add(name)
    if not databases:
        return
    matches = [
        item
        for item in await _list_items(client, token, target.workspace_id, "KQLDatabase")
        if item.get("displayName") in databases
    ]
    ids = sorted({str(item.get("id")) for item in matches})
    if target.kql_database_id is None or str(target.kql_database_id) not in ids:
        found = ", ".join(ids) or "none visible in this workspace"
        print(
            f"note: time series are bound to KQL database(s) {sorted(databases)}; set kqlDatabaseId to one of: {found}"
        )


async def build(arguments: argparse.Namespace) -> SchemaSnapshot:
    alias, target = load_catalog(arguments.catalog, arguments.alias)
    started = time.perf_counter()
    credential = AzureCliCredential(tenant_id=arguments.tenant_id) if arguments.tenant_id else AzureCliCredential()
    try:
        token = credential.get_token(FABRIC_SCOPE).token
    finally:
        credential.close()
    listing = await read_listing(target, token)
    excluded = {
        (entity, prop)
        for entity, _, prop in (item.partition(".") for item in cast(list[str], arguments.exclude_values))
        if entity and prop
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), follow_redirects=False) as client:
        graph_model_id = await resolve_graph_model(client, token, target)
        url = graph_query_url(target.workspace_id, graph_model_id)
        # The first query also wakes an idle graph, which takes about half a minute.
        relationship_rows = await graph_rows(client, token, url, RELATIONSHIP_QUERY)
        candidates = [candidate for candidate in value_candidates(listing) if candidate not in excluded]
        stored_values = await read_stored_values(client, token, url, candidates, cast(int, arguments.max_values))
        await kql_database_hints(client, token, target, listing)
    snapshot = build_snapshot(
        alias=alias,
        target=target,
        graph_model_id=graph_model_id,
        listing=listing,
        relationship_rows=relationship_rows,
        stored_values=stored_values,
        generated_at=datetime.now(UTC),
    )
    rendered = render_snapshot(snapshot, description=target.description, timeseries=target.kql_database_id is not None)
    listed = sum(1 for values in stored_values.values() if values)
    print(rendered)
    print(
        f"\nbuilt in {time.perf_counter() - started:.1f} s: {len(snapshot.entities)} entities, "
        f"{len(snapshot.relationships)} relationships, stored values for {listed} of {len(candidates)} string "
        f"properties, {len(rendered):,} characters (about {len(rendered) // 4:,} tokens)"
    )
    return snapshot


def publish(arguments: argparse.Namespace, snapshot: SchemaSnapshot) -> None:
    if not arguments.cosmos_endpoint:
        raise ValueError("--publish requires --cosmos-endpoint or EDA_COSMOS_ENDPOINT")
    credential = AzureCliCredential(tenant_id=arguments.tenant_id) if arguments.tenant_id else AzureCliCredential()
    cosmos = CosmosClient(cast(str, arguments.cosmos_endpoint), credential=credential)
    try:
        container = cosmos.get_database_client(cast(str, arguments.database)).get_container_client(
            cast(str, arguments.container)
        )
        container.upsert_item(snapshot.model_dump(mode="json", by_alias=True))
    finally:
        cosmos.close()
        credential.close()
    print(f"published {snapshot.id}; restart the API revision so the runtime loads it")


def write_snapshot(path: Path, snapshot: SchemaSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(snapshot.model_dump(mode="json", by_alias=True), indent=2) + "\n", encoding="utf-8")
    staging.replace(path)


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.input is not None:
            snapshot = SchemaSnapshot.model_validate_json(cast(Path, arguments.input).read_text(encoding="utf-8"))
        else:
            snapshot = asyncio.run(build(arguments))
            write_snapshot(cast(Path, arguments.output), snapshot)
            print(f"wrote {arguments.output}; review it before publishing")
        if arguments.publish:
            publish(arguments, snapshot)
    except (OSError, ValueError, httpx.HTTPError, GraphQueryError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
