from typing import Annotated
from uuid import UUID

import json

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from ..agent.model_factory import ModelFactory
from ..dependencies import model_factory, model_service
from ..model_config.models import ChatInput, ChatResult, ProbeInput, ProbeResult, ProfileInput, ProfileView
from ..model_config.providers import PROVIDERS
from ..model_config.service import ModelService

router = APIRouter(prefix="/api/models", tags=["models"])
Service = Annotated[ModelService, Depends(model_service)]
Factory = Annotated[ModelFactory, Depends(model_factory)]


def _sse(events):
    async def generate():
        async for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return generate()

def _stream_response(events) -> StreamingResponse:
    return StreamingResponse(
        _sse(events), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("", response_model=list[ProfileView])
def list_profiles(service: Service):
    return service.list()


@router.post("", response_model=ProfileView, status_code=201)
def create_profile(data: ProfileInput, service: Service):
    return service.save(data)


@router.post("/test", response_model=ProbeResult)
async def test_active(data: ProbeInput, factory: Factory):
    return await factory.probe(data.mode, confirm_external=data.confirm_external)


@router.get("/providers")
def providers():
    return {"reviewed_at": "2026-08-31", "providers": PROVIDERS}


@router.post("/chat", response_model=ChatResult)
async def chat_active(data: ChatInput, factory: Factory):
    return await factory.chat(data)


@router.post("/chat/stream")
async def chat_stream_active(data: ChatInput, factory: Factory):
    # Eager checks (confirmation, busy lock, credential) raise before streaming starts.
    return _stream_response(await factory.chat_stream(data))


@router.get("/{profile_id}", response_model=ProfileView)
def get_profile(profile_id: UUID, service: Service):
    return service.view(service.repository.get(str(profile_id)))


@router.put("/{profile_id}", response_model=ProfileView)
def update_profile(profile_id: UUID, data: ProfileInput, service: Service):
    return service.save(data, str(profile_id))


@router.post("/{profile_id}/activate", response_model=ProfileView)
def activate_profile(profile_id: UUID, service: Service):
    return service.activate(str(profile_id))


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: UUID, service: Service):
    service.repository.delete(str(profile_id))
    return Response(status_code=204)


@router.post("/{profile_id}/test", response_model=ProbeResult)
async def test_profile(profile_id: UUID, data: ProbeInput, factory: Factory):
    return await factory.probe(data.mode, str(profile_id), confirm_external=data.confirm_external)


@router.post("/{profile_id}/chat", response_model=ChatResult)
async def chat_profile(profile_id: UUID, data: ChatInput, factory: Factory):
    return await factory.chat(data, str(profile_id))


@router.post("/{profile_id}/chat/stream")
async def chat_stream_profile(profile_id: UUID, data: ChatInput, factory: Factory):
    return _stream_response(await factory.chat_stream(data, str(profile_id)))
