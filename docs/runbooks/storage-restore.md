# Storage Restore

## Cosmos PITR

1. Pause writes and record the intended recovery timestamp in UTC.
2. Use Cosmos continuous backup point-in-time restore (PITR) into a new account; never overwrite the active account.
3. Validate container counts, partition access, and a synthetic task before changing application configuration.
4. Cut over through a reviewed deployment, then retain the original account until the recovery is accepted.

## Blob soft delete and version recovery

1. Identify the storage account, container, object version, and intended timestamp from operational metadata.
2. Restore the deleted Blob or promote the selected Blob version with the Azure portal or CLI recovery operation.
3. Verify object metadata and application access with a synthetic artifact only.
4. Document the recovery scope and investigate the originating deletion before closing the incident.

Defender scanning and versioning remain enabled during recovery; do not bypass quarantine controls.
