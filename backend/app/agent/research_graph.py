"""Bounded LangGraph research pipeline (plan 03 sections 2.2 / 2.4 / 2.5).

StateGraph 节点：身份确认 → 证据收集（模型工具循环 / 固定取数模板 / 无模型）
→ 确定性计算 → 分节解释 → 校验（复用 output_validation）→ 定稿与持久化。

预算红线：
- 默认最多 24 次 LLM 请求、24 次结构化取数、8 个工具轮；重试计入预算；
- 进入正文分析前为每个模板章节预留 1 次请求，取数轮不得吃掉章节预算；
- 达到预算不再新开付费请求，保留已完成内容。

修复红线（每类每节最多一次，全部计入预算，绝不自动循环）：
- 格式修复：完整文本但 JSON 不合格 → 一次"只修格式"请求；
- 内容重生成：空答（无论断且无待验证问题）或数字/期间冲突 → 一次带材料与
  条件分析任务的重生成；不能把安全过滤当缺数绕过；
- 截断恢复：finish_reason=length / 无正常结束 → 该节 incomplete，即便解析出
  合法 JSON 也不判完整；自动恢复一次，重写该章节而不是拼接两段半截 JSON。

reasoning_content 只保留任务后端内存（仅字符计数）；Graph 不使用落盘
checkpoint；SQLite 只存业务进度、证据与公开结果。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from langgraph.graph import END, START, StateGraph
from openai import APIConnectionError, APIError, APITimeoutError
from pydantic import ValidationError

from ..errors import AppError
from ..model_config.limits import view_budget
from ..model_config.models import ProfileInput
from ..research.evidence_models import DataGap
from ..research.models import RawSectionText, TaskBudget, TaskUsage
from .json_parser import JsonRejected, parse_complete_section_objects, parse_section_object
from .output_models import (
    SECTION_TEMPLATES,
    ChartSpec,
    Coverage,
    MetricValue,
    ModelSnapshot,
    OutputIntegrity,
    ReportSection,
    ResearchReport,
    SectionStatus,
)
from .output_validation import ValidationContext, validate_report
from .prompts import (
    SECTION_SYSTEM,
    TOOL_COLLECTION_RULES,
    content_regen_user,
    format_repair_user,
    section_user,
    tool_collection_user,
)
from .state import (
    MODE_NONE,
    MODE_TEMPLATE,
    MODE_TOOLS,
    DEFAULT_BUDGET,
    ResearchState,
    can_spend,
    new_usage,
    remaining,
)
from .tools import ToolExecutor, compute_metric_set, template_fetch_plan_for_scope
from .toolsets import names_for_scope, schemas_for_scope

MAX_MATERIALS_CHARS = 9000
MAX_NOTICES = 64
CONTENT_CONFLICT_CODES = {"metric_conflict", "period_conflict"}

MATERIALS_PLAN = {
    "facts": ["income", "balance", "cashflow", "price_snapshot", "valuation"],
    "financials": ["income", "balance", "cashflow"],
    "price_valuation": ["price_history", "price_snapshot", "valuation"],
    "risk": ["income", "balance", "cashflow", "price_history"],
    "followups": [],
}


@dataclass(frozen=True)
class TurnResult:
    """一次 LLM 请求的完整结果；finish 缺失或 length 都算 incomplete。"""

    content: str = ""
    tool_calls: list = field(default_factory=list)
    finish_reason: str | None = None
    reasoning_chars: int = 0
    error: dict | None = None

    @property
    def incomplete(self) -> bool:
        return self.error is None and self.finish_reason in (None, "length")


class FactoryModelAdapter:
    """用 01 的 ModelFactory/ChunkMerger/预算执行一次 LLM 请求（流式聚合）。

    每次调用 turn() = 一次付费请求。reasoning_content 只在 ChunkMerger 内存中
    计数，不进入 TurnResult 之外任何位置。任务启动授权覆盖任务内全部请求。
    """

    def __init__(self, factory, profile: ProfileInput, profile_id: str | None = None) -> None:
        self._factory = factory
        self._profile = profile
        self._profile_id = profile_id

    @property
    def profile_id(self) -> str | None:
        return self._profile_id

    @property
    def mode(self) -> str:
        # 自定义接口没有已核验的稳定 tool calling：降级为固定取数模板，
        # 不要求用户换模型（计划 §2.2）。
        return MODE_TOOLS if self._profile.provider != "custom" else MODE_TEMPLATE

    def snapshot(self) -> ModelSnapshot:
        value = view_budget(self._profile)["value"]
        label = f"{value // 1024}K" if value >= 1024 else str(value)
        return ModelSnapshot(provider=self._profile.provider, model=self._profile.model,
                             output_label=label, mode=self.mode, json_mode=False)

    async def turn(self, messages: list[dict], *, tools: list[dict] | None = None) -> TurnResult:
        from .model_factory import ChunkMerger, generation_timeout, transfer_byte_cap
        from .provider_requests import completion_options

        merger: ChunkMerger | None = None
        try:
            async with self._factory.request_scope(
                    self._profile_id, confirmed=True,
                    timeout=generation_timeout(), total=False) as (client, profile):
                options = completion_options(profile, messages)
                budget = options.pop("_output_budget")
                options["stream"] = True
                if tools:
                    options["tools"] = tools
                    options["tool_choice"] = "auto"
                merger = ChunkMerger(transfer_byte_cap(budget.effective))
                response = await client.chat.completions.create(**options)
                async with response:
                    async for chunk in response:
                        merger.add(chunk)
                        if merger.overflow:
                            break
        except AppError as error:
            return TurnResult(error={"category": error.category, "message": error.message})
        except (APITimeoutError, httpx.TimeoutException, TimeoutError, APIConnectionError,
                httpx.RequestError):
            # 连接中断/超时：无正常结束 → incomplete，保留已接收内容。
            return TurnResult(content=merger.content if merger else "",
                              tool_calls=merger.calls if merger else [],
                              reasoning_chars=merger.reasoning_chars if merger else 0)
        except (APIError, ValueError, AttributeError, TypeError, KeyError):
            return TurnResult(error={"category": "model_invalid_response",
                                     "message": "模型响应不完整或格式不兼容；原始正文已隐藏。"})
        assert merger is not None
        if merger.overflow or merger.finish is None:
            return TurnResult(content=merger.content, tool_calls=merger.calls,
                              reasoning_chars=merger.reasoning_chars)
        if merger.finish == "content_filter":
            return TurnResult(error={"category": "model_filtered",
                                     "message": "模型服务未返回可展示的内容；不将过滤当作缺数绕过。"})
        return TurnResult(content=merger.content, tool_calls=merger.calls,
                          finish_reason=merger.finish, reasoning_chars=merger.reasoning_chars)


class ResearchGraph:
    """一次任务运行一个实例；不配置任何 checkpoint，不落盘模型会话状态。"""

    def __init__(self, *, market, model, tasks, reports, supplements=None, warehouse=None,
                 news=None, knowledge=None, budget: TaskBudget | None = None,
                 now: datetime | None = None, event_sink=None) -> None:
        self._market = market
        self._model = model
        self._tasks = tasks
        self._reports = reports
        self._supplements = supplements
        self._warehouse = warehouse
        self._news = news
        self._knowledge = knowledge
        self._budget = budget or TaskBudget(max_llm_requests=DEFAULT_BUDGET["max_llm_requests"],
                                            max_fetches=DEFAULT_BUDGET["max_fetches"],
                                            max_tool_rounds=DEFAULT_BUDGET["max_tool_rounds"])
        self._now = now
        # 事件实时落库（批次 4 SSE）；缺省时仍在 finalize 统一落库。
        self._event_sink = event_sink

    # -- entry ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        if self._model is None:
            return MODE_NONE
        return getattr(self._model, "mode", MODE_TOOLS)

    async def run(self, *, task_id: str, question: str, scope: dict | None = None,
                  parent_report_id: str | None = None, supplement_ids: list[str] | None = None,
                  supplements: list[dict] | None = None) -> ResearchState:
        initial: ResearchState = {
            "task_id": task_id,
            "question": question,
            "scope": dict(scope or {}),
            "mode": self.mode,
            "budget": self._budget.model_dump(),
            "usage": new_usage(),
            "parent_report_id": parent_report_id,
            "supplement_ids": list(supplement_ids or []),
            "supplements": list(supplements or []),
        }
        graph = self._build()
        try:
            return await graph.ainvoke(initial, config={"recursion_limit": 64})
        except Exception:
            # 本地执行意外才是任务失败；数据缺失/模型失败走 completed+缺口。
            self._tasks.mark_failed(task_id, error="研究任务执行失败，请稍后重试或新建任务。")
            raise

    def _build(self):
        builder = StateGraph(ResearchState)
        builder.add_node("confirm_identity", self._confirm_identity)
        builder.add_node("collect_evidence", self._collect_evidence)
        builder.add_node("compute_metrics", self._compute_metrics)
        builder.add_node("write_sections", self._write_sections)
        builder.add_node("validate_sections", self._validate_sections)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "confirm_identity")
        builder.add_edge("confirm_identity", "collect_evidence")
        builder.add_edge("collect_evidence", "compute_metrics")
        builder.add_edge("compute_metrics", "write_sections")
        builder.add_edge("write_sections", "validate_sections")
        builder.add_edge("validate_sections", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()

    # -- node: 身份确认 -------------------------------------------------------
    async def _confirm_identity(self, state: ResearchState) -> dict:
        scope = state.get("scope") or {}
        thscode = scope.get("thscode") or next(iter(scope.get("thscodes") or []), None)
        identity = None
        gaps: list[dict] = []
        if thscode and self._market is not None:
            try:
                identity = self._market.require_identity(thscode).model_dump()
            except AppError:
                identity = None
        needs_identity = scope.get("type", "general") not in {"general", "market_pulse", "market_breadth"}
        if identity is None and needs_identity:
            gaps.append(DataGap(
                id=uuid.uuid4().hex, dimension="identity", fields=["thscode"],
                security_id=None, period=None, reason="not_configured",
                impact="标的身份未确认，结构化取数无法进行；报告将基于问题给出条件分析框架与取证清单。",
                remedies=["先在资料预览中检索并确认标的，再重新发起分析。"]).model_dump())
        detail = (f"标的已确认：{identity['thscode']}。" if identity
                  else ("本研究范围不要求单一标的身份。" if not needs_identity
                        else "标的身份未确认；继续生成分析框架。"))
        notice = {"type": "phase", "phase": "identity", "detail": detail}
        self._emit(task_id := state["task_id"], [notice])
        return {"identity": identity, "gaps": gaps,
                "notices": list(state.get("notices") or []) + [notice]}

    # -- node: 证据收集 -------------------------------------------------------
    async def _collect_evidence(self, state: ResearchState) -> dict:
        base_notices = list(state.get("notices") or [])
        executor = ToolExecutor(market=self._market, warehouse=self._warehouse, news=self._news,
                                knowledge=self._knowledge, identity=state.get("identity"),
                                usage=state.get("usage") or new_usage(),
                                budget=state.get("budget") or DEFAULT_BUDGET, now=self._now,
                                allowed_tools=names_for_scope(state.get("scope") or {}))
        usage = executor.usage
        notices: list[dict] = []
        if self.mode in (MODE_TEMPLATE, MODE_NONE):
            for name, args in template_fetch_plan_for_scope(state.get("scope") or {}, self._now):
                outcome = await executor.execute_program(name, args)
                if outcome["status"] == "error" and outcome["error"]["reason"] == "budget_exhausted":
                    notices.append({"type": "phase", "phase": "collect",
                                    "detail": "取数预算已用尽，固定模板提前结束。"})
                    break
            notices.append({"type": "phase", "phase": "collect",
                            "detail": ("所选模型不支持结构化工具调用，已按固定模板取数（能力降级）。"
                                       if self.mode == MODE_TEMPLATE
                                       else "没有可用模型，已按本领域固定计划取数并保留事实与缺口。")})
        else:
            notices += await self._tool_loop(state, executor, usage)
        self._emit(state["task_id"], notices)
        return {"records": executor.records, "group_evidence": executor.group_evidence,
                "evidence_index": executor.evidence_index,
                "gaps": list(state.get("gaps") or []) + executor.gaps,
                "usage": usage, "notices": base_notices + notices}

    async def _tool_loop(self, state: ResearchState, executor: ToolExecutor, usage: dict) -> list[dict]:
        budget = state.get("budget") or DEFAULT_BUDGET
        # 预留：每章至少一次正式分析，另为 JSON 异常预留一次自然语言重写，
        # 不让取数阶段消耗掉用户可读正文所需的请求额度。
        evidence_budget = remaining(budget, usage, "llm_requests") - (2 * len(SECTION_TEMPLATES))
        rounds_left = remaining(budget, usage, "tool_rounds")
        if evidence_budget < 1:
            return [{"type": "phase", "phase": "collect",
                     "detail": "剩余模型请求不足以同时覆盖取数与章节生成，已跳过模型取数轮。"}]
        messages = [
            {"role": "system", "content": TOOL_COLLECTION_RULES},
            {"role": "user", "content": tool_collection_user(
                state["question"], state.get("scope") or {}, state.get("identity"))},
        ]
        notices: list[dict] = []
        while evidence_budget >= 1 and rounds_left >= 1:
            turn = await self._model.turn(messages, tools=schemas_for_scope(state.get("scope") or {}))
            usage["llm_requests"] = int(usage.get("llm_requests", 0)) + 1
            reasoning = self._reasoning_notice(usage, turn)
            if reasoning:
                # 只在此即时落库一次；返回列表不再携带，避免 _collect_evidence 重复落库。
                self._emit(state["task_id"], [reasoning])
            evidence_budget -= 1
            if turn.error is not None:
                notices.append({"type": "phase", "phase": "collect",
                                "detail": f"取数轮模型请求失败：{turn.error['message']}"})
                break
            if not turn.tool_calls:
                break
            messages.append({"role": "assistant", "content": turn.content or "",
                             "tool_calls": turn.tool_calls})
            for call in turn.tool_calls:
                outcome = await executor.execute(call)
                messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                                 "content": json.dumps(outcome, ensure_ascii=False)})
            usage["tool_rounds"] = int(usage.get("tool_rounds", 0)) + 1
            rounds_left -= 1
        if rounds_left < 1:
            notices.append({"type": "phase", "phase": "collect",
                            "detail": "工具轮预算已用尽，转入已有材料分析。"})
        return notices

    # -- node: 确定性计算 ------------------------------------------------------
    async def _compute_metrics(self, state: ResearchState) -> dict:
        metrics = compute_metric_set(state.get("records") or {}, state.get("group_evidence") or {})
        notice = {"type": "phase", "phase": "metrics",
                  "detail": f"程序计算指标完成：{len(metrics)} 项（缺数指标保留空值与说明）。"}
        self._emit(state["task_id"], [notice])
        return {"metrics": [metric.model_dump() for metric in metrics],
                "notices": list(state.get("notices") or []) + [notice]}

    # -- node: 分节解释 --------------------------------------------------------
    async def _write_sections(self, state: ResearchState) -> dict:
        usage = dict(state.get("usage") or new_usage())
        budget = state.get("budget") or DEFAULT_BUDGET
        context = self._validation_context(state)
        sections: list[ReportSection] = []
        section_states: dict[str, dict] = {}
        raw_sections: list[dict] = []
        notices = list(state.get("notices") or [])
        if self.mode == MODE_NONE or self._model is None:
            for template in SECTION_TEMPLATES:
                sections.append(self._placeholder(template, "没有可用模型，本节未生成 AI 内容。"))
                section_states[template["id"]] = {"integrity": "missing", "llm_requests": 0}
            notice = {"type": "phase", "phase": "sections",
                      "detail": "没有可用模型，未完成 AI 分析；本地事实、计算与缺口已保留。"}
            notices.append(notice)
            self._emit(state["task_id"], [notice])
            return {"sections": [s.model_dump() for s in sections],
                    "section_states": section_states, "raw_sections": raw_sections,
                    "usage": usage, "notices": notices}
        for template in SECTION_TEMPLATES:
            if not can_spend(budget, usage, "llm_requests"):
                sections.append(self._placeholder(template, "调用预算已用尽，本节未生成；已完成内容已保留。"))
                section_states[template["id"]] = {"integrity": "missing", "llm_requests": 0}
                notices.append({"type": "section", "id": template["id"], "status": "unavailable",
                                "detail": "调用预算已用尽，本节未生成。"})
                continue
            materials = self._materials(state, template["id"])
            messages = [
                {"role": "system", "content": SECTION_SYSTEM},
                {"role": "user", "content": section_user(state["question"], state.get("scope") or {},
                                                         template, materials)},
            ]
            turn = await self._model.turn(messages)
            usage = _spend(usage, "llm_requests")
            reasoning = self._reasoning_notice(usage, turn)
            if reasoning:
                notices.append(reasoning)
                self._emit(state["task_id"], [reasoning])
            section, integrity = await self._process_section(
                template, state, materials, turn, usage, budget, context,
                raw_sections, section_states)
            section_states[template["id"]]["llm_requests"] = section_states[template["id"]].get("llm_requests", 0) + 1
            sections.append(section)
            section_notice = {"type": "section", "id": template["id"], "status": section.status,
                              "integrity": integrity,
                              "detail": f"章节【{template['title']}】完成：{section.status}。"}
            notices.append(section_notice)
            self._emit(state["task_id"], [section_notice])
        return {"sections": [s.model_dump() for s in sections],
                "section_states": section_states, "raw_sections": raw_sections,
                "usage": usage, "notices": notices}

    async def _process_section(self, template, state, materials, turn: TurnResult,
                               usage: dict, budget: dict, context: ValidationContext,
                               raw_sections: list, section_states: dict):
        """处理一轮输出：错误 → 不可用；截断或 Markdown 标记异常 → 重写正文。"""
        section_id = template["id"]
        st = {"integrity": "validated", "format_repair_used": False,
              "content_regen_used": False, "recovery_used": False}
        section_states[section_id] = st
        if turn.error is not None:
            # 预留的一次正文重写也用于瞬时模型错误；这样正常的短暂失败不会留下
            # “本节未生成”的空洞卡片。
            return await self._repair_format(template, state, materials, turn, usage, budget,
                                             context, raw_sections, st)
        if turn.incomplete:
            return await self._recover_section(template, state, materials, turn, usage, budget,
                                               context, raw_sections, st)
        section, parse_error = self._parse_section(template, turn.content)
        if parse_error is not None:
            return await self._repair_format(template, state, materials, turn, usage, budget,
                                             context, raw_sections, st)
        return await self._accept_section(template, section, state, materials, usage, budget,
                                          context, raw_sections, st, turn.content)

    async def _recover_section(self, template, state, materials, failed_turn: TurnResult,
                               usage: dict, budget: dict, context: ValidationContext,
                               raw_sections: list, st: dict):
        section_id = template["id"]
        # A transport interruption can follow an already complete object.  In
        # that narrow case preserve the intact object and drop only the damaged
        # tail; do not synthesize brackets/values or mix two competing objects.
        recovered = self._recover_complete_prefix(template, failed_turn.content)
        if recovered is not None:
            st["integrity"] = "partial_recovered"
            return await self._accept_section(template, recovered, state, materials, usage, budget,
                                              context, raw_sections, st, failed_turn.content)
        # 截断且没有完整对象时，不再重写 JSON。保留可解析的完整前缀后，直接让
        # 模型基于同一材料重写用户可读正文，避免又得到半截 JSON。
        return await self._repair_format(template, state, materials, failed_turn, usage, budget,
                                         context, raw_sections, st)

    async def _repair_format(self, template, state, materials, failed_turn: TurnResult,
                             usage: dict, budget: dict, context: ValidationContext,
                             raw_sections: list, st: dict):
        section_id = template["id"]
        if st["format_repair_used"] or not can_spend(budget, usage, "llm_requests"):
            raw_sections.append({"id": section_id, "title": template["title"],
                                 "reason": "unparseable_no_repair", "text": failed_turn.content})
            st["integrity"] = "unstructured"
            return self._placeholder(template, "本节未能生成可核验的正文，已保留其他章节内容。"), st["integrity"]
        st["format_repair_used"] = True
        messages = [
            {"role": "system", "content": SECTION_SYSTEM},
            {"role": "user", "content": format_repair_user(
                state["question"], state.get("scope") or {}, template, materials,
                failed_turn.content, "未找到完整的 Markdown 标题")},
        ]
        turn = await self._model.turn(messages)
        _spend(usage, "llm_requests")
        usage["reasoning_chars"] += turn.reasoning_chars
        if turn.error is not None or turn.incomplete or not turn.content.strip():
            raw_sections.append({"id": section_id, "title": template["title"],
                                 "reason": "repair_failed", "text": failed_turn.content})
            st["integrity"] = "unstructured"
            return self._placeholder(template, "本节未能生成可核验的正文，已保留其他章节内容。"), st["integrity"]
        section, parse_error = self._parse_section(template, turn.content)
        if parse_error is None:
            st["integrity"] = "markdown_rewritten"
            return await self._accept_section(template, section, state, materials, usage, budget,
                                              context, raw_sections, st, turn.content)
        # 即使 Markdown 标题再次丢失，只要模型给的是自然语言，仍将其作为本节
        # 正文显示；不能把格式错误或原始 JSON 展示给用户。
        if not turn.content.lstrip().startswith("{"):
            st["integrity"] = "markdown_rewritten"
            return self._natural_language_section(template, turn.content), st["integrity"]
        raw_sections.append({"id": section_id, "title": template["title"],
                             "reason": "markdown_rewrite_invalid", "text": turn.content})
        st["integrity"] = "unstructured"
        return self._placeholder(template, "本节分析暂未生成，其他章节仍可正常阅读。"), st["integrity"]

    async def _accept_section(self, template, section: ReportSection, state, materials,
                              usage: dict, budget: dict, context: ValidationContext,
                              raw_sections: list, st: dict, original_text: str):
        validated, codes, content_rejected = self._validate_section(section, state, context)
        needs_regen = content_rejected or bool(codes & CONTENT_CONFLICT_CODES)
        if needs_regen and not st["content_regen_used"] and can_spend(budget, usage, "llm_requests"):
            st["content_regen_used"] = True
            messages = [
                {"role": "system", "content": SECTION_SYSTEM},
                {"role": "user", "content": content_regen_user(state["question"],
                                                               state.get("scope") or {}, template, materials)},
            ]
            turn = await self._model.turn(messages)
            _spend(usage, "llm_requests")
            usage["reasoning_chars"] += turn.reasoning_chars
            if turn.error is None and not turn.incomplete:
                regenerated, parse_error = self._parse_section(template, turn.content)
                if parse_error is None:
                    validated, codes, content_rejected = self._validate_section(regenerated, state, context)
        if content_rejected:
            # 一次重生成后仍为空答：如实保留为 unavailable，不再循环。
            validated = validated.model_copy(update={"status": SectionStatus.UNAVAILABLE})
        return validated, st["integrity"]

    def _validate_section(self, section: ReportSection, state, context: ValidationContext):
        draft = self._draft_report(state, [section])
        validated, issues = validate_report(draft, context)
        codes = {issue.code for issue in issues if issue.section_id == section.id}
        content_rejected = not validated.sections[0].claims and not validated.sections[0].follow_up_questions
        return validated.sections[0], codes, content_rejected

    # -- node: 校验 ------------------------------------------------------------
    async def _validate_sections(self, state: ResearchState) -> dict:
        sections = state.get("sections") or []
        notices = list(state.get("notices") or [])
        if not sections:
            return {"notices": notices}
        draft = self._draft_report(state, [ReportSection.model_validate(s) for s in sections])
        validated, issues = validate_report(draft, self._validation_context(state))
        issue_notices = [{"type": "validation", "section": issue.section_id,
                          "code": issue.code, "detail": issue.message}
                         for issue in issues[:20]]
        notices += issue_notices
        self._emit(state["task_id"], issue_notices)
        return {"sections": [s.model_dump() for s in validated.sections], "notices": notices}

    # -- node: 定稿 ------------------------------------------------------------
    async def _finalize(self, state: ResearchState) -> dict:
        section_states = state.get("section_states") or {}
        coverage = self._coverage(state.get("records") or {})
        integrity, limitations = self._integrity(state)
        snapshot = self._model.snapshot() if (self._model is not None and hasattr(self._model, "snapshot")) else None
        report = ResearchReport(
            id=uuid.uuid4().hex, version=1, task_id=state["task_id"],
            question=state["question"], scope=dict(state.get("scope") or {}),
            model_snapshot=snapshot,
            parent_report_id=state.get("parent_report_id"),
            evidence_ids=sorted((state.get("evidence_index") or {})),
            metrics=[MetricValue.model_validate(m) for m in state.get("metrics") or []],
            chart_specs=self._chart_specs(state.get("metrics") or [], state.get("scope") or {}),
            sections=[ReportSection.model_validate(s) for s in state.get("sections") or []],
            data_gaps=[DataGap.model_validate(g) for g in state.get("gaps") or []],
            coverage=coverage, output_integrity=integrity, limitations=limitations)
        raw = [RawSectionText.model_validate(item) for item in state.get("raw_sections") or []]
        stored = self._reports.save(report, raw_sections=raw)
        usage = state.get("usage") or new_usage()
        self._tasks.mark_completed(state["task_id"], usage=TaskUsage(**usage),
                                   report_id=stored.id)
        if self._supplements is not None and state.get("supplement_ids"):
            # 再分析显式引用的用户补充：记录引用关系，不改动原报告。
            self._supplements.mark_referenced(list(state["supplement_ids"]), stored.id)
        final_notice = {"type": "report", "report_id": stored.id,
                        "detail": f"报告已保存：coverage={coverage.value} output_integrity={integrity.value}"}
        if self._event_sink is None:
            # 单元测试路径：无实时落库时在 finalize 统一批量落库。
            for notice in (list(state.get("notices") or []) + [final_notice])[:MAX_NOTICES]:
                self._tasks.event(state["task_id"], str(notice.get("type") or "progress"), notice)
        else:
            self._emit(state["task_id"], [final_notice])
        self._tasks.event(state["task_id"], "usage", usage)
        return {"report_id": stored.id, "finished_reason": "completed",
                "notices": list(state.get("notices") or []) + [final_notice]}

    # -- helpers ---------------------------------------------------------------
    def _emit(self, task_id: str, notices: list[dict]) -> None:
        """公开进度事件实时落库；失败不影响研究执行。"""
        if self._event_sink is None:
            return
        for notice in notices:
            try:
                self._event_sink(task_id, str(notice.get("type") or "progress"), notice)
            except Exception:  # noqa: BLE001 - 进度事件绝不阻断任务
                return

    @staticmethod
    def _reasoning_notice(usage: dict, turn: TurnResult) -> dict | None:
        """长思考活动只以字符计数公开（计划 §2.6）。"""
        if turn.reasoning_chars <= 0:
            return None
        usage["reasoning_chars"] = int(usage.get("reasoning_chars", 0)) + turn.reasoning_chars
        return {"type": "reasoning", "chars": turn.reasoning_chars}

    def _validation_context(self, state: ResearchState) -> ValidationContext:
        return ValidationContext(
            evidence_index=dict(state.get("evidence_index") or {}),
            metric_names={m["metric"] for m in state.get("metrics") or []},
            gap_ids={g["id"] for g in state.get("gaps") or []},
            scope_thscode=(state.get("scope") or {}).get("thscode"),
        )

    def _draft_report(self, state, sections: list[ReportSection]) -> ResearchReport:
        return ResearchReport(
            id="draft", version=1, task_id=state["task_id"], question=state["question"],
            scope=dict(state.get("scope") or {}),
            metrics=[MetricValue.model_validate(m) for m in state.get("metrics") or []],
            chart_specs=self._chart_specs(state.get("metrics") or [], state.get("scope") or {}),
            sections=sections,
            data_gaps=[DataGap.model_validate(g) for g in state.get("gaps") or []],
            coverage=Coverage.PARTIAL, output_integrity=OutputIntegrity.PARTIAL)

    @staticmethod
    def _chart_specs(metrics: list[dict], scope: dict | None = None) -> list[ChartSpec]:
        """Generate display-only specs from deterministic metric IDs, never LLM text."""
        refs = [str(item.get("metric")) for item in metrics
                if item.get("metric") and item.get("value") is not None]
        if not refs:
            return []
        primary = ChartSpec(id="deterministic-metrics", chart_type="bar",
                            title="确定性指标对比", metric_refs=refs[:16])
        return [primary, ChartSpec(
            id="research-dashboard", chart_type="panel_grid", title="研究指标看板",
            panel_grid={"columns": 12, "cells": [
                {"row": 0, "column": 0, "row_span": 1, "column_span": 12,
                 "component": "metrics", "title": "关键数据"},
                {"row": 1, "column": 0, "row_span": 2, "column_span": 8,
                 "component": "chart", "reference": "deterministic-metrics", "title": "指标对比"},
                {"row": 1, "column": 8, "row_span": 2, "column_span": 4,
                 "component": "gaps", "title": "数据缺口"},
            ]})]

    def _parse_section(self, template, text: str):
        if not text or not text.strip():
            return None, "模型没有返回内容"
        markdown = self._parse_markdown_section(template, text)
        if markdown is not None:
            return markdown, None
        # 仅兼容已保存的历史模型输出或升级期间的旧响应；新提示词和正常运行路径
        # 均只使用 Markdown，不把 JSON 作为报告正文协议。
        try:
            obj, _cleanups = parse_section_object(text, max_bytes=32768)
        except JsonRejected as error:
            return None, error.message
        obj["id"] = template["id"]
        obj["title"] = template["title"]
        try:
            return ReportSection.model_validate(obj), None
        except ValidationError:
            return None, "章节字段不符合报告契约"

    @staticmethod
    def _parse_markdown_section(template, text: str) -> ReportSection | None:
        cleaned = text.strip().removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
        if cleaned.startswith("{"):
            return None
        heading = re.compile(r"^##\s+(核心结论|结论|分析依据|分析|风险与待核验|风险提示|待核验)\s*$", re.MULTILINE)
        matches = list(heading.finditer(cleaned))
        if not matches:
            return None
        blocks: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
            blocks[match.group(1)] = cleaned[match.end():end].strip()
        conclusion = blocks.get("核心结论") or blocks.get("结论") or ""
        analysis = blocks.get("分析依据") or blocks.get("分析") or ""
        risks = blocks.get("风险与待核验") or blocks.get("风险提示") or blocks.get("待核验") or ""
        if not conclusion:
            return None

        def lines(value: str) -> list[str]:
            return [item.strip(" -•\t") for item in value.splitlines() if item.strip(" -•\t")]

        claim_lines = lines(analysis)
        risk_lines = lines(risks)
        if not claim_lines:
            claim_lines = [conclusion]
        return ReportSection(
            id=template["id"], title=template["title"], status=SectionStatus.CONDITIONAL,
            summary=conclusion[:2000],
            claims=[{"kind": "inference", "text": item[:1200]} for item in claim_lines[:12]],
            follow_up_questions=risk_lines[:8],
        )

    @staticmethod
    def _recover_complete_prefix(template, text: str) -> ReportSection | None:
        if not text or not text.strip():
            return None
        try:
            values, cleanups = parse_complete_section_objects(text, max_bytes=32768)
        except JsonRejected:
            return None
        if not cleanups or len(values) != 1:
            return None
        value = dict(values[0])
        value["id"] = template["id"]
        value["title"] = template["title"]
        try:
            return ReportSection.model_validate(value)
        except ValidationError:
            return None

    @staticmethod
    def _natural_language_section(template, text: str) -> ReportSection:
        cleaned = text.strip().removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
        # 个别模型会无视自然语言要求仍返回 JSON。服务端只提取其中的读者正文，
        # 不把 JSON 字符串返回给页面。
        if cleaned.startswith("{"):
            try:
                obj, _ = parse_section_object(cleaned, max_bytes=32768)
                parts = [str(obj.get("summary") or "")]
                parts.extend(str(item.get("text") or "") for item in obj.get("claims") or [])
                parts.extend("风险提示：" + str(item.get("text") or "")
                             for item in obj.get("counter_evidence") or [])
                questions = [str(item) for item in obj.get("follow_up_questions") or [] if item]
                if questions:
                    parts.append("仍待核验：" + "；".join(questions))
                cleaned = "\n\n".join(part for part in parts if part)
            except JsonRejected:
                pass
        return ReportSection(id=template["id"], title=template["title"],
                             status=SectionStatus.CONDITIONAL, summary=cleaned[:2000])

    def _placeholder(self, template, summary: str) -> ReportSection:
        return ReportSection(id=template["id"], title=template["title"],
                             status=SectionStatus.UNAVAILABLE, summary=summary)

    def _materials(self, state: ResearchState, section_id: str) -> str:
        records = state.get("records") or {}
        lines: list[str] = []
        metrics = state.get("metrics") or []
        if metrics:
            lines.append("程序计算指标（事实数字用 metric_refs 引用指标名，不要改写数值）：")
            lines += [f"- {m['metric']}: {m['value']} ({m['unit']}) 口径={m['basis']}"
                      + (f" 备注：{m['note']}" if m.get("note") else "")
                      for m in metrics]
        gaps = state.get("gaps") or []
        if gaps:
            lines.append("程序缺口（用 gap_refs 引用缺口 ID）：")
            lines += [f"- {g['id']}: 维度={g['dimension']} 字段={g['fields']} 原因={g['reason']} 影响={g['impact']}"
                      for g in gaps[:16]]
        supplements = state.get("supplements") or []
        if supplements:
            lines.append("用户补充材料（user_provided/user_assumption；作为用户假设处理，不改写官方证据）：")
            for s in supplements[:12]:
                # 来源标注（附件文件名/解析方式或用户备注）随材料进入提示词，
                # 让模型能区分"用户自述"与官方证据；不改写内容本身。
                source_note = (s.get("fields") or {}).get("source_note")
                suffix = f"（来源：{source_note}）" if source_note else ""
                lines.append(f"- [{s.get('kind')}] {s.get('content')}{suffix}")
        for group in MATERIALS_PLAN.get(section_id, []):
            rows = records.get(group) or []
            if not rows:
                continue
            if group == "price_history":
                lines.append(f"价格日线：共 {len(rows)} 根（{rows[0].get('period_key')} → "
                             f"{rows[-1].get('period_key')}）；区间涨跌与最大回撤引用指标 range_change/max_drawdown。")
                sample = [rows[0], rows[len(rows) // 2], rows[-1]]
            else:
                sample = rows[:8]
            lines.append(f"证据记录摘录（{group}，最多 8 条；全文按证据 ID 另读）：")
            lines += [self._record_line(row) for row in sample]
        evidence_ids = sorted(state.get("evidence_index") or {})
        if evidence_ids:
            lines.append("本任务证据 ID：" + "、".join(evidence_ids))
        return "\n".join(lines)[:MAX_MATERIALS_CHARS]

    @staticmethod
    def _record_line(record: dict) -> str:
        fields = []
        for key, value in list((record.get("fields") or {}).items())[:6]:
            unit = value.get("unit") or ""
            fields.append(f"{key}={value.get('value')}" + (f"({unit})" if unit else ""))
        period = record.get("period_key") or "无期间"
        return f"- {period} [{record.get('basis')}]" + ("；".join([""] + fields) if fields else "")

    @staticmethod
    def _coverage(records: dict) -> Coverage:
        financial_ok = any(records.get(group) for group in ("income", "balance", "cashflow"))
        price_ok = any(records.get(group) for group in ("price_history", "price_snapshot"))
        valuation_ok = bool(records.get("valuation"))
        if financial_ok and price_ok and valuation_ok:
            return Coverage.SUFFICIENT
        # Company coverage requires the three established fact groups above,
        # while index/market/fund/warehouse scopes contribute records under
        # their own domain keys.  Those records are valid business evidence
        # too; reporting them as "no_business_evidence" would hide a useful
        # partial result merely because it is not a single-stock study.
        if financial_ok or price_ok or valuation_ok or any(records.values()):
            return Coverage.PARTIAL
        return Coverage.NO_BUSINESS_EVIDENCE

    def _integrity(self, state: ResearchState):
        states = list((state.get("section_states") or {}).values())
        limitations: list[str] = []
        if any(item.get("integrity") == "incomplete" for item in states):
            integrity = OutputIntegrity.TRUNCATED
            limitations.append("存在被截断且自动恢复未成功的章节；已完成章节保持可用，可在报告中手动继续该节。")
        elif any(item.get("integrity") == "unstructured" for item in states):
            integrity = OutputIntegrity.UNSTRUCTURED
            limitations.append("部分章节未能生成可核验的正文，已保留其他章节内容。")
        elif any(item.get("integrity") in {"natural_language", "markdown_rewritten"} for item in states):
            integrity = OutputIntegrity.PARTIAL
            limitations.append("部分章节已按可读正文重新生成；其中的结论应结合数据范围与待核验事项阅读。")
        elif self.mode == MODE_NONE or any(item.get("integrity") == "missing" for item in states) or not states:
            integrity = OutputIntegrity.PARTIAL
        else:
            integrity = OutputIntegrity.VALIDATED
        if any(item.get("integrity") == "partial_recovered" for item in states):
            integrity = OutputIntegrity.PARTIAL
            limitations.append("部分模型输出在完整 JSON 后中断；已丢弃损坏尾部并保留结构完整的内容。")
        missing = sum(1 for item in states if item.get("integrity") == "missing")
        if missing:
            limitations.append(f"调用预算耗尽，{missing} 个章节未生成；已完成内容已保留，可新建任务继续。")
        if self.mode == MODE_NONE:
            limitations.append("没有可用模型，未完成 AI 分析；本报告仅包含本地事实、程序计算与缺口清单。")
        elif self.mode == MODE_TEMPLATE:
            limitations.append("所选模型不支持结构化工具调用，已按固定模板取数（能力降级）；未能按需追加取数。")
        return integrity, limitations


def _spend(usage: dict, kind: str, n: int = 1) -> dict:
    usage[kind] = int(usage.get(kind, 0)) + n
    return usage
