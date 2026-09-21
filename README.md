# Enterprise Data Analyst

A private analyst workspace that runs in your own Azure subscription. Ask a question in chat, and a Foundry Hosted workflow plans the work, runs code in an isolated sandbox, validates what it produced, and publishes the file for you to download.

It is built on the Microsoft Agent Framework, Azure Container Apps, and storage the deployment owns. There is no shared backend and no other tenant's data.

- **Two speeds.** Short questions are answered inline. Anything needing code, a file, or work that must survive a disconnect goes to a resilient background response you can steer or cancel.
- **Files you can open.** Workbooks, reports, and charts are generated in the sandbox and checked by a deterministic validator. Nothing is published until its validator passes.
- **Optional packs, off by default.** Fabric and Office documents stay disabled until you enable them and their acceptance evidence passes.

## Run it locally

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24 with npm 11.6.2 or later (below 12), Docker Compose, and Poppler (`pdftoppm`) for PDF validation tests.

```sh
make bootstrap   # install Python and Node dependencies
make check       # contracts, lint, types, tests
make integration # local emulators: Redis and Azurite
```

Fabric is not required. The core pack runs against synthetic data.

Run `make source-notices` after changing dependencies to regenerate [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This source inventory does not replace release-image SBOM, vulnerability, or secret-scanning gates.

## Deploy to your own subscription

### Before you start

You need an Azure subscription, an Entra tenant where you can register an application, and Foundry quota for `gpt-5.6-terra` in your region.

Add to the local toolchain above: Azure CLI 2.80+, azd 1.31.1+, the `azure.ai.agents` azd extension 1.0.0-beta.11+, Docker, `jq`, and the Bicep extension (`az bicep install`). Application images are built remotely in ACR, including from ARM64 hosts.

The image build hooks also require [Syft](https://github.com/anchore/syft) and [Trivy](https://github.com/aquasecurity/trivy) on your PATH for SBOM generation and vulnerability scanning. The full release gate pins Syft to 1.49.0; see the [release checklist](docs/runbooks/release-checklist.md) for the remaining release tools.

Permissions are the step people miss. Read [deployer permissions](docs/operations/deployer-permissions.md) first — it lists each role and why provisioning needs it.

Register the resource providers once per subscription:

```sh
for provider in Microsoft.App Microsoft.Cache Microsoft.CognitiveServices \
  Microsoft.Consumption Microsoft.DocumentDB \
  Microsoft.Insights Microsoft.OperationalInsights Microsoft.Storage; do
  az provider register --namespace "$provider"
done
```

### Deploy

```sh
azd auth login
azd env new <environment-name>

azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_TENANT_ID <tenant-id>
azd env set AZURE_LOCATION southeastasia
azd env set AZURE_MONTHLY_BUDGET_AMOUNT 500
azd env set EDA_MODEL_PROFILE gpt-5.6-terra-medium-v1
azd env set FOUNDRY_MODEL_CAPACITY 100
azd env set EDA_DOCTOR_NONINTERACTIVE 1

# Each flag confirms you accept the cost and security posture of that service.
# Read docs/operations/cost-controls.md before setting them.
azd env set EDA_DEFENDER_CONFIRMED true
azd env set EDA_SANDBOXES_PREVIEW_CONFIRMED true
azd env set EDA_REDIS_SKU_CONFIRMED true

azd up
```

Provisioning runs `scripts/doctor.sh` before anything is created. It checks tool versions, required variables, provider registration, and whether every resource type exists in your region, then stops on the first problem and names it. Fix that one thing and rerun.

The worker and API images embed a model contract from `.artifacts/`, and that contract is generated against a live Foundry deployment. On a first deployment, provisioning stops at the image build until those artifacts exist. Generate them with `make model-contract` using the Foundry values from your azd environment, then rerun `azd up`. [Model compatibility](docs/runbooks/model-compatibility.md) lists the required inputs.

The rest — resource group, Foundry agent endpoint and identity, image digests, and Sandbox group coordinates — is written back into your azd environment by the deployment hooks. Do not set those by hand.

### Confirm it worked

```sh
curl -s "$(azd env get-value API_URL)/health/ready" | jq
```

`status` reads `ready` once the core pack is live. Optional packs read `disabled` until you turn them on.

### Optional packs

Every pack is fail-closed. Setting a flag makes it `configured`; only its acceptance gate can promote it to `ready`. A prior deployment, a passing local test, or a same-tenant smoke never counts as evidence.

| Pack                       | Flags               | Guide                                                          |
| -------------------------- | ------------------- | -------------------------------------------------------------- |
| Fabric semantic model      | `FABRIC_ENABLED`    | [fabric-iq.md](docs/runbooks/fabric-iq.md)                     |
| Fabric ontology            | `FABRIC_ENABLED`    | [fabric-ontology-lab.md](docs/runbooks/fabric-ontology-lab.md) |
| Documents: DOCX, PDF, PPTX | `DOCUMENTS_ENABLED` | [document-pack.md](docs/runbooks/document-pack.md)             |

Fabric also requires two distinct tenants: your product tenant and a separate Fabric tenant. A same-tenant setup can be used to try things out, but it can never reach `ready`.

The Document Pack downloads source-available skills only after you accept their terms explicitly. The Apache-licensed Web Artifact Pack is included in the runtime for validated, self-contained interactive HTML when requested. Export requests produce only the requested file formats; an XLSX export does not add a dashboard.

The sandbox uses Plotly/Kaleido for charts, PyPNG for PNG validation, `pypdf` and Poppler for PDF validation, Pydyf and `pdf-lib` for PDF creation, and PptxGenJS/`pptx2json` for PowerPoint creation and reading. Pillow, Matplotlib, Seaborn, ReportLab, PDFium, and `python-pptx` are not installed. Acquired skill scripts that require those packages must be adapted to these tools before they can pass Document Pack acceptance; enabling the pack alone does not prove compatibility.

The HTML bundler checks the final file in offline Chromium before writing its output. JavaScript startup errors, an empty application root, and non-embedded resource requests fail the build so the agent can repair the source. This initial-render check does not validate every interaction or retroactively change published files.

## Operate it

### Session recovery

Cosmos DB stores sessions, canonical messages, tasks, checklists, and published file metadata. Blob Storage stores the published file bytes. The workspace restores saved analyses through owner-scoped APIs and lists files from every task in the session, including after a browser reload or API container restart.

Interactive model context is also stored in Cosmos DB; Redis supplies disposable live-event projections and a migration fallback for older context. Context larger than 512,000 characters is discarded with a warning, without deleting canonical message history. Sessions and tasks keep their existing 30-day retention. Unpublished sandbox scratch files are temporary and are not recoverable outputs.

### Runbooks

Start at the [runbook index](docs/runbooks/README.md). It maps a symptom to the runbook that fixes it, and lists the known issues worth reading before you hit them.

What drives cost, and which levers to pull first, is in [cost controls](docs/operations/cost-controls.md).

## Repository layout

| Path               | What lives there                                                    |
| ------------------ | ------------------------------------------------------------------- |
| `apps/api`         | FastAPI backend: auth, sessions, chat streaming, artifact downloads |
| `apps/web`         | React workspace built with Fluent UI                                |
| `services/worker`  | Hosted workflow, agent harness, tools, cleanup and acceptance jobs  |
| `services/sandbox` | Task-scoped isolated execution and artifact validation              |
| `packages/`        | Shared contracts, artifact validators, provenance, Fabric auth      |
| `infra/`           | Bicep and Terraform, kept at parity                                 |
| `scripts/`         | Preflight doctors, acceptance gates, contract publication           |
| `docs/runbooks`    | Operational procedures                                              |

The dev loop and contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Support And Security

This repository is community-supported and does not include a Microsoft product support commitment. See [SUPPORT.md](SUPPORT.md) for support guidance and [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.
