# Upload Quarantine Runbook

Load the deployed environment without printing credentials:

```sh
eval "$(azd env get-values)"
RESOURCE_GROUP="$AZURE_RESOURCE_GROUP"
```

Inspect Defender-related storage diagnostics in Azure Monitor using the deployed Log Analytics workspace and the upload correlation ID. Classify the outcome as pending, `Malicious`, `Error`, or `Not scanned`; only `No threats found` is promotable.

For a malicious result, verify Defender soft delete and do not copy or download the quarantine blob. For `Error` or `Not scanned`, use the approved on-demand rescan or re-upload procedure; do not change scan metadata or manually promote the blob.

To inspect resource configuration, use managed identity and Azure resource queries:

```sh
az storage account show --resource-group "$RESOURCE_GROUP" --name "$EDA_STORAGE_ACCOUNT_NAME" --query "{defender:properties.azureDefenderForStorage,softDelete:properties.deleteRetentionPolicy}"
```

Delete abandoned uploads only through the cleanup job or the owner-partition cleanup command. Do not use account keys, browser SAS URLs, or ad hoc cross-owner prefix deletion.
