from __future__ import annotations

# Microsoft documents this delegated pair for an ontology BYO Entra app. A
# 2026-07-28 probe saw it return 401 at MCP initialize where Item.ReadWrite.All
# passed, so check the grant and admin consent first if discovery fails.
ONTOLOGY_BYO_SCOPES = (
    "https://analysis.windows.net/powerbi/api/Item.Read.All",
    "https://analysis.windows.net/powerbi/api/Item.Execute.All",
)

# This audience belongs only to an isolated diagnostic reference probe. It is
# never a production BFF token scope and cannot satisfy ontology readiness.
ONTOLOGY_DIRECT_REFERENCE_SCOPE = "https://api.fabric.microsoft.com/.default"
