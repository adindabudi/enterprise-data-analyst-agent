# Fabric Ontology Acceptance Lab

This runbook creates and validates the external Lamna Healthcare acceptance fixture for the optional Fabric ontology provider. The fixture contains fictitious, synthetic data only. Do not upload PHI or any production data.

The Fabric workspace is operator-owned SaaS infrastructure. Item creation remains manual because this repository has not verified a stable public ontology item-definition API. Do not use Bicep, Terraform, or other IaC to create a Fabric workspace, lakehouse, Eventhouse, semantic model, or ontology item.

## Preconditions

- Use Fabric tenant D, distinct from the Foundry/product tenant F required by the cross-tenant gate.
- Create a dedicated nonproduction workspace named `eda-ontology-acceptance-<suffix>`.
- Assign paid F2+ or P1+ capacity before MCP acceptance. A trial capacity may be used to follow the lab, but it is not acceptance evidence.
- Set both ontology tenant settings: `FOUNDRY_TENANT_ID` and `FABRIC_ONTOLOGY_TENANT_ID`.
- Create one allowed and one denied B2B test user. The denied user has no ontology or bound-source access.

## Build The Fixture

1. Acquire the pinned archive through `scripts/fetch-lamna-fixture.py`; verify its hash before extracting it. Do not commit the archive or extracted CSV files.
2. Upload the five static CSV files to lakehouse `LamnaHealthcareLH`. Keep `VitalSignsReadings.csv` out of the lakehouse.
3. Ingest `VitalSignsReadings.csv` into Eventhouse/KQL database `LamnaHealthcareEH` as the `VitalSignsReadings` table. Verify its 20-row result.
4. Create Direct Lake semantic model `LamnaHealthcareModel` from the five lakehouse tables. Define four Direct Lake relationships from the pinned fixture contract, all many-to-one and bidirectional.
5. Generate ontology `LamnaHealthcareOntology`. Verify five entity keys and configure four relationship bindings from the fixture contract.
6. Bind the Eventhouse time series to `VitalSignEquipment`, using `EquipmentId` as the key and `Timestamp` plus the four reading properties.
7. Mark the ontology as published before any MCP contract discovery. Store the workspace and ontology item UUIDs only in secret-managed deployment configuration; never place them in prompts, source, or public artifacts.
8. Grant only the required item and source permissions to the allowed B2B fixture user. Confirm the denied user gets no data and no result artifact.

## Validate

Prepare a mode-0700 isolated Azure CLI context outside the repository and provide its path through `FABRIC_ONTOLOGY_AZURE_CONFIG_DIR`. The doctor never mutates global Azure CLI context and never invokes login, logout, account switching, or provisioning.

Provide the selected target catalog, paid-capacity evidence, and denied-user no-data probe as nonsecret local paths, then run:

```sh
./scripts/doctor-fabric-ontology.sh
```

The doctor only reports hashes, counts, and pass/fail status. It validates distinct tenant hashes, the selected `ontology` provider, app access through the prepared context, the F2+/P1+ evidence, catalog and fixture shape, B2B fixture identities, publication/reachability, the exact two-tool MCP contract, entity-key discovery, and the denied-user no-data result.

After doctor succeeds, publish the contract, optionally run the same-tenant smoke, and run the cross-tenant acceptance gate. The smoke cannot promote readiness.

## Publish the schema snapshot

The analyst never discovers the ontology at run time. It writes GQL for the graph and KQL for time series from a schema snapshot pinned in its instructions, so a source without a published snapshot gets no query tools: the API logs `Fabric source queries are unavailable`, and the Fabric status reports `chatQuery: false`.

