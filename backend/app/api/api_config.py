from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from ..api_config.catalog import INTEGRATIONS, REVIEWED_AT
from ..api_config.models import (
    ApiProfileInput, ApiProfileView, ApiTestInput, ApiTestView, ImportEnvironmentInput, ImportedView,
)
from ..api_config.service import ApiConfigService
from ..dependencies import api_config_service

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
Service = Annotated[ApiConfigService, Depends(api_config_service)]


@router.get("/providers")
def providers(service: Service):
    return {"reviewed_at": REVIEWED_AT, "environment": service.environment(), "providers": INTEGRATIONS}


@router.get("/profiles", response_model=list[ApiProfileView])
def list_profiles(service: Service):
    return service.list()


@router.post("/profiles", response_model=ApiProfileView, status_code=201)
def create_profile(data: ApiProfileInput, service: Service):
    return service.save(data)


@router.put("/profiles/{profile_id}", response_model=ApiProfileView)
def update_profile(profile_id: UUID, data: ApiProfileInput, service: Service):
    return service.save(data, str(profile_id))


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: UUID, service: Service,
                   credential_version: int | None = Query(default=None, ge=1)):
    service.delete(str(profile_id), credential_version)
    return Response(status_code=204)


@router.post("/profiles/{profile_id}/test", response_model=ApiTestView)
async def test_profile(profile_id: UUID, data: ApiTestInput, service: Service):
    return await service.test(str(profile_id), data)


@router.post("/import-environment", response_model=ImportedView)
def import_environment(data: ImportEnvironmentInput, service: Service):
    return service.import_environment(data.service)
