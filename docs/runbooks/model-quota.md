# Model Quota And Hosting

The Core Data Pack deploys exactly one `claude-opus-4-8` Anthropic-format model. For the Azure-hosted Sweden Central catalog entry, the pinned version is `2` and the deployment uses `GlobalStandard`. Do not replace it with another model, version, region, or hosting variant to get a deployment through.

Before provisioning, set the non-secret deployment metadata: `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_LOCATION`, `CLAUDE_ORGANIZATION_NAME`, `CLAUDE_COUNTRY_CODE`, `CLAUDE_INDUSTRY`, `CLAUDE_HOSTING`, and `CLAUDE_CAPACITY`. Set `CLAUDE_MARKETPLACE_TERMS_ACCEPTED=true` only after accepting the Claude offer in Azure portal: Microsoft Foundry > Model catalog > `claude-opus-4-8` > Accept offer. The current Azure CLI has no Foundry-project Marketplace acceptance command; do not use the Azure ML workspace marketplace-subscription commands for this project.

`scripts/preflight-claude.sh` uses the documented `az cognitiveservices model list --location` and `az cognitiveservices usage list --location` commands to check the exact model, version, hosting, SKU, and headroom. It also verifies that the active subscription and tenant equal the explicit target, and that the deployer has Owner before the worker Cognitive Services User assignment is created.

For `CLAUDE_HOSTING=anthropic`, prompts and outputs are processed outside Azure infrastructure. This needs explicit data-boundary approval, recorded by setting `CLAUDE_ANTHROPIC_DATA_BOUNDARY_ACKNOWLEDGED=true`; no deployment may silently change from Azure to Anthropic hosting or the reverse.

## Failure Handling

Soft-deleted Cognitive Services accounts can retain quota. Inspect deleted accounts in the target subscription and restore or purge only the account approved for this environment before retrying. Do not consume another region's quota by changing `AZURE_LOCATION` without an approved deployment change.

When the catalog has no matching model/hosting/version, treat it as a catalog mismatch. Recheck the selected region and Marketplace eligibility, accept the offer if needed, and wait for catalog propagation. Do not alter the Bicep model reference or substitute another model.

If available quota is below the requested capacity, lower `CLAUDE_CAPACITY` only when the reduced throughput has been approved and the load/evaluation gate remains valid. Otherwise request quota for the exact `AIServices.GlobalStandard.claude-opus-4-8.Azure` usage in the target region, then rerun preflight.

Deployment creation is asynchronous. On timeout, retrieve the deployment operation status in the Azure portal or with the matching ARM deployment operation before retrying. A failed or partial deployment should be rolled back by deleting only the failed model deployment and then reconciling the same pinned model/version/capacity. Keep the Foundry account and project if they contain no other product resources; do not delete them merely to work around quota.

Rollback means returning to the previously evaluated deployment configuration for the same Claude model line, with an explicit change review. It never means silently routing requests to a fallback model.
