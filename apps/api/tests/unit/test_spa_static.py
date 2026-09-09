from pathlib import Path

from eda_api.main import _frontend_dist
from httpx import AsyncClient


def test_module_app_uses_frontend_dist_environment(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EDA_FRONTEND_DIST", str(tmp_path))

    assert _frontend_dist(None) == tmp_path


async def test_unknown_api_path_remains_a_json_not_found(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/unknown")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


async def test_client_side_path_uses_the_spa_index(api_client: AsyncClient) -> None:
    response = await api_client.get("/analysis/ses_123", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in response.text
