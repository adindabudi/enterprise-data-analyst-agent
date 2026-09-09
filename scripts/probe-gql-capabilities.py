"""What does the ontology's graph actually accept?

The Learn articles for Fabric graph document OPTIONAL MATCH, inline WHERE, LET,
CASE, coalesce and variable-length patterns. The tool description we ship claims
FILTER-not-WHERE and no OPTIONAL MATCH, both concluded from single failed tries.
This runs every documented construct against the real ontology graph so the
description states what the endpoint does, not what one bad attempt suggested.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any, cast

import httpx

WORKSPACE = "b82afbde-8304-44c0-ac94-3cf69f6da909"
GRAPH_MODEL = "bdf1d01e-7da8-4b58-873a-54d8edb12f34"
EXECUTE = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/GraphModels/{GRAPH_MODEL}/executeQuery?preview=true"
)

ICU = "'Intensive Care Unit'"
CASES: list[tuple[str, str]] = [
    (
        "inline WHERE on a node",
        f"MATCH (d:departments WHERE d.DepartmentName = {ICU}) RETURN count(*) AS n",
    ),
    (
        "inline WHERE across a hop",
        f"MATCH (r:rooms)-[:rooms_has_departments]->(d:departments WHERE d.DepartmentName = {ICU}) "
        "RETURN count(*) AS n",
    ),
    (
        "statement FILTER (what we ship today)",
        f"MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) FILTER d.DepartmentName = {ICU} "
        "RETURN count(*) AS n",
    ),
    (
        "OPTIONAL MATCH keeps unmatched rows",
        "MATCH (r:rooms) OPTIONAL MATCH (p:patients)-[:patients_has_rooms]->(r) RETURN count(*) AS rows_kept",
    ),
    (
        "OPTIONAL MATCH + IS NULL (the occupancy question in ONE query)",
        f"MATCH (r:rooms)-[:rooms_has_departments]->(d:departments WHERE d.DepartmentName = {ICU}) "
        "OPTIONAL MATCH (p:patients)-[:patients_has_rooms]->(r) "
        "LET dept = d.DepartmentId "
        "RETURN dept, count(*) AS total_rooms, count(p) AS occupied GROUP BY dept ORDER BY dept",
    ),
    (
        "GROUP BY a RETURN alias",
        "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) RETURN d.DepartmentId AS id, "
        "count(*) AS c GROUP BY id ORDER BY id LIMIT 3",
    ),
    (
        "GROUP BY a property expression (must FAIL)",
        "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) RETURN d.DepartmentId AS id, "
        "count(*) AS c GROUP BY d.DepartmentId ORDER BY id LIMIT 3",
    ),
    (
        "LET then GROUP BY the alias",
        "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) LET name = d.DepartmentName "
        "RETURN name, count(*) AS n GROUP BY name ORDER BY n DESC LIMIT 3",
    ),
    (
        "count(DISTINCT x)",
        "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) RETURN count(DISTINCT d) AS depts",
    ),
    (
        "FILTER after GROUP BY (SQL HAVING)",
        "MATCH (r:rooms)-[:rooms_has_departments]->(d:departments) LET name = d.DepartmentName "
        "RETURN name, count(*) AS n GROUP BY name FILTER n > 20 ORDER BY n DESC",
    ),
    (
        "CASE WHEN",
        "MATCH (d:departments) RETURN d.DepartmentName, "
        f"CASE WHEN d.DepartmentName = {ICU} THEN 'critical' ELSE 'other' END AS kind LIMIT 3",
    ),
    (
        "coalesce",
        "MATCH (d:departments) RETURN coalesce(d.DepartmentName, 'unknown') AS name LIMIT 2",
    ),
    (
        "IS NOT NULL",
        "MATCH (d:departments) FILTER d.DepartmentName IS NOT NULL RETURN count(*) AS n",
    ),
    (
        "string concat ||",
        "MATCH (d:departments) RETURN d.DepartmentName || ' unit' AS label_text LIMIT 2",
    ),
    (
        "variable-length hop {1,2}",
        "MATCH (p:patients)-[:patients_has_rooms]->{1,1}(r:rooms) RETURN count(*) AS n",
    ),
    (
        "TRAIL path mode",
        "MATCH TRAIL (p:patients)-[:patients_has_rooms]->(r:rooms) RETURN count(*) AS n",
    ),
    (
        "undirected edge",
        "MATCH (p:patients)-[:patients_has_rooms]-(r:rooms) RETURN count(*) AS n",
    ),
    (
        "ORDER BY then LIMIT",
        "MATCH (d:departments) RETURN d.DepartmentName AS name ORDER BY name LIMIT 2",
    ),
    (
        "STARTS WITH",
        "MATCH (d:departments WHERE d.DepartmentName STARTS WITH 'Intensive') RETURN count(*) AS n",
    ),
    (
        "multiple patterns joined by a shared variable",
        "MATCH (p:patients)-[:patients_has_rooms]->(r:rooms), (r)-[:rooms_has_departments]->(d:departments) "
        "RETURN count(*) AS n",
    ),
    (
        "RETURN *",
        "MATCH (d:departments) RETURN * LIMIT 1",
    ),
]


def token() -> str:
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("the Azure CLI is required")
    argv = [
        az,
        "account",
        "get-access-token",
        "--resource",
        "https://api.fabric.microsoft.com",
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


async def main() -> None:
    bearer = await asyncio.to_thread(token)
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
        for label, query in CASES:
            response = await client.post(EXECUTE, headers=headers, content=json.dumps({"query": query}).encode())
            body = cast(dict[str, Any], response.json())
            status = cast(dict[str, Any], body.get("status") or {})
            if status.get("code") == "00000":
                data = json.dumps(cast(dict[str, Any], body.get("result") or {}).get("data"))
                print(f"OK    {label}\n        {data[:220]}")
            else:
                cause = cast(dict[str, Any], status.get("cause") or {})
                description = str(cause.get("description") or "")
                reason = description.split("Error message:")[-1].strip().replace("\n", " ")
                print(f"FAIL  {label}\n        {status.get('code')} {reason[:180]}")


if __name__ == "__main__":
    asyncio.run(main())
