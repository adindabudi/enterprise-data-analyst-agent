# Runbooks

Start here when something breaks or when you are about to change the deployment.

## Find it by symptom

| What you are seeing                             | Go to                                                      |
| ----------------------------------------------- | ---------------------------------------------------------- |
| Users cannot sign in, or sign-in loops          | [entra-auth.md](entra-auth.md)                             |
| Live updates stall, chat feels stuck            | [redis-degraded-streaming.md](redis-degraded-streaming.md) |
| Redis is down                                   | [redis-outage.md](redis-outage.md)                         |
| Analyses stop progressing or never finish       | [analysis-recovery.md](analysis-recovery.md)               |
| Code execution fails, artifacts never appear    | [sandbox-failure.md](sandbox-failure.md)                   |
| An uploaded file is stuck or rejected           | [upload-quarantine.md](upload-quarantine.md)               |
| Data loss, or you need a point-in-time copy     | [storage-restore.md](storage-restore.md)                   |
| The model rejects requests, or quota errors     | [model-quota.md](model-quota.md)                           |
| A deployment behaves worse after a model change | [model-compatibility.md](model-compatibility.md)           |
| The bad release is live and you need it gone    | [rollback.md](rollback.md)                                 |

## Before you change the deployment

| Task                                 | Guide                                                                        |
| ------------------------------------ | ---------------------------------------------------------------------------- |
| Ship a release                       | [release-checklist.md](release-checklist.md)                                 |
| Keep Bicep and Terraform aligned     | [terraform-parity.md](terraform-parity.md)                                   |
| Enable Fabric semantic model         | [fabric-iq.md](fabric-iq.md)                                                 |
| Enable Fabric ontology               | [fabric-ontology-lab.md](fabric-ontology-lab.md)                             |
| Refresh the ontology schema snapshot | [fabric-ontology-lab.md](fabric-ontology-lab.md#publish-the-schema-snapshot) |
| Enable Office document generation    | [document-pack.md](document-pack.md)                                         |

## Known issues

Things that surprise people. Most are configuration rather than defects, but the first one blocks document tools.

**Document tools disappear after an image rebuild.**
Startup checks the feature record against the deployed worker and sandbox image digests. Republish the contract for the new digests, or set `DOCUMENTS_ENABLED=false` until you can. See [document-pack.md](document-pack.md).

**The analyst says no source is configured, but the UI shows Fabric connected.**
The API can report a linked Fabric grant before the selected provider contract has passed readiness. Re-run the provider contract and acceptance gates; an environment variable alone cannot promote the provider.

**Fabric stays `configured` and never reaches `ready`.**
Readiness requires two distinct tenants. If your Fabric tenant ID equals your product tenant ID, the acceptance gate rejects it by design. A same-tenant setup is a smoke test only.

**Fabric is linked, but the analyst has no tool to query the ontology.**
The source's schema snapshot is missing, invalid, or describes a different source, so the API registers no query tools and logs `Fabric source queries are unavailable`. Build and publish the snapshot, then restart the API revision. See [fabric-ontology-lab.md](fabric-ontology-lab.md#publish-the-schema-snapshot).

**Building images on an ARM machine produces something Container Apps will not run.**
Apple Silicon and ARM WSL hosts build `linux/arm64` by default. Use the repository deployment hooks, which build with ACR remotely. Never push a locally built image straight to Container Apps.

**Contract publication scripts fail with `Forbidden ... through public internet`.**
Cosmos DB is reachable only through its private endpoint. Run those scripts from inside the VNet — a self-hosted CI runner or a jump host. Adding a firewall IP rule will not help when public network access is disabled.

**`ModuleNotFoundError` for a workspace package after `uv sync`.**
Plain `uv sync` removes the workspace members. Use `uv sync --all-packages --frozen`, which is what `make bootstrap` runs.

**Frontend tests fail in parallel but pass one at a time.**
Vitest can time out terminating fork workers on some hosts, and the failures look like real assertion errors. Confirm with `npm test --workspace=@eda/web -- --maxWorkers=1` before investigating.
