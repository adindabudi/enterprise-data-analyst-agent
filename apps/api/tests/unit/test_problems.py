from __future__ import annotations

import pytest
from eda_api.problems import install_problem_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_validation_errors_do_not_echo_invalid_input(client: TestClient) -> None:
    response = client.post("/api/auth/callback", data={"code": "secret-code"})

    assert response.status_code == 422
    assert "secret-code" not in response.text


def test_unknown_api_paths_use_api_problem(client: TestClient) -> None:
    response = client.get("/api/not-a-real-route")

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not found",
        "status": 404,
        "code": "not_found",
        "correlationId": response.headers["X-Correlation-ID"],
        "detail": None,
    }


def test_an_unhandled_failure_is_recorded_without_reaching_the_reader(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/api/boom")
    async def boom() -> None:
        raise RuntimeError("cipher unwrap rejected the key")

    with caplog.at_level("ERROR"), TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/boom")

    assert response.status_code == 500
    assert "cipher unwrap" not in response.text
    assert response.json()["correlationId"] == response.headers["X-Correlation-ID"]
    assert "cipher unwrap rejected the key" in caplog.text
