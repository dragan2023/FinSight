import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from ..errors import AppError
from ..model_config.limits import OutputBudget
from ..model_config.models import ChatInput, ChatResult, ProbeResult, ProfileInput
from ..model_config.service import ModelService
from .provider_requests import chat_messages, completion_options

CONNECT_SECONDS = 10.0
READ_IDLE_SECONDS = 300.0
TRANSFER_FLOOR_BYTES = 4 * 1024 * 1024


class ProbeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Literal["ok"]


def upstream_error(status: int) -> AppError:
    if status in (401, 403):
        return AppError("model_auth", "模型服务拒绝凭据或当前模型权限，请检查密钥、地域与账户授权。", 502)
    if status == 429:
        return AppError("model_rate_limited", "模型服务限流或额度不足，请稍后再试。", 429, True)
    if status in (400, 404, 405, 422):
        return AppError("model_incompatible", "模型服务不支持本次模型或参数，请核对账户权限、地域和配置。", 502)
    return AppError("model_upstream", "模型服务返回异常状态；上游正文已隐藏。", 502, status >= 500)


def probe_timeout(profile: ProfileInput) -> httpx.Timeout:
    """Probes are explicit small requests with a bounded total time."""
    return httpx.Timeout(profile.timeout, connect=CONNECT_SECONDS, read=profile.timeout, write=profile.timeout)


def generation_timeout(read_idle: float = READ_IDLE_SECONDS) -> httpx.Timeout:
    """Long generation has no short total limit; only connect and read-idle apply.

    Research tasks can add their own time budget and cancellation in stage 03.
    """
    return httpx.Timeout(None, connect=CONNECT_SECONDS, read=read_idle, write=60.0)


def transfer_byte_cap(effective_tokens: int) -> int:
    """Conservative transport ceiling derived from the output budget.

    This guards against runaway responses; it is not a token estimate and never
    silently shrinks legitimate output below the documented budget.
    """
    return max(TRANSFER_FLOOR_BYTES, effective_tokens * 64 + 1024 * 1024)


