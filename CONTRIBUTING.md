# Contributing

## The loop

```sh
make bootstrap   # uv sync --all-packages --frozen, then npm ci
make check       # contracts, lint, types, tests, contract-drift
```

`make check` is the gate. It regenerates the JSON Schema and TypeScript contracts, runs Ruff and ESLint, runs Pyright and `tsc`, runs both test suites, and then fails if regeneration changed a tracked file. Run it before you push.

Write the test first. Every change here is expected to arrive with the test that would have caught the bug.

Work in an isolated worktree, not on a shared checkout.

## Where things live

| Path                  | What it owns                                                                 |
| --------------------- | ---------------------------------------------------------------------------- |
| `apps/api`            | HTTP surface: auth, sessions, chat streaming, artifact downloads             |
| `apps/web`            | React workspace                                                              |
| `services/worker`     | Durable orchestration, agent harness, tools, Fabric providers                |
| `services/sandbox`    | Code execution and artifact validation                                       |
| `packages/contracts`  | Shared models; the JSON Schema and TypeScript types are generated from these |
| `packages/artifacts`  | Format validators (XLSX, HTML, PDF, OOXML, PBIR)                             |
| `packages/provenance` | Task manifest models                                                         |
| `infra/`              | Bicep and Terraform, kept at parity                                          |
| `scripts/`            | Preflight doctors, acceptance gates, contract publication                    |

## Common changes

**Adding a tool the agent can call.** Define its bounded schema in `services/worker/src/eda_worker/tools/contracts.py`, add the tool in `capabilities.py`, and wire the gateway call. Tool descriptions are part of the model contract, so keep them short and precise.

**Changing a shared contract.** Edit the Pydantic model in `packages/contracts`, then run `make contracts`. Never hand-edit generated schema or TypeScript; `make check` will catch it.

**Adding an artifact format.** Add the validator in `packages/artifacts`, register a `ValidationProfile` in the sandbox and the worker tool contract, and map it in the sandbox gateway. Missing the mapping means the agent can request the profile and get nothing back.

**Touching infrastructure.** Bicep and Terraform must stay equivalent. See [terraform parity](docs/runbooks/terraform-parity.md).

## Before you open a pull request

Document migrations, public-contract changes, preview-API changes, and IaC changes in the pull request itself.

Never commit credentials, customer data, acquired proprietary source, private Office content, or unreviewed lock-file churn.

If a test only passes on your machine, say so. An honest "not verified in the cloud" is worth more than a green checkbox nobody can reproduce.
