import logging
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent.model_factory import ModelFactory
from .agent.research_graph import FactoryModelAdapter, ResearchGraph
from .api import advisor as advisor_api, api_config, company, exports, funds, indices, kb as kb_api, library, market_data, market_research, market_review, market_warehouse, model_config, research as research_api, rss as rss_api, sectors, sources as sources_api, strategy as strategy_api, supplements as supplements_api, system, watchlist
from .api_config.repository import ApiRepository
from .api_config.service import ApiConfigService
from .credentials import CredentialRoot
from .errors import AppError
from .market_data.service import MarketDataService
from .market_warehouse.service import MarketWarehouseService
from .web_research.service import WebResearchService
from .advisor.graph import AdvisorGraph
from .advisor.ledger import AdvisorLedger
from .advisor.research_track import ResearchPredictionLedger
from .advisor.service import AdvisorService
from .advisor.runner import AdvisorRunManager, AdvisorRunStore
from .model_config.repository import ModelRepository
from .model_config.service import ModelService
from .research.models import TaskStatus
from .research.reports import ReportStore
from .research.attachments import AttachmentStore
from .research.repository import ResearchRepository
from .research.runner import ResearchTaskManager
from .research.supplements import SupplementStore
from .kb.service import KbService
from .rss.service import RssService
from .research.tasks import TaskStore
from .security import LocalSecurityMiddleware, error_response
from .settings import Settings, load_settings
from .storage import Storage
from .strategy.service import StrategyService


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # SDK debug output may contain request payloads or URLs; never enable it for this app.
        for name in ("httpx", "httpcore", "openai"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        settings.create_directories()
        storage = Storage(settings.db_path)
        storage.initialize()
        app.state.settings = settings
        app.state.storage = storage
        app.state.session_token = secrets.token_urlsafe(32)
        root = CredentialRoot(storage, settings)
        app.state.credentials = root
        app.state.models = ModelService(ModelRepository(storage), root, settings)
        app.state.integrations = ApiConfigService(root, ApiRepository(storage), settings, app.state.models)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                                    timeout=httpx.Timeout(30, connect=10)) as http:
            app.state.http = http
            app.state.model_factory = ModelFactory(app.state.models, http)
            market = MarketDataService(app.state.integrations, storage, http)
            app.state.market = market
            app.state.strategy = StrategyService(storage, market)
            app.state.market_warehouse = MarketWarehouseService(storage, settings, market, http)
            # Settings capability tests for finance adapters run through the market service.
            app.state.integrations.market_service = market
            web = WebResearchService(app.state.integrations, http)
            app.state.web_research = web
            # Settings capability tests for search adapters run through the web service.
            app.state.integrations.web_service = web
            # V2-02：RSS 聚合服务；轮询默认关闭，仅在用户显式开启后运行免费源。
            app.state.rss = RssService(storage, http, market)
            # V2-03：私有知识库；全文与分块仅存本地库，嵌入可选。
            app.state.kb = KbService(storage, app.state.integrations, settings, http)
            app.state.integrations.embedding_service = app.state.kb
            repository = ResearchRepository(storage)
            # 阶段04 批次4：附件上传/解析（只在本机，绝不外发、绝不自动再分析）。
            app.state.attachments = AttachmentStore(repository=repository, settings=settings)
            research_tasks = TaskStore(repository)
            # 进程重启：在途任务收尾为 interrupted，绝不自动再次付费（计划 §2.6）。
            for status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
                for leftover in research_tasks.list_by_status(status):
                    research_tasks.mark_interrupted(leftover.id)

            def build_research_graph(task):
                model = None
                profile_id = task.model_profile_id
                if profile_id is None:
                    for row in app.state.models.repository.list():
                        if row.get("active"):
                            profile_id = row["id"]
                            break
                if profile_id:
                    try:
                        profile = app.state.models.resolve(profile_id)
                        model = FactoryModelAdapter(app.state.model_factory, profile,
                                                    profile_id=profile_id)
                    except AppError:
                        model = None  # 无可用模型：图仍产出事实/计算/缺口与框架
                return ResearchGraph(
                    market=market, model=model, tasks=research_tasks,
                    reports=ReportStore(repository), supplements=SupplementStore(repository),
                    warehouse=app.state.market_warehouse, news=app.state.rss,
                    knowledge=app.state.kb,
                    budget=task.budget, event_sink=research_tasks.event)

            app.state.research = ResearchTaskManager(graph_factory=build_research_graph,
                                                     tasks=research_tasks)
            app.state.ledger = AdvisorLedger(storage)
            app.state.research_predictions = ResearchPredictionLedger(storage)
            app.state.advisor = AdvisorService(storage)
            def build_advisor_graph() -> AdvisorGraph:
                """每次发起顾问任务解析当前激活模型，切换配置无需重启服务。"""
                advisor_model = None
                try:
                    profile_id = next((row["id"] for row in app.state.models.repository.list()
                                       if row.get("active")), None)
                    if profile_id:
                        profile = app.state.models.resolve(profile_id)
                        advisor_model = FactoryModelAdapter(app.state.model_factory, profile,
                                                            profile_id=profile_id)
                except AppError:
                    advisor_model = None
                return AdvisorGraph(
                    model=advisor_model, market=market, knowledge=app.state.kb,
                    web_research=app.state.web_research, rss=app.state.rss,
                    ledger=app.state.ledger, warehouse=app.state.market_warehouse,
                    strategy=app.state.strategy,
                    research_predictions=app.state.research_predictions)

            app.state.advisor_graph_factory = build_advisor_graph
            # 保留同步兼容端点，但每次请求也通过工厂获取当前配置。
            app.state.advisor_graph = build_advisor_graph()
            # 顾问任务落盘：重启后在途任务收尾为 interrupted，已完成任务可查可恢复。
            app.state.advisor_run_store = AdvisorRunStore(storage)
            app.state.advisor_run_store.mark_inflight_interrupted()
            app.state.advisor_runs = AdvisorRunManager(graph_factory=build_advisor_graph,
                                                       service=app.state.advisor,
                                                       store=app.state.advisor_run_store)
            await app.state.rss.start_polling_if_enabled()
            yield
            await app.state.research.shutdown()
            await app.state.advisor_runs.shutdown()
            await app.state.rss.shutdown()
            await app.state.market_warehouse.shutdown()

    app = FastAPI(title="FinSight · 金融洞察", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalSecurityMiddleware, settings=settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-FinSight-Client", "X-FinSight-Session"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)

    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return error_response(error.category, error.message, error.status_code,
                              request.state.request_id, error.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # FastAPI's default response includes input values (including submitted keys).
        return error_response("validation", "输入格式不正确，请检查必填项、地址和数值范围。", 422, request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response("not_found" if error.status_code == 404 else "request_error",
                              "请求的资源不存在或此方法不可用。", error.status_code, request.state.request_id)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        # Do not log exceptions or chained SDK responses; log only the local correlation ID.
        logging.getLogger("finsight").error("request_failed request_id=%s", request.state.request_id)
        return error_response("internal", "本机服务发生错误，请检查配置后重试。", 500, request.state.request_id)

    app.include_router(system.router)
    app.include_router(model_config.router)
    app.include_router(api_config.router)
    app.include_router(market_data.router)
    app.include_router(market_data.EvidenceRouter)
    app.include_router(research_api.router)
    app.include_router(supplements_api.router)
    app.include_router(watchlist.router)
    app.include_router(company.router)
    app.include_router(library.router)
    app.include_router(exports.router)
    app.include_router(market_research.router)
    app.include_router(sectors.router)
    app.include_router(indices.router)
    app.include_router(funds.router)
    app.include_router(market_review.router)
    app.include_router(market_warehouse.router)
    app.include_router(kb_api.router)
    app.include_router(advisor_api.router)
    app.include_router(strategy_api.router)
    app.include_router(rss_api.router)
    app.include_router(sources_api.router)
    return app