class ChunkMerger:
    """Shared vendor-agnostic stream merger by choice/tool index.

    reasoning_content is counted for progress only and kept in memory; it is
    never yielded, logged, persisted or shown.
    """

    def __init__(self, byte_cap: int):
        self.byte_cap = byte_cap
        self.content = ""
        self.reasoning_chars = 0
        self.finish: str | None = None
        self.overflow = False
        self.received_bytes = 0
        self._calls: dict[int, dict] = {}

    def add(self, chunk) -> list[dict]:
        events: list[dict] = []
        if not chunk.choices:
            return events
        choice = chunk.choices[0]
        delta = choice.delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            self.reasoning_chars += len(reasoning)
            events.append({"type": "reasoning", "chars": len(reasoning)})
        piece = delta.content or ""
        if piece:
            self.content += piece
            self.received_bytes += len(piece.encode("utf-8"))
            events.append({"type": "delta", "content": piece})
        self.finish = choice.finish_reason or self.finish
        for tool_delta in delta.tool_calls or []:
            call = self._calls.setdefault(tool_delta.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if tool_delta.type and tool_delta.type != "function":
                raise ValueError("unsupported tool type")
            if tool_delta.id:
                call["id"] = tool_delta.id
            if tool_delta.function:
                call["function"]["name"] += tool_delta.function.name or ""
                call["function"]["arguments"] += tool_delta.function.arguments or ""
            if len(self._calls) > 8 or len(call["function"]["arguments"]) > 16384:
                raise ValueError("oversized tool reply")
        if self.received_bytes > self.byte_cap:
            self.overflow = True
        return events

    @property
    def calls(self) -> list[dict]:
        return [call for _, call in sorted(self._calls.items())]


class ModelFactory:
    """The lifespan owns the HTTP pool; SDK instances borrow it without closing it."""

    def __init__(self, service: ModelService, http: httpx.AsyncClient):
        self.service = service
        self.http = http
        self._probe_lock = asyncio.Lock()

    async def create(self, profile_id: str | None = None, *, timeout: httpx.Timeout | None = None) -> tuple[AsyncOpenAI, ProfileInput]:
        profile = await run_in_threadpool(self.service.resolve, profile_id)
        client = AsyncOpenAI(
            base_url=profile.base_url, api_key=profile.api_key.get_secret_value(),
            timeout=timeout or probe_timeout(profile), max_retries=0, http_client=self.http,
        )
        return client, profile

    @asynccontextmanager
    async def request_scope(self, profile_id: str | None, confirmed: bool, *,
                            timeout: httpx.Timeout | None = None, total: bool = True):
        # A manual UI confirmation authorizes exactly this request. Unattended tests remain opt-in.
        if not confirmed and not self.service.settings.run_live_llm_tests:
            raise AppError("live_tests_disabled", "请先确认本次请求将发送至模型服务且可能计费。", 409)
        if self._probe_lock.locked():
            raise AppError("probe_busy", "已有模型请求正在进行，请等待完成。", 409, True)
        async with self._probe_lock:
            client, profile = await self.create(profile_id, timeout=timeout)
            try:
                # Probes stay bounded by a total limit; generation never enters one.
                if total:
                    async with asyncio.timeout(profile.timeout):
                        yield client, profile
                else:
                    yield client, profile
            except (APITimeoutError, httpx.TimeoutException, TimeoutError):
                raise AppError("model_timeout", "模型请求超时，未自动重试；上游仍可能计费。", 504, True) from None
            except APIStatusError as error:
                raise upstream_error(error.status_code) from None
            except (APIConnectionError, httpx.RequestError):
                raise AppError("model_connection", "无法连接模型服务，请检查本机网络与服务地址。", 502, True) from None
            except (APIError, ValueError, AttributeError, TypeError, KeyError):
                raise AppError("model_invalid_response", "模型响应不完整或格式不兼容；原始正文已隐藏。", 502) from None

    def _finish_payload(self, merger: ChunkMerger) -> dict:
        finish = merger.finish
        if merger.overflow:
            return {"type": "done", "content": merger.content, "finish": None, "truncated": True, "abnormal": True,
                    "message": "回复超出本机传输安全上限，已保留已接收内容；结果不完整。"}
        if finish == "content_filter":
            return {"type": "done", "content": merger.content, "finish": finish, "truncated": True, "abnormal": True,
                    "message": "模型服务未返回可展示的内容。"}
        if not finish:
            return {"type": "done", "content": merger.content, "finish": None, "truncated": True, "abnormal": True,
                    "message": "连接中断，未收到正常结束标记；已保留已接收内容。"}
        return {"type": "done", "content": merger.content, "finish": finish,
                "truncated": finish == "length", "abnormal": False}

    async def _consume(self, client: AsyncOpenAI, options: dict, merger: ChunkMerger) -> None:
        response = await client.chat.completions.create(**options)
        async with response:
            async for chunk in response:
                merger.add(chunk)
                if merger.overflow:
                    break

    async def complete(self, client: AsyncOpenAI, options: dict) -> tuple[str, list[dict], str]:
        budget: OutputBudget | None = options.pop("_output_budget", None)
        byte_cap = transfer_byte_cap(budget.effective) if budget else TRANSFER_FLOOR_BYTES
        if not options.get("stream"):
            response = await client.chat.completions.create(**options)
            if not response.choices:
                raise ValueError("empty choices")
            choice = response.choices[0]
            content = choice.message.content or ""
            calls = [call.model_dump() for call in choice.message.tool_calls or []]
            finish = choice.finish_reason
            if len(content.encode("utf-8")) > byte_cap:
                raise ValueError("oversized reply")
        else:
            merger = ChunkMerger(byte_cap)
            # Close streams even on malformed chunks, cancellation or timeout.
            await self._consume(client, options, merger)
            content, calls, finish = merger.content, merger.calls, merger.finish
            if merger.overflow:
                raise ValueError("oversized reply")
        if not finish:
            raise ValueError("incomplete reply")
        if finish == "content_filter":
            raise AppError("model_filtered", "模型服务未返回可展示的内容。", 502)
        return content, calls, finish

    async def chat(self, data: ChatInput, profile_id: str | None = None) -> ChatResult:
        async with self.request_scope(profile_id, data.confirm_external,
                                      timeout=generation_timeout(), total=False) as (client, profile):
            options = completion_options(profile, chat_messages(profile, data))
            content, _, finish = await self.complete(client, options)
            if not content.strip():
                if finish == "length":
                    raise AppError("model_output_limit", "输出预算已耗尽但没有最终回复，请增加输出上限或降低思考预算。", 502)
                raise ValueError("empty chat response")
            return ChatResult(content=content, model_label=profile.model, truncated=finish == "length")

    async def chat_stream(self, data: ChatInput, profile_id: str | None = None):
        """Return an async generator of SSE-ready events; raises AppError eagerly.

        A client disconnect closes the upstream generation; it is never replayed.
        """
        if not data.confirm_external and not self.service.settings.run_live_llm_tests:
            raise AppError("live_tests_disabled", "请先确认本次请求将发送至模型服务且可能计费。", 409)
        if self._probe_lock.locked():
            raise AppError("probe_busy", "已有模型请求正在进行，请等待完成。", 409, True)
        await self._probe_lock.acquire()
        try:
            client, profile = await self.create(profile_id, timeout=generation_timeout())
        except BaseException:
            self._probe_lock.release()
            raise

        async def events():
            merger: ChunkMerger | None = None
            try:
                options = completion_options(profile, chat_messages(profile, data))
                budget: OutputBudget = options.pop("_output_budget")
                options["stream"] = True
                yield {"type": "meta", "model_label": profile.model, "output": budget.as_dict()}
                merger = ChunkMerger(transfer_byte_cap(budget.effective))
                try:
                    response = await client.chat.completions.create(**options)
                    async with response:
                        async for chunk in response:
                            for event in merger.add(chunk):
                                yield event
                            if merger.overflow:
                                break
                    payload = self._finish_payload(merger)
                    if not payload["abnormal"] and not merger.content.strip() and payload["finish"] != "length":
                        payload = {"type": "done", "content": "", "finish": payload["finish"],
                                   "truncated": False, "abnormal": True, "message": "模型没有返回正文内容。"}
                except (APITimeoutError, httpx.TimeoutException, TimeoutError):
                    payload = {"type": "done", "content": merger.content if merger else "", "finish": None,
                               "truncated": True, "abnormal": True,
                               "message": "生成超时或读取空闲超时；已保留已接收内容，未自动重试。"}
                except (APIConnectionError, httpx.RequestError):
                    payload = {"type": "done", "content": merger.content if merger else "", "finish": None,
                               "truncated": True, "abnormal": True,
                               "message": "连接中断；已保留已接收内容，未自动重试。"}
                except APIStatusError as error:
                    mapped = upstream_error(error.status_code)
                    payload = {"type": "error", "category": mapped.category, "message": mapped.message}
                except ValueError:
                    payload = {"type": "error", "category": "model_invalid_response",
                               "message": "模型响应不完整或格式不兼容；原始正文已隐藏。"}
                yield payload
            finally:
                self._probe_lock.release()

        return events()

    async def probe(self, mode: str, profile_id: str | None = None, *, confirm_external: bool = False) -> ProbeResult:
        async with self.request_scope(profile_id, confirm_external) as (client, profile):
            if mode == "connectivity":
                if profile.provider == "glm":
                    return ProbeResult(mode=mode, supported=False, model_label=profile.model,
                                       message="GLM 未提供已核验的模型列表端点，请选择聊天响应验证；本次未发起外部请求。")
                response = await self.http.get(
                    f"{profile.base_url}/models", headers={"Authorization": f"Bearer {profile.api_key.get_secret_value()}"},
                    timeout=probe_timeout(profile),
                )
                if not 200 <= response.status_code < 300:
                    raise upstream_error(response.status_code)
                body = response.json()
                if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                    raise ValueError("invalid models response")
                message = "模型列表端点可访问；不代表聊天或工具调用已经通过。"
            else:
                options = completion_options(profile, [{"role": "user", "content": "Reply with OK only."}], probe=True)
                if mode == "tools":
                    function = {"name": "connection_probe", "description": "A harmless compatibility probe; no action is executed.",
                                "parameters": ProbeArguments.model_json_schema()}
                    if profile.provider == "custom":
                        function["strict"] = True
                    # GLM only accepts auto. Other thinking models also use auto to avoid forced-tool restrictions.
                    options.update(messages=[{"role": "user", "content": "Call connection_probe with value ok."}],
                                   tools=[{"type": "function", "function": function}],
                                   tool_choice="auto" if profile.provider != "custom" else {"type": "function", "function": {"name": "connection_probe"}})
                content, calls, finish = await self.complete(client, options)
                if finish == "length":
                    return ProbeResult(mode=mode, supported=False, model_label=profile.model,
                                       message="输出预算耗尽，未完成验证。请增加输出上限或降低思考预算。")
                if mode == "tools":
                    if len(calls) != 1 or calls[0]["type"] != "function" or calls[0]["function"]["name"] != "connection_probe":
                        return ProbeResult(mode=mode, supported=False, model_label=profile.model,
                                           message="服务已响应，但没有返回指定的结构化工具调用（自动选择不保证调用）。")
                    ProbeArguments.model_validate_json(calls[0]["function"]["arguments"])
                    message = "结构化工具名称和参数校验通过；没有执行任何业务工具。"
                else:
                    if not content.strip():
                        raise ValueError("empty chat response")
                    message = "聊天请求通过；不代表结构化工具调用已经通过。"
            return ProbeResult(mode=mode, supported=True, message=message, model_label=profile.model)
