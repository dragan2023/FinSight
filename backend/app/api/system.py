from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "app": "FinSight", "version": "0.1.0"}


@router.get("/api/system/session")
def session(request: Request):
    return {"token": request.app.state.session_token}


@router.get("/api/system/status")
def status(request: Request):
    settings = request.app.state.settings
    models = request.app.state.models
    integrations = request.app.state.integrations
    source = request.app.state.market.source_status()
    return {
        "app": "FinSight", "version": "0.1.0", "stage": "foundation",
        "local_only": True, "database": "ready",
        "market_data": {"status": "ready" if source["configured"] and source["enabled"] else "not_configured",
                        "key_configured": source["configured"]},
        "model": models.status(),
        "integrations": {"environment": integrations.environment()},
        "encryption": {"status": models.root.encryption.status, "can_save": models.root.encryption.status == "ready",
                       "can_initialize": models.can_initialize()},
        "live_tests": {"api": settings.run_live_api_tests, "llm": settings.run_live_llm_tests},
    }
