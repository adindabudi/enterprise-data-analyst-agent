# Fabric IQ Semantic-Model Pack

## Readiness Rule

The semantic-model pack remains `disabled`, `failed`, or `configured` until `make acceptance-fabric` completes against two distinct tenants. A same-tenant probe, provider documentation, app registration, successful MCP discovery, or local test run is not readiness evidence.

The only supported provider profile is direct Fabric IQ Power BI data exploration at the endpoint and routing variant pinned in code. Do not configure a Foundry MCP connection, Fabric data agent, XMLA client, service-principal data identity, arbitrary MCP URL, or fallback model.

## Required Topology

Use product/Foundry tenant F and Fabric tenant D, with F different from D. Prepare:

- one dedicated single-tenant Fabric OAuth application in D;
- one nonproduction semantic model in D;
- product users A, B, and C represented as B2B guests in D;
- Build permission plus distinct RLS control totals for A and B;
- product access but no Build permission for C;
- a disposable administrator allowed to revoke sign-in sessions for only A, B, and C;
- isolated mode-0700 Azure CLI contexts for F and D;
- mode-0600 browser/fixture artifacts under `.artifacts/private/fabric/`.

Never place passwords, browser storage state, tokens, authorization codes, cookies, private keys, or serialized MSAL caches in azd values, environment dumps, command arguments, logs, screenshots, traces, or reports.

## Configure

Set nonsecret values in the selected azd environment:

```text
FABRIC_ENABLED=true
FABRIC_PROVIDER=semantic_model
FABRIC_TENANT_ID=<tenant D>
FABRIC_SEMANTIC_MODELS_JSON=<alias catalog>
FABRIC_ACCEPTANCE_PRINCIPAL_ID=<principal in tenant F>
FABRIC_AZURE_CONFIG_DIR=<mode-0700 tenant-D context>
PRODUCT_AZURE_CONFIG_DIR=<mode-0700 tenant-F context>
```

Run the phases in order:

```bash
./scripts/configure-fabric-entra.sh bootstrap
./scripts/doctor-fabric.sh --phase predeploy
azd provision
./scripts/configure-fabric-entra.sh finalize
./scripts/doctor-fabric.sh --phase postdeploy
```

Bootstrap creates or reuses one credential-free application and requests only delegated `Item.Read.All` and `Item.Execute.All`. Finalize exports only the public Key Vault certificate under context F and uploads it under context D. Grant tenant-wide admin consent after finalize. No client secret is supported.

Provider discovery and publication use separate identities and contexts:

```bash
uv run python scripts/check-fabric-contract.py --output .artifacts/fabric-provider-contract.json
uv run python scripts/publish-fabric-contract.py
```

Discovery performs only MCP initialize and tools/list. Publication writes one immutable full contract and sets the mutable feature record to `configured`; it cannot write `ready`.

## User Link Flow

A blocked task emits only `/api/fabric/auth/start`. The user explicitly selects **Connect to Fabric**. The BFF callback stores an encrypted pending grant and an HttpOnly one-time completion cookie. The same-origin completion POST promotes the owner-bound grant before the next bounded Hosted Workflow phase.

If callback state, browser correlation, owner session, task checkpoint, tenant, or receipt does not match, restart the link flow. Do not move callback URLs, receipts, or browser cookies between browsers or users.

## Acceptance

Prepare the private fixture and external/browser observation artifact with mode 0600, then run:

```bash
make acceptance-fabric
```

The gate requires all of the following in one run bound to the active deployment, provider contract, Terra profile, served model/snapshot, prompt hash, and request-options hash:

- distinct product and Fabric tenant hashes;
- exact six-tool provider contract and three-tool runtime allowlist;
- delegated link through the production BFF;
- ordinary silent refresh;
- revoked-session refresh returning `reauth_required` without local unlink;
- unlink, relink, and one Hosted Workflow resume;
- distinct expected RLS result hashes for A and B;
- nonretryable Build denial and no result artifact for C;
- forced-first and automatic `query_fabric` routing;
- reconciled query/DAX/result/provenance hashes;
- zero credential markers across logs, traces, Redis, Foundry response inputs, Blob, browser, sandbox, and non-grant Cosmos documents;
- no provider, transport, identity, endpoint, or model fallback.

The acceptance principal may wrap the input DEK but cannot unwrap it or sign assertions. The worker decrypts the TTL input once. The finalizer verifies all local and Cosmos evidence, then performs one ETag transition from `configured` to `ready`.

## Diagnosis

- **401 or `reauth_required`**: relink the owner. If revocation was intentional, verify the task returned to `blocked_auth` before relinking.
- **403 / `build_permission_denied`**: correct item access, license, Build permission, or B2B membership. Do not retry or substitute a value.
- **RLS mismatch**: stop. Verify guest mapping, role membership, model refresh, and expected control hashes.
- **Invalid DAX**: one structured correction is permitted. A second failure is terminal.
- **429 or 5xx**: at most three bounded transport attempts are allowed.
- **Schema drift**: rediscover and republish the provider contract, reset to `configured`, and rerun full acceptance.
- **Timeout or oversized output**: reduce the source query; do not retry through another data path.
- **Callback failure**: verify HTTPS origin, exact redirect URI, correlation cookie, configured tenant, and certificate credential.

## Rotation

For certificate rotation, create a new Key Vault certificate version, upload only its public certificate to the Fabric app, validate both credentials during overlap, restart API/worker revisions, relink a test owner, then remove the old public credential. Never export the private key.

For cache-wrap rotation, create a new key version, deploy writers using the new key, retain unwrap permission for existing envelopes during the bounded migration, then remove the old version only after all grants expire or are re-encrypted. A deployment/profile/prompt/options change resets readiness to `configured` and requires full acceptance.

## Disable

Set `FABRIC_ENABLED=false` and provision. Core must remain healthy with four tools, no Fabric auth infrastructure, no Fabric UI action, and no acceptance Job execution. Keep historical immutable evidence for audit retention; do not copy it to another deployment or model profile.
