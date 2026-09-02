from fastapi import Request

from .agent.model_factory import ModelFactory
from .api_config.service import ApiConfigService
from .credentials import CredentialRoot
from .market_data.service import MarketDataService
from .model_config.service import ModelService
from .research.attachments import AttachmentStore
from .research.runner import ResearchTaskManager
from .research.repository import ResearchRepository


def credential_root(request: Request) -> CredentialRoot:
    return request.app.state.credentials


def model_service(request: Request) -> ModelService:
    return request.app.state.models


def api_config_service(request: Request) -> ApiConfigService:
    return request.app.state.integrations


def model_factory(request: Request) -> ModelFactory:
    return request.app.state.model_factory


def market_service(request: Request) -> MarketDataService:
    return request.app.state.market


def research_manager(request: Request) -> ResearchTaskManager:
    return request.app.state.research


def attachments_store(request: Request) -> AttachmentStore:
    return request.app.state.attachments

def research_repository(request: Request) -> ResearchRepository:
    return ResearchRepository(request.app.state.storage)
