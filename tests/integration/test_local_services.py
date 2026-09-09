from __future__ import annotations

import os

import httpx
import pytest


@pytest.mark.integration
@pytest.mark.parametrize(
    ("environment_variable", "default_url", "expected_status"),
    [
        ("AZURITE_HEALTH_URL", "http://127.0.0.1:10000/devstoreaccount1", None),
        ("REDIS_HEALTH_URL", "http://127.0.0.1:18079/health", 200),
    ],
)
async def test_local_service_is_http_reachable(
    environment_variable: str,
    default_url: str,
    expected_status: int | None,
) -> None:
    url = os.environ.get(environment_variable, default_url)

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)

    if expected_status is None:
        assert response.status_code < 500, f"{url} returned {response.status_code}"
    else:
        assert response.status_code == expected_status
