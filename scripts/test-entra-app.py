from __future__ import annotations

import json
import subprocess
from typing import NoReturn, cast
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


def fail(message: str) -> NoReturn:
    raise SystemExit(f"FAIL: {message}")


def command(*arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603 -- callers provide fixed Azure CLI commands and validated IDs.
        arguments,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        fail(f"command failed: {' '.join(arguments[:4])}")
    return completed.stdout


def json_command(*arguments: str) -> object:
    try:
        return cast(object, json.loads(command(*arguments)))
    except json.JSONDecodeError as error:
        fail(f"command did not return JSON: {error.msg}")


def require_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        fail(f"{name} must be an object")
    return cast(dict[str, object], value)


def require_object_list(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        fail(f"{name} must be a list")
    items = cast(list[object], value)
    return [require_object(item, f"{name} entry") for item in items]


def azd_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in command("azd", "env", "get-values").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip('"')
    return values


def require_value(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not value:
        fail(f"azd output {name} must be set")
    return value


def require_uuid(value: str, name: str) -> str:
    try:
        UUID(value)
    except ValueError:
        fail(f"{name} must be a UUID")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, *args: object, **kwargs: object) -> Request | None:
        del req, args, kwargs
        return None


def login_location(api_url: str) -> str:
    request = Request(  # noqa: S310 -- API_URL is the deployed HTTPS azd output.
        f"{api_url.rstrip('/')}/api/auth/login", method="GET"
    )
    try:
        response = build_opener(NoRedirect()).open(request, timeout=15)
    except HTTPError as error:
        if error.code != 307:
            fail(f"/api/auth/login returned HTTP {error.code}")
        location = error.headers.get("Location")
    else:
        fail(f"/api/auth/login returned HTTP {response.status}, expected 307")
    if not location:
        fail("/api/auth/login did not return an authorization location")
    return location


def main() -> None:
    values = azd_values()
    tenant_id = require_uuid(require_value(values, "ENTRA_TENANT_ID"), "ENTRA_TENANT_ID")
    client_id = require_uuid(require_value(values, "ENTRA_CLIENT_ID"), "ENTRA_CLIENT_ID")
    api_url = require_value(values, "API_URL")
    web_identity_id = require_value(values, "WEB_IDENTITY_ID")
    api_app_id = require_value(values, "API_APP_ID")

    active_tenant = command(
        "az", "account", "show", "--query", "tenantId", "--output", "tsv", "--only-show-errors"
    ).strip()
    if active_tenant != tenant_id:
        fail("active Azure tenant does not match ENTRA_TENANT_ID")

    application = require_object(
        json_command("az", "ad", "app", "show", "--id", client_id, "--only-show-errors"),
        "application metadata",
    )
    if application.get("signInAudience") != "AzureADMyOrg":
        fail("application is not single-tenant")
    if application.get("passwordCredentials") or application.get("keyCredentials"):
        fail("application contains forbidden credentials")
    redirect_uri = f"{api_url.rstrip('/')}/api/auth/callback"
    web = require_object(application.get("web"), "application web configuration")
    redirect_uris = web.get("redirectUris")
    if redirect_uris != [redirect_uri]:
        fail("application must contain exactly the expected web redirect URI")

    web_principal_id = command(
        "az",
        "identity",
        "show",
        "--ids",
        web_identity_id,
        "--query",
        "principalId",
        "--output",
        "tsv",
        "--only-show-errors",
    ).strip()
    require_uuid(web_principal_id, "web identity principal ID")
    app_object_id = application.get("id")
    if not isinstance(app_object_id, str):
        fail("application object ID is missing")
    credentials = require_object_list(
        json_command("az", "ad", "app", "federated-credential", "list", "--id", app_object_id, "--only-show-errors"),
        "federated credentials",
    )
    expected_name = f"eda-web-{require_value(values, 'AZURE_ENV_NAME')}"
    matches = [credential for credential in credentials if credential.get("name") == expected_name]
    expected_fic = {
        "issuer": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        "subject": web_principal_id,
        "audiences": ["api://AzureADTokenExchange"],
    }
    if len(matches) != 1 or any(matches[0].get(field) != value for field, value in expected_fic.items()):
        fail("web federated credential does not exactly match the web UAMI")

    app_environment = require_object_list(
        json_command(
            "az",
            "containerapp",
            "show",
            "--ids",
            api_app_id,
            "--query",
            "properties.template.containers[0].env",
            "--only-show-errors",
        ),
        "API container environment",
    )
    environment = {
        name: value
        for item in app_environment
        if isinstance(name := item.get("name"), str) and isinstance(value := item.get("value"), str)
    }
    if environment.get("EDA_ENTRA_CLIENT_ID") != client_id or environment.get("EDA_ENTRA_TENANT_ID") != tenant_id:
        fail("API container Entra client or tenant configuration does not match the application")

    location = login_location(api_url)
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.netloc != "login.microsoftonline.com":
        fail("/api/auth/login did not redirect to Microsoft Entra")
    if not query.get("code_challenge") or query.get("response_mode") != ["form_post"]:
        fail("/api/auth/login did not request PKCE with form_post")
    print("PASS: Entra application, federation, container environment, and PKCE login redirect verified.")


if __name__ == "__main__":
    main()