```mermaid
flowchart LR
  job["Schema snapshot job<br/>(when the ontology changes)"] --> listing["Ontology MCP<br/>list_ontology_entity_types"]
  job --> introspection["GQL introspection<br/>relationships + stored values"]
  job --> record[("Runtime container<br/>fabric-schema-snapshot:alias")]
  record -. "loaded at startup" .-> agent["Analyst runtime<br/>snapshot in instructions"]
  user[User] --> agent
  agent -- "query_graph (GQL)" --> graph["Graph<br/>executeQuery?beta=true"]
  agent -- "query_timeseries (KQL)" --> kql["KQL database<br/>remote MCP executeQuery"]
```

Add the two items the tools read to the catalog entry in `FABRIC_ONTOLOGIES_JSON` (see `config/fabric-ontologies.example.json`):

- `graphModelId`: the graph Fabric generated from the ontology. Without it, every reader needs `Workspace.Read.All` to find it.
- `kqlDatabaseId`: the KQL database behind the ontology's time-series bindings. Without it the agent has no `query_timeseries` tool and says that time series cannot be read.

Build the snapshot and review it. The ontology MCP accepts only a user token, so sign in as a person who can read the ontology and its graph:

```sh
az login
uv run python scripts/build-fabric-schema-snapshot.py --catalog <catalog.json> \
  --exclude-values patients.FirstName --exclude-values patients.LastName
```

The job prints the instructions exactly as the agent reads them, with their size, and writes `.artifacts/fabric-schema-snapshot.json`. On the Lamna lab it took 26 seconds and produced about 8 KB (2,000 tokens). When `kqlDatabaseId` is missing or does not match the time-series bindings, it names the KQL database they use.

Before you publish, check what the snapshot shows:

- Every user who links Fabric in this deployment sees the snapshot, including every listed value. Use `--exclude-values Entity.Property` for any property whose values must not be shown. Queries still run with each user's own token, so Fabric still decides what data each user gets back.
- A string property is listed with its values only when it holds at most 30 (`--max-values`). Names, identifiers and free text are left unlisted, and the agent matches them exactly as the user wrote them.

Publish the reviewed file, then restart the API revision so the runtime loads it:

```sh
uv run python scripts/build-fabric-schema-snapshot.py --input .artifacts/fabric-schema-snapshot.json --publish
```

Publishing writes `fabric-schema-snapshot:<alias>` to the runtime container with your Azure CLI identity, which needs Cosmos DB data-plane write access. Like the contract scripts, run it from inside the VNet when Cosmos DB public access is disabled.

Run the job again whenever entity types, properties, relationships, time-series bindings or categorical values change. Each run reads the ontology definition, which Fabric meters, and wakes the graph if it is idle. Answering a question does neither.

Notes:

- The GQL Query API is beta (`beta=true`). A still-running query is followed through its continuation token for up to 120 seconds; a truncated response is flagged so no total is reported from it.
- The KQL route was checked with an Azure CLI user token. Confirm that your Fabric app's delegated grant reaches the KQL database's MCP endpoint in the acceptance tenant before you rely on it.
- The Fabric capacity status check still opens the ontology endpoint, without calling a tool, when a linked user opens the workspace and no recent reading exists. In the Fabric tool-path benchmark, an opened ontology session started the _Ontology Modeling_ meter window.

## Separate Indonesian Upstream Demo

The upstream pack is an additive, fictional demonstration, not a replacement for
the Lamna acceptance fixture or evidence that the optional packs are production-ready.
It uses public Indonesian geological analogues, not real operator well observations.
The generator keeps source facts and authored documents separate from evaluation
answer keys. Do not upload the `evaluation` directory to a Lakehouse source or
document knowledge base.

Generate and inspect the local artifacts from the repository root:

```sh
.venv/bin/python scripts/energy_demo_data.py --output .artifacts/energy-demo
.venv/bin/python scripts/energy_demo_fabric.py --pack .artifacts/energy-demo
.venv/bin/python scripts/energy_demo_documents.py --pack .artifacts/energy-demo
.venv/bin/python scripts/energy_demo_gql.py --pack .artifacts/energy-demo
```

