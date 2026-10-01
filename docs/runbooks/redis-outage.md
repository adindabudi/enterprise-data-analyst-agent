# Redis Outage

## Detect and contain

1. Acknowledge the Redis circuit-open alert and declare the service degraded.
2. Confirm API health without querying user material. Stop new background dispatch when the circuit remains open.
3. Inspect Managed Redis availability, connection, and throttling metrics. Do not enable access keys as a workaround.

## Recover

1. Restore normal dispatch only after the circuit closes and a short synthetic task completes.
2. Let the supervisor recover canonical tasks from the execution ledger rather than replaying client requests.
3. Record the outage window, affected opaque task IDs, recovery time, and corrective action.

There is no regional HA claim for the demo profile. Escalate a sustained platform outage according to the Azure support process.
