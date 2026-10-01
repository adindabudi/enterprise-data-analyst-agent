# Model quota

The Core pack deploys one model: `gpt-5.6-terra`, version `2026-07-09`, as a `GlobalStandard` deployment in `AZURE_LOCATION`. Do not swap the model, version, region, or SKU to get a deployment through; the prompt, model contract, and evaluation evidence are bound to this exact model.

Set `FOUNDRY_MODEL_CAPACITY` before provisioning. `scripts/preflight-model.sh` runs in the preprovision hook and checks three things in the target region with the documented `az cognitiveservices model list` and `az cognitiveservices usage list` commands:

- the live catalog lists the pinned model, version, and SKU with Responses support;
- the quota usage the catalog names for it has at least `FOUNDRY_MODEL_CAPACITY` of headroom;
- the deployer can create the deployment and its role assignments.

## Failure handling

Soft-deleted Cognitive Services accounts can hold quota. Inspect deleted accounts in the target subscription, and restore or purge only the one that belongs to this environment before retrying. Do not move to another region's quota by changing `AZURE_LOCATION` without an approved deployment change.

If the catalog does not list the pinned model in your region, treat it as a catalog mismatch: confirm the region offers it, wait for propagation, and do not edit the model reference in Bicep or Terraform.

If headroom is below `FOUNDRY_MODEL_CAPACITY`, lower the capacity only when the reduced throughput has been approved. Otherwise request quota for the usage the preflight names, then rerun it.

Deployment creation is asynchronous. On a timeout, read the deployment operation status before retrying. Roll back a failed deployment by deleting only that model deployment and reconciling the same pinned model, version, and capacity. Keep the Foundry account and project.
