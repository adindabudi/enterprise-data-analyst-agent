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
