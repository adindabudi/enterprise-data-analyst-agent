from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, Form, Response, status
from fastapi.responses import RedirectResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from eda_api.auth.dependencies import CsrfPrincipalDep, CurrentPrincipalDep
from eda_api.auth.msal_client import AuthenticationError
from eda_api.config import Settings
from eda_api.dependencies import settings

from .capacity import CapacityState
from .dependencies import (
    FabricAuthServiceDep,
    FabricAvailableDep,
    FabricCapacityDep,
    FabricChatQueryDep,
    FabricProviderDep,
)

router = APIRouter(prefix="/api/fabric/auth", tags=["fabric-auth"])
source_router = APIRouter(prefix="/api/fabric/source", tags=["fabric-source"])


class FabricAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    task_id: str | None = Field(default=None, alias="taskId", pattern=r"^task_[A-Za-z0-9_-]{8,}$")


class FabricAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_url: AnyHttpUrl = Field(serialization_alias="authorizationUrl")


class FabricSourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alias: str
    description: str


class FabricAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["ontology"]
    state: Literal["unlinked", "linked", "reauth_required"]
    chat_query: bool = Field(serialization_alias="chatQuery")
    source: FabricSourceMetadata | None = None


class FabricSourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capacity: CapacityState


@router.post("/start", response_model=FabricAuthStartResponse)
async def start(
    request: FabricAuthStartRequest,
    response: Response,
    principal: CsrfPrincipalDep,
    service: FabricAuthServiceDep,
    available: FabricAvailableDep,
) -> FabricAuthStartResponse:
    del available
    authorization_url, correlation_secret = await service.start(principal=principal, task_id=request.task_id)
    response.set_cookie(
        "eda_fabric_link",
        correlation_secret,
        max_age=600,
        secure=True,
        httponly=True,
        samesite="none",
        path="/api/fabric/auth/callback",
    )
    return FabricAuthStartResponse.model_validate({"authorization_url": authorization_url})


@router.post("/callback", response_class=RedirectResponse, status_code=status.HTTP_303_SEE_OTHER)
async def callback(
    service: FabricAuthServiceDep,
    state_value: Annotated[str, Form(alias="state")],
    code: Annotated[str | None, Form()] = None,
    error: Annotated[str | None, Form()] = None,
    correlation_secret: Annotated[str | None, Cookie(alias="eda_fabric_link")] = None,
) -> RedirectResponse:
    if not correlation_secret:
        raise AuthenticationError("Fabric authorization is required.")
    receipt = await service.callback(
        state_value=state_value,
        correlation_secret=correlation_secret,
        code=code,
        error=error,
    )
    response = RedirectResponse("/fabric-auth/complete", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("eda_fabric_link", path="/api/fabric/auth/callback")
    response.set_cookie(
        "eda_fabric_complete",
        receipt,
        max_age=300,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/api/fabric/auth/complete",
    )
    return response


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete(
    response: Response,
    principal: CsrfPrincipalDep,
    service: FabricAuthServiceDep,
    receipt: Annotated[str | None, Cookie(alias="eda_fabric_complete")] = None,
) -> None:
    response.delete_cookie("eda_fabric_complete", path="/api/fabric/auth/complete")
    if not receipt:
        raise AuthenticationError("Fabric authorization is required.")
    await service.complete(principal=principal, receipt=receipt)


@router.get("/status", response_model=FabricAuthStatus, response_model_exclude_none=True)
async def status_view(
    principal: CurrentPrincipalDep,
    service: FabricAuthServiceDep,
    provider: FabricProviderDep,
    chat_query: FabricChatQueryDep,
    config: Annotated[Settings, Depends(settings)],
) -> FabricAuthStatus:
    source = None
    if config.fabric_ontologies:
        alias, target = next(iter(config.fabric_ontologies.items()))
        source = FabricSourceMetadata(alias=alias, description=target.description)
    return FabricAuthStatus(
        provider=provider.value,
        state=await service.status(principal=principal),
        chat_query=chat_query,
        source=source,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(
    principal: CsrfPrincipalDep,
    service: FabricAuthServiceDep,
) -> None:
    await service.unlink(principal=principal)


@source_router.get("/status", response_model=FabricSourceStatus)
async def source_status(
    principal: CurrentPrincipalDep,
    service: FabricAuthServiceDep,
    capacity: FabricCapacityDep,
) -> FabricSourceStatus:
    """Whether the linked source can run queries right now, which the link alone cannot tell."""
    # Only a linked owner's own grant may probe, and an unlinked caller learns nothing about the source.
    if capacity is None or await service.status(principal=principal) != "linked":
        return FabricSourceStatus(capacity=CapacityState.UNKNOWN)
    return FabricSourceStatus(capacity=await capacity.for_owner(principal.tenant_id, principal.owner_object_id))
