from eda_runtime_state.tasks import RuntimeStateRepository
from fastapi import Request

from eda_api.auth.msal_client import MsalAuthClient
from eda_api.auth.repository import AuthRepository
from eda_api.config import Settings
from eda_api.fabric_auth.service import FabricAuthCoordinator
from eda_api.storage.artifacts import ArtifactCatalog
from eda_api.storage.history import SessionHistoryReader
from eda_api.storage.todos import SessionTodoReader
from eda_api.storage.uploads import UploadService
from eda_api.storage.workspace import WorkspaceRepository
from eda_api.task_service import TaskService


def settings(request: Request) -> Settings:
    return request.app.state.settings


def auth_repository(request: Request) -> AuthRepository:
    return request.app.state.auth_repository


def msal_client(request: Request) -> MsalAuthClient:
    return request.app.state.msal_client


def workspace_repository(request: Request) -> WorkspaceRepository:
    return request.app.state.workspace_repository


def upload_service(request: Request) -> UploadService:
    return request.app.state.upload_service


def artifact_catalog(request: Request) -> ArtifactCatalog | None:
    return request.app.state.artifact_catalog


def session_todo_reader(request: Request) -> SessionTodoReader | None:
    return request.app.state.session_todo_reader


def session_history_reader(request: Request) -> SessionHistoryReader | None:
    return request.app.state.session_history_reader


def runtime_repository(request: Request) -> RuntimeStateRepository:
    return request.app.state.runtime_repository


def task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def fabric_auth_service(request: Request) -> FabricAuthCoordinator | None:
    return request.app.state.fabric_auth_service
