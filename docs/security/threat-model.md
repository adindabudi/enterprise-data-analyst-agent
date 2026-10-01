# Storage Threat Model

## Trust Boundaries

| Boundary        | Protected data                  | Permitted identity               | Denied flow                                  | Verification                    |
| --------------- | ------------------------------- | -------------------------------- | -------------------------------------------- | ------------------------------- |
| Browser         | Opaque session and CSRF cookies | Same-origin BFF                  | Bearer tokens, OBO, browser storage tokens   | Auth/CSRF tests                 |
| API             | Principal and request content   | Web managed identity             | Caller-supplied tenant or owner IDs          | Session route tests             |
| Cosmos          | Tenant/owner/session records    | API and worker data-plane roles  | Cross-owner point reads and unscoped queries | HPK cloud contract              |
| Blob quarantine | Untrusted uploads               | API upload identity and Defender | Browser SAS, promotion before clean scan     | Azurite and Defender acceptance |
| Worker          | Task and artifact state         | Worker managed identity          | Web-session token reuse                      | Runtime tests                   |
| Foundry         | Prompts and tool calls          | Web managed identity             | Browser direct model access                  | Harness gate                    |
| Fabric          | Optional ontology access        | Fabric pack identity             | Core web token delegation                    | Fabric auth tests               |
| Sandbox         | Generated code and files        | Session pool identity            | Shared host or production network access     | Session-pool acceptance         |

Opaque IDs are locators, not authorization. Every API and Cosmos operation derives the hierarchical partition key from the validated principal.

Defender scan tags are not trusted on their own. Quarantine isolation, least-privilege RBAC, soft-delete remediation, audit telemetry, and the deployed EICAR acceptance test are required in addition to the exact clean-result allow rule.
