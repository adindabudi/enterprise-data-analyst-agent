# Deployer permissions

Provisioning fails late and confusingly when permissions are short, so check these before your first `azd up`.

## What you need

| Scope        | Permission                                                       | Why                                                                                                                                                                                        |
| ------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Subscription | **Owner**, or **Contributor** plus **User Access Administrator** | The templates create resources _and_ assign managed identities to them. Contributor alone can create the resources but cannot grant the role assignments, so the deployment stops partway. |
| Entra tenant | Microsoft Graph **`Application.ReadWrite.All`**                  | `scripts/ensure-entra-app.sh` registers the sign-in application and its app roles. Without this the preprovision hook stops and tells you to grant it.                                     |
| Subscription | Read access to role assignments                                  | The preflight lists your own assignments to confirm it can grant identities later. This comes with Owner or User Access Administrator.                                                     |
| Subscription | Register resource providers                                      | Nine namespaces must be `Registered`. See the loop in the README. Contributor can do this.                                                                                                 |

Least privilege here is Contributor + User Access Administrator + `Application.ReadWrite.All`. Owner is simpler and does the same thing.

## Bring your own app registration

If your tenant does not let you create applications, have an administrator register one and pass its client ID:

```sh
azd env set EDA_ENTRA_APP_CLIENT_ID <client-id>
```

The preflight then verifies that app exists instead of creating one. You still need read access to Entra applications.

## What the deployment grants, and to whom

The templates create user-assigned managed identities and grant them data-plane roles. Nobody gets a shared key or connection string.

| Identity                        | Gets access to                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------- |
| API                             | Cosmos DB, Blob Storage, Redis, the Foundry model (Cognitive Services User), ACA Sandbox execution |
| Cleanup and acceptance job UAMI | Cosmos DB, Blob Storage, Redis, Container Registry, selected Fabric resources                      |

The exact role definition IDs are in `scripts/rbac-assignments.json` and mirrored in `infra/bicep/modules/` and `infra/terraform/modules/`.

## Fabric needs more

Enabling a Fabric pack adds a second tenant and a second application. That app is registered in the Fabric tenant, uses a certificate rather than a secret, and needs delegated Power BI permissions with admin consent.

Full sequence: [fabric-iq.md](../runbooks/fabric-iq.md) for the semantic model, [fabric-ontology-lab.md](../runbooks/fabric-ontology-lab.md) for the ontology.

## Common failures

**`Manual action required: grant Microsoft Graph Application.ReadWrite.All`** — the deployer cannot register applications. Grant it, or use `EDA_ENTRA_APP_CLIENT_ID` above.

**`the deployer must be able to inspect subscription role assignments`** — you are Contributor without User Access Administrator.

**`provider <name> is not registered`** — run the provider registration loop, wait for it to finish, then rerun.

**A role assignment fails midway through provisioning** — the same missing User Access Administrator, found later. Grant it and rerun `azd provision`; the deployment is idempotent.
