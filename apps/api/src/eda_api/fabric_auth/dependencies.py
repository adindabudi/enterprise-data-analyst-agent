from __future__ import annotations

from typing import Annotated

from eda_fabric_auth import FabricProvider
from fastapi import Depends, HTTPException, Request, status

from eda_api.config import Settings
from eda_api.dependencies import settings
from eda_api.readiness.models import FabricPackStatus

from .capacity import CapacityStatus
from .service import FabricAuthCoordinator


def fabric_provider(config: Annotated[Settings, Depends(settings)]) -> FabricProvider:
    if not config.fabric_enabled or config.fabric_provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return config.fabric_provider


def fabric_auth_service(request: Request, provider: FabricProviderDep) -> FabricAuthCoordinator:
    del provider
    service = getattr(request.app.state, "fabric_auth_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return service


def fabric_available(request: Request) -> None:
    if getattr(request.app.state, "fabric_readiness", None) not in {
        FabricPackStatus.CONFIGURED,
        FabricPackStatus.READY,
    }:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def fabric_chat_query(request: Request) -> bool:
    return getattr(request.app.state, "fabric_chat_query", False) is True


def fabric_capacity(request: Request) -> CapacityStatus | None:
    capacity = getattr(request.app.state, "fabric_capacity", None)
    return capacity if isinstance(capacity, CapacityStatus) else None


FabricProviderDep = Annotated[FabricProvider, Depends(fabric_provider)]
FabricAuthServiceDep = Annotated[FabricAuthCoordinator, Depends(fabric_auth_service)]
FabricAvailableDep = Annotated[None, Depends(fabric_available)]
FabricChatQueryDep = Annotated[bool, Depends(fabric_chat_query)]
FabricCapacityDep = Annotated[CapacityStatus | None, Depends(fabric_capacity)]
