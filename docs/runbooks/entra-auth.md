# Entra BFF Authentication

The production BFF uses a single-tenant Entra application with the `Branding.Admin` user app role and the web user-assigned managed identity as its federated credential. It does not use client passwords, certificates, access keys, browser token storage, or unattended interactive sign-in.

## Prerequisites

Run `scripts/ensure-entra-app.sh` while signed into the target tenant. The deployer needs Microsoft Graph permission to read and update application registrations. Without it, the script exits with the minimum manual action: grant `Application.ReadWrite.All` to the deployer, then rerun it. To use an existing registration, set the non-secret `EDA_ENTRA_APP_CLIENT_ID`; it must be an application in the active tenant with `AzureADMyOrg` sign-in audience and no password or certificate credentials.

The script sets `ENTRA_CLIENT_ID` and `ENTRA_TENANT_ID` in the selected azd environment. It also sets `AZURE_ENTRA_CLIENT_ID` because the current Bicep parameter contract consumes that compatibility name. It creates or retains the immutable `Branding.Admin` role ID `cc6305dc-7f9b-4f48-9d87-08e1c83f8e72`.

After provisioning, run `scripts/configure-entra-federation.sh`. It configures exactly this web redirect URI:

```text
https://<application-host>/api/auth/callback
```

It also creates the named credential `eda-web-<environment>` with the web UAMI principal ID as its subject and `api://AzureADTokenExchange` as its sole audience. The script removes its temporary JSON configuration with an exit trap.

## Common Failures

### Graph permissions or role assignment

If application create/update fails, grant the deployer Microsoft Graph `Application.ReadWrite.All` and obtain tenant admin consent when policy requires it. Assign `Branding.Admin` after deployment through **Enterprise applications** > the EDA application > **Users and groups**. Do not change the role UUID or use a directory role as a substitute.

### Redirect mismatch

Compare the deployed `API_URL` with the application’s one web redirect URI. It must be the public HTTPS host plus `/api/auth/callback`, with no extra path, port, or trailing slash. Rerun `scripts/configure-entra-federation.sh` after an application hostname change.

### AADSTS70021 subject mismatch

This means the federated credential subject does not equal the deployed web UAMI principal ID. Retrieve the identity principal ID, compare it to the credential named `eda-web-<environment>`, and rerun the federation script. Do not create an additional credential with a guessed subject. Entra federation changes can take several minutes to propagate; wait and retry the login flow before changing configuration again.

### Cookie or origin failure

The browser must use the application’s public HTTPS origin. Verify the API container has matching `EDA_ENTRA_CLIENT_ID` and `EDA_ENTRA_TENANT_ID`, then call `/api/auth/login` and confirm it redirects to Microsoft Entra with a PKCE `code_challenge` and `response_mode=form_post`. A callback must be a form POST; the runbook does not automate a human login. Check browser cookie policy only after origin and redirect URI match.

## Rotation And Removal

There is no client password or certificate to rotate. Rotate the web identity only through an approved identity replacement: configure the replacement principal in the existing named credential, wait for propagation, validate login, then delete the retired identity.

For environment teardown, delete the Container Apps and their web UAMI first, then delete the named federated credential and finally the dedicated application registration. Never delete a shared application registration solely because one environment is being removed.