These commands do not call Azure. The initial ontology blueprint is **unbound**.
After approval, create a dedicated, schema-enabled `IndonesiaEnergyDemoLH`, upload
only the source tables plus their schema/manifest, attach the generated notebook
to that Lakehouse, and run its preview. Its write mode is create-only and requires
`APPLY=True`. Never attach it to a Lamna or Centoso Lakehouse.

Compile the actual bindings only after the new Lakehouse UUID is known:

Spark can normalize managed table names to lowercase. Discover their actual names
under `Tables/dbo` in OneLake and provide an explicit contract-to-physical-name
JSON map (for example, `"Well": "well"`). Entity labels remain unchanged. The
Lakehouse List Tables REST endpoint currently rejects schema-enabled Lakehouses;
use OneLake directory listing or the Spark catalog rather than guessing paths.

```sh
.venv/bin/python scripts/energy_demo_fabric.py --pack .artifacts/energy-demo \
  --workspace "$FABRIC_WORKSPACE_ID" --lakehouse "$ENERGY_LAKEHOUSE_ID" \
  --table-names "$ENERGY_TABLE_NAME_MAP" --overwrite
```

The compiler includes stable entity/property IDs, directed contextualizations and
semantic enrichment. The current [REST definition article](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/ontology-definition)
documents enrichment, although its older machine-readable schemas do not fully
validate that metadata. Synonyms belong only on entities. Critical grain, units and
temporal rules also belong in agent instructions; relationship metadata is not
currently used by the public Data Agent experience.

Creation, first opening of the generated Graph in Fabric, ingestion/refresh,
permission checks and application catalog switching remain separate operations.
Do not select the new source before its row counts and directed edges match the
pack and its GQL regression cases pass. `energy_demo_live.py` records graph-level
results; it does not prove natural-language or document-retrieval accuracy.
Keep the old catalog for rollback and start a fresh chat after a source switch.

`config/energy-agent-instructions.txt` is an undeployed candidate policy, not a
precomputed answer prompt. `config/energy-research-evidence.json` records public
sources and their limits. The app's ontology schema budget accommodates richer
multi-entity sources and retains numeric/timestamp types. This source change still
requires a reviewed application deployment.

After `energy_demo_live.py` records a complete successful run, render the operator
guide with `--live-gql-report evaluation/<report-name>.json --overwrite`. This
replaces the offline source status with the report's observed GQL outcome, without
changing source PDF content or claiming natural-language agent accuracy. Keep the
report and guide in `evaluation`, never in a retrieval source.

The existing ontology MCP and direct GQL integration is **not** automatically a
Fabric Data Agent or Foundry IQ deployment. PDFs and schematics are prepared
evidence, not proof that document retrieval, image interpretation, ADME ingestion
or a 3D viewer has been configured.

## Cleanup

Perform workspace cleanup before capacity cleanup. The cleanup command defaults to dry run:

```sh
./scripts/cleanup-fabric-ontology-lab.sh \
  --workspace-id <workspace-uuid> \
  --expected-name eda-ontology-acceptance-<suffix>
```

Destructive execution requires the exact workspace UUID twice, the matching expected name, and an external ownership manifest that names the workspace, tenant D, `lamna-healthcare` fixture, and `enterprise-data-analyst` owner:

```sh
./scripts/cleanup-fabric-ontology-lab.sh \
  --workspace-id <workspace-uuid> \
  --confirm-workspace-id <same-workspace-uuid> \
  --expected-name eda-ontology-acceptance-<suffix> \
  --ownership-manifest /secure/path/fabric-ontology-ownership.json \
  --execute
```

The script point-reads the workspace under tenant D, checks its exact name and ownership manifest, deletes only `https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}`, and waits for the workspace to disappear. It does not delete a resource group as a Fabric cleanup mechanism, delete or pause capacity, remove B2B users, or delete the Entra application. Handle those actions separately and only after the workspace cleanup is confirmed.
