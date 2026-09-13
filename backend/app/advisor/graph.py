"""V2-04 Advisor 编排图：澄清→计划→四分析师→交叉验证→辩论→风控→综合→校验→登记。

预算红线（方案 §2 定稿）：单条建议默认 ≤14 次 LLM 请求——澄清 1 + 计划 1 +
分析师 4 + 交叉验证 1 + 辩论 4 + 风控 1 + 综合 1 + 修复 1。超限先砍辩论
（提示词优先级调研：辩论是最先可牺牲项），再砍交叉验证。
不建第二套图引擎：顺序代码；取证由程序按维度取数，
不消耗 LLM 轮次。每个证据段落编号（F/T/N/C/D/R 开头），综合者按编号引用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError

from ..errors import AppError
from ..kb.query_builder import build_strategy_query, infer_horizon
from ..agent.json_parser import JsonRejected, parse_section_object
from ..web_research.models import WebSearchRequest
from ..agent.research_graph import FactoryModelAdapter
from ..strategy.registry import get_spec
from .ledger import AdvisorLedger, _base_cutoff_date, _now_iso
from .research_track import ResearchPredictionLedger
from .evidence import AdvisorEvidenceItem, evidence_index, metric_index
from .algorithms import (BASIS_CORPORATE_ACTION_TOTAL_RETURN, BASIS_FORWARD_ADJUSTED_SOURCE,
                         calculate_base_rate, calculate_f_score, calculate_half_kelly,
                         calculate_scenario_ev, calculate_volatility_target, calculate_z_score,
                         total_return_closes)
from .output_models import EvSummary, SuggestionCard
from .prompts import (analyst_system, analyst_user, clarifier_system, clarifier_user,
                      debater_system, planner_system, reflector_system, risk_officer_system,
                      synthesizer_system)
from .suggestion_validation import validate_suggestion

logger = logging.getLogger(__name__)

ELEMENT_READY_MARK = "决策要素已齐全"
REQUESTS_ANALYSTS = 4
REQUESTS_CROSSCHECK = 1
REQUESTS_DEBATE = 4
REQUESTS_RISK = 1
REQUESTS_SYNTH = 1
REQUESTS_REPAIR = 1
PACK_CHARS = 5200
BRIEF_CHARS = 2400
SNAPSHOT_BARS = 8
# 顾问链由一次用户操作直接触发，不能沿用研究任务的长时间后台执行策略。
# 单次模型请求与整条链路均设置硬上限；asyncio 的取消会原样向上传播。
# 当前支持的推理模型配置允许最长 120 秒；顾问层不能再用 25 秒把一个本来
# 正常的模型请求提前截断。整条标准链为综合、一次修复与一次冲突审计预留
# 3 个请求窗口，仍有总时限兜底，绝不无限等待或自动重试。
ADVISOR_REQUEST_TIMEOUT_SECONDS = 120.0
ADVISOR_RUN_TIMEOUT_SECONDS = 360.0


class AdvisorGraph:
    """一次 run() = 一次用户发言的处理；澄清未齐时返回反问并结束本轮。"""

    # 检查点阶段序：恢复时按此判断哪些付费阶段已完成、可以跳过。
    _STAGES = ("clarified", "planned", "fetched", "analyzed",
               "debated", "risked", "synthesized", "registered", "research_sealed")

    def __init__(self, *, model: FactoryModelAdapter | None, market, knowledge,
                 web_research, ledger: AdvisorLedger, research_predictions: ResearchPredictionLedger | None = None, rss=None,
                 warehouse=None, strategy=None, max_llm_requests: int = 14,
                 request_timeout_seconds: float = ADVISOR_REQUEST_TIMEOUT_SECONDS,
                 run_timeout_seconds: float = ADVISOR_RUN_TIMEOUT_SECONDS) -> None:
        self._model = model
        self._market = market
        self._knowledge = knowledge
        self._web = web_research
        self._rss = rss
        self._ledger = ledger
        self._research_predictions = research_predictions
        self._warehouse = warehouse
        self._strategy = strategy
        self._cap = max(6, int(max_llm_requests))
        self._request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        self._run_timeout_seconds = max(0.1, float(run_timeout_seconds))

    async def run(self, *, session_id: str, question: str, profile: dict,
                  history: list[dict], allow_web_search: bool = False,
                  analysis_mode: str = "standard",
                  event_sink=None, resume_state: dict | None = None,
                  checkpoint_sink=None) -> dict:
        if analysis_mode not in {"standard", "deep"}:
            raise AppError("invalid_analysis_mode", "分析模式仅支持 standard 或 deep。", 422)
        if self._model is None:
            raise AppError("not_configured", "尚未配置模型；请在「模型与 API 配置」接入后重试。", 409)
        try:
            async with asyncio.timeout(self._run_timeout_seconds):
                return await self._run(session_id=session_id, question=question, profile=profile,
                                       history=history, allow_web_search=allow_web_search,
                                       analysis_mode=analysis_mode,
                                       event_sink=event_sink, resume_state=resume_state,
                                       checkpoint_sink=checkpoint_sink)
        except TimeoutError:
            raise AppError("advisor_timeout",
                           f"本次顾问分析超过 {self._run_timeout_seconds:g} 秒，已停止；可直接继续此会话重试。",
                           504, True) from None

    async def _run(self, *, session_id: str, question: str, profile: dict,
                   history: list[dict], allow_web_search: bool = False,
                   analysis_mode: str = "standard",
                   event_sink=None, resume_state: dict | None = None,
                   checkpoint_sink=None) -> dict:
        # 恢复语义：resume_state 携带上一轮已完成的阶段产物，只补做未完成的
        # 付费阶段；问题不匹配时忽略检查点，按全新任务执行。检查点只存阶段
        # 产物（提示词可见材料与程序取数结果），不存模型隐藏推理。
        resume = resume_state if (resume_state and resume_state.get("question") == question) else {}
        stage = resume.get("stage", "")

        def reached(name: str) -> bool:
            return stage in self._STAGES and self._STAGES.index(stage) >= self._STAGES.index(name)

        # 检查点状态滚动累积：每个检查点都携带此前全部阶段产物，
        # 恢复时无论停在哪一阶段都能读到所需输入。
        state: dict = {}

        def checkpoint(name: str, **data) -> None:
            state.update({key: _checkpoint_value(value) for key, value in data.items()})
            if checkpoint_sink is not None:
                checkpoint_sink({"stage": name, "question": question, "used": used,
                                 "phases": list(phases), **state})

        phases: list[dict] = list(resume.get("phases", []))
        used: int = int(resume.get("used", 0))
        # 标准模式硬预算：澄清(≤1)+综合(1)+修复(1)+冲突审计(1) ≤ 4 次；
        # 深度模式沿用原 14 次上限。预算在任何路径都不可被模型或用户放大。
        cap = self._cap if analysis_mode == "deep" else min(self._cap, 4)

        def phase(name: str, detail: str) -> None:
            item = {"phase": name, "detail": detail}
            phases.append(item)
            if event_sink is not None:
                event_sink(item)

        known = _known_elements(profile)
        if reached("clarified"):
            reply = resume["reply"]
        else:
            # 这些要素是结构化且可由本机可靠辨识的，不能把已有信息再交给模型
            # 随机决定是否“齐全”。这样既减少一次不必要的付费调用，也避免用户已
            # 明确给出约束后仍反复被追问。
            if _has_explicit_decision_elements(question):
                reply = ELEMENT_READY_MARK + "：目标、期限、仓位约束与风险承受已在本轮明确。"
            else:
                reply, used = await self._ask(
                    [self._sys(clarifier_system()), *self._history(history),
                     self._user(clarifier_user(question, known, len(history) // 2 + 1))], used,
                    cap=cap)
            phase("clarify", reply[:120])
            if ELEMENT_READY_MARK not in reply:
                return {"needs_clarification": True, "reply": reply, "phases": phases,
                        "llm_requests": used, "card": None, "advice_id": None}
            checkpoint("clarified", reply=reply)

        if reached("planned"):
            plan_tasks = resume["plan_tasks"]
        elif analysis_mode == "standard":
            # 标准模式只保留澄清、程序取证和综合，硬上限为 4 次模型调用。
            plan_tasks = "程序取证、候选策略匹配与公开证据核验；缺少候选策略时只输出等待或拒答。"
            phase("plan", "标准模式：跳过多分析师与辩论，保留证据综合。")
            checkpoint("planned", plan_tasks=plan_tasks)
        else:
            plan_tasks, used = await self._ask(
                [self._sys(planner_system()), self._user(
                    f"用户目标：{question}\n已知要素：{json.dumps(known, ensure_ascii=False)}")], used,
                cap=cap)
            phase("plan", plan_tasks[:160])
            checkpoint("planned", plan_tasks=plan_tasks)

        thscode, name = self._extract_target(question)
        if reached("fetched"):
            pack = [AdvisorEvidenceItem.model_validate(item) for item in resume["pack"]]
        else:
            pack = await self._fetch_evidence(thscode, name, question, allow_web_search)
            if not thscode:
                phase("fetch", "未识别到具体标的，进入条件分析模式。")
            else:
                phase("fetch", f"证据包 {len(pack)} 段（编号 {_pack_ids(pack)}）。")
            checkpoint("fetched", pack=pack)
        pack_text = _render_pack(pack)

        if reached("analyzed"):
            briefs = resume["briefs"]
            pack = [AdvisorEvidenceItem.model_validate(item) for item in resume["pack"]]
        elif analysis_mode == "standard":
            briefs = []
            phase("analyze", "标准模式：以程序证据直接综合。")
            checkpoint("analyzed", briefs=briefs, pack=pack)
        else:
            briefs, used = await self._analyze(thscode, name, pack_text, plan_tasks, used)
            pack.extend([_evidence({"id": f"B{i + 1}", "dimension": "brief", "title": "分析简报", "text": text[:BRIEF_CHARS],
                                    "thscode": thscode or None, "publishable": False})
                         for i, text in enumerate(briefs)])
            checkpoint("analyzed", briefs=briefs, pack=pack)

        if reached("debated"):
            debate_log = resume["debate_log"]
            pack = [AdvisorEvidenceItem.model_validate(item) for item in resume["pack"]]
        elif analysis_mode == "standard":
            debate_log = ""
            phase("debate", "标准模式：未运行多空辩论。")
            checkpoint("debated", debate_log=debate_log, pack=pack)
        else:
            crosscheck = ""
            if used + REQUESTS_CROSSCHECK + REQUESTS_RISK + REQUESTS_SYNTH <= cap:
                crosscheck, used = await self._ask([
                    self._sys("你是交叉验证员：找出各简报间相互矛盾或证据不足的断言，每条一行；没有则输出「未发现矛盾」。"),
                    self._user(f"证据包与简报：\n{_render_pack(pack)}")], used, cap=cap)
                phase("crosscheck", crosscheck[:160])

            debate_log = ""
            if used + REQUESTS_DEBATE + REQUESTS_RISK + REQUESTS_SYNTH <= cap:
                debate_log, used = await self._debate(pack, used, cap=cap)
                phase("debate", "多空辩论 2 轮完成。" if debate_log else "辩论预算不足，已降级。")
            else:
                phase("debate", "预算不足以支撑辩论，按方案降级为简报并列。")

            if debate_log:
                pack.append(_evidence({"id": "D1", "dimension": "debate", "title": "多空辩论纪要",
                                       "text": debate_log[:PACK_CHARS], "thscode": thscode or None,
                                       "publishable": False}))
            checkpoint("debated", debate_log=debate_log, pack=pack)

        if not reached("risked"):
            risk_memo = ""
            if analysis_mode != "standard" and used + REQUESTS_RISK + REQUESTS_SYNTH <= cap:
                risk_memo, used = await self._ask([
                    self._sys(risk_officer_system()),
                    self._user(f"证据包与简报：\n{_render_pack(pack)}\n请输出风控备忘（失效条件/观察哨）。")], used,
                    cap=cap)
                if risk_memo:
                    pack.append(_evidence({"id": "R1", "dimension": "risk", "title": "风控备忘",
                                           "text": risk_memo[:PACK_CHARS], "thscode": thscode or None,
                                           "publishable": False}))
                phase("risk", risk_memo[:120] or "风控备忘为空。")
            checkpoint("risked", pack=pack)

        base_rate_available = any(item.get("base_rate_available") for item in pack)
        calibration_warning = bool(self._ledger.calibration().get("warn_high"))
        candidate_refs = _strategy_refs(pack)
        if reached("synthesized"):
            card = SuggestionCard.model_validate(resume["card"])
            conflicts = _strategy_conflicts(pack, candidate_refs=candidate_refs)
        else:
            card, issues, used = await self._synthesize(question, known, pack, used,
                                                        base_rate_available=base_rate_available,
                                                        calibration_warning=calibration_warning,
                                                        strategy_refs=candidate_refs, cap=cap)
            # 程序校验（schema 与发布门禁）失败时只修复一次；修复后仍不通过则拒绝发布。
            first_failed = card is None or bool(issues)
            first_issue = issues[0][:80] if issues else ""
            if used < cap and first_failed:
                card, issues, used = await self._synthesize(question, known, pack, used, repair=issues,
                                                            base_rate_available=base_rate_available,
                                                            calibration_warning=calibration_warning,
                                                            strategy_refs=candidate_refs, cap=cap)
            if card is None or issues:
                # 模型没有通过公开证据、数字与策略引用门禁时，不能把一次格式/引用
                # 失误升级成用户侧“任务失败”。这里不放宽任何验证规则，也不把模型
                # 草稿原文发布；改由程序生成一个无方向、可追溯的拒答卡，让本轮完整
                # 取数、证据和拒答原因都能进入台账，供人工前向验证复盘。
                rejected = "；".join((issues or ["模型建议卡未通过结构校验。"])[:4])
                card = self._safe_abstain_card(thscode, name, question, pack, rejected)
                issues = validate_suggestion(card, evidence_index=evidence_index(pack, thscode=card.thscode),
                                             metrics=metric_index(pack), strategy_refs=candidate_refs)
                if issues:
                    raise AppError("suggestion_invalid",
                                   "程序拒答卡未通过校验：" + "；".join(issues[:3]), 502)
                phase("validate", "模型建议卡未通过门禁，已生成程序化拒答并保留证据。")
            else:
                # 程序化冲突检测不耗预算；标准模式在预算内最多做 1 次新上下文审计。
                phase("validate", "校验通过。" if not first_failed
                      else f"经一次修复通过（原问题：{first_issue}）。")
            # 程序化冲突检测不耗预算；标准模式在预算内最多做 1 次新上下文审计。
            conflicts = _strategy_conflicts(pack, candidate_refs=candidate_refs)
            if conflicts and card.direction != "abstain" and analysis_mode == "standard":
                if used < cap:
                    findings, used = await self._ask([
                        self._sys("你是冲突审计员：对照证据包独立复核给定冲突是否成立，引用证据编号，"
                                  "输出不超过三行结论；不得新增数字，不得修改结论方向。"),
                        self._user("待审计冲突：\n- " + "\n- ".join(conflicts)
                                   + f"\n\n证据包：\n{_render_pack(pack)}")],
                        used, cap=cap)
                    phase("audit", findings[:120] or "冲突审计无输出。")
                else:
                    phase("audit", "检测到策略冲突但审计预算已用尽，直接按服务器规则降级。")
            checkpoint("synthesized", card=card.model_dump())

        # 双轨独立结算的前提：**门禁改写之前的**原始 LLM 裁决必须留档。
        # `_enrich_card()` 会把"候选为空/存在冲突"改写成 wait 并压低把握度，
        # 用改写后的卡片做"AI 轨"等于拿被治理层修剪过的结果去评价治理层。
        raw_verdict = card.model_dump()
        card = self._enrich_card(card, pack, conflicts=conflicts)
        evidence = _public_evidence(pack)
        if reached("registered"):
            advice_id = resume["advice_id"]
        else:
            entry = self._ledger.register(
                card.model_dump(), session_id=session_id, evidence=evidence,
                model_profile_id=getattr(self._model, "profile_id", None),
                raw_verdict=raw_verdict)
            advice_id = entry["id"]
            checkpoint("registered", advice_id=advice_id)
        research_prediction_id = None
        if self._research_predictions is not None:
            if reached("research_sealed"):
                research_prediction_id = resume.get("research_prediction_id")
            else:
                research_prediction_id, detail = self._seal_research_prediction(
                    card, evidence=evidence, advice_id=advice_id)
                checkpoint("research_sealed", advice_id=advice_id,
                           research_prediction_id=research_prediction_id)
                phase("research_prediction", detail)
        narrative = card.decision_narrative
        research_suffix = (" 本次方向研究预测已独立封存，可在「研究预测封存」中结算与核验。"
                           if research_prediction_id else "")
        reply = (f"{narrative}\n\n跟踪与反思：该建议已登记台账（编号 {advice_id[:8]}），"
                 f"可按失效条件跟踪；研究参考，不构成投资建议。{research_suffix}")
        if not reached("registered"):
            phase("ledger", f"已登记：{card.direction} / {card.confidence}。")
        return {"needs_clarification": False, "reply": reply, "phases": phases,
                "llm_requests": used,
                "card": card.model_dump(), "advice_id": advice_id,
                "research_prediction_id": research_prediction_id, "evidence": evidence}

    # -- LLM 基元 ------------------------------------------------------------
    def _sys(self, text: str) -> dict:
        return {"role": "system", "content": text}

    def _user(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def _history(self, history: list[dict]) -> list[dict]:
        turns = []
        for item in history[-8:]:
            role = "user" if item.get("role") == "user" else "assistant"
            content = str(item.get("content") or "")[:1500]
            if content:
                turns.append({"role": role, "content": content})
        return turns

    async def _ask(self, messages: list[dict], used: int, *, cap: int | None = None) -> tuple[str, int]:
        limit = self._cap if cap is None else cap
        if used >= limit:
            raise AppError("budget_exceeded", "本条建议的模型请求预算已用尽，请稍后重试或简化问题。", 429)
        try:
            async with asyncio.timeout(self._request_timeout_seconds):
                result = await self._model.turn(messages)
        except TimeoutError:
            raise AppError("advisor_model_timeout",
                           f"单个模型分析超过 {self._request_timeout_seconds:g} 秒，已停止；未自动重试。",
                           504, True) from None
        if result.error:
            raise AppError("model_failed", result.error.get("message") or "模型请求失败。", 502)
        return (result.content or "").strip(), used + 1

    # -- 节点：四分析师 -------------------------------------------------------
    async def _analyze(self, thscode: str, name: str, pack_text: str, plan_tasks: str,
                       used: int, *, cap: int | None = None) -> tuple[list[str], int]:
        limit = self._cap if cap is None else cap
        roles = ("fundamental", "technical", "news", "capital")
        briefs: list[str] = []
        for role in roles:
            if used >= limit:
                briefs.append(f"{role} 维度分析未执行；本条建议的模型请求预算已用尽。")
                continue
            try:
                text, used = await self._ask([
                    self._sys(analyst_system(role)),
                    self._user(analyst_user(role, thscode or "未确认", name or "未确认", pack_text, plan_tasks))],
                    used, cap=limit)
                briefs.append(text)
            except AppError:
                # 上游已有可能收到请求，失败的尝试也占用一次预算，避免超限重试。
                used += 1
                briefs.append(f"{role} 维度分析请求失败；该维度按证据缺失处理。")
        return briefs, used

    # -- 节点：辩论 -----------------------------------------------------------
    async def _debate(self, pack: list[dict], used: int, *, cap: int | None = None) -> tuple[str, int]:
        limit = self._cap if cap is None else cap
        material = _render_pack(pack)
        transcript = ""
        for round_no in (1, 2):
            for stance, target in (("多", "空"), ("空", "多")):
                if used >= limit - (REQUESTS_RISK + REQUESTS_SYNTH):
                    return transcript, used
                text, used = await self._ask([
                    self._sys(debater_system()),
                    self._user(f"立场：{stance}方（第 {round_no} 轮）。攻击{target}方最弱论点，每条论点引用证据编号。\n材料：\n{material}\n既有辩论：\n{transcript or '（开场）'}")],
                    used, cap=limit)
                transcript += f"【第{round_no}轮·{stance}方】{text}\n"
        return transcript, used

    # -- 节点：综合与校验 -----------------------------------------------------
    async def _synthesize(self, question: str, known: dict, pack: list[dict], used: int,
                          *, repair: list[str] | None = None, base_rate_available: bool = False,
                          calibration_warning: bool = False,
                          strategy_refs: set[str] | None = None,
                          cap: int | None = None) -> tuple[SuggestionCard | None, list[str], int]:
        issues_text = ""
        if repair:
            issues_text = "\n上一次输出未通过校验，必须修复以下问题：\n- " + "\n- ".join(repair)
        text, used = await self._ask([
            self._sys(synthesizer_system(base_rate_available=base_rate_available,
                                         calibration_warning=calibration_warning)),
            self._user(f"用户目标：{question}\n已知要素：{json.dumps(known, ensure_ascii=False)}\n"
                       f"证据包：\n{_render_pack(pack)}{issues_text}\n"
                       "只输出一个 JSON 对象（SuggestionCard），不要代码围栏。")], used, cap=cap)
        try:
            obj, _cleanups = parse_section_object(text)
            card = SuggestionCard.model_validate(obj)
        except JsonRejected as error:
            return None, [error.message[:200]], used
        except ValidationError as error:
            # errors() 中的 input 可能是模型原文，绝不带入后续提示或日志；
            # 只传字段路径与规则，令唯一一次修复有可执行目标。
            issues = []
            for item in error.errors()[:6]:
                location = ".".join(str(part) for part in item.get("loc", ())) or "根对象"
                issues.append(f"{location}：{item.get('msg', '字段不符合结构约束')}")
            return None, ["；".join(issues)[:600]], used
        except ValueError as error:
            return None, [str(error).split("\n")[0][:200]], used
        try:
            public_index = evidence_index(pack, thscode=card.thscode)
            metrics = metric_index(pack)
        except ValueError as error:
            return None, [str(error)], used
        issues = validate_suggestion(card, evidence_index=public_index, metrics=metrics,
                                     strategy_refs=strategy_refs)
        issues.extend(_research_prediction_issues(card, base_rate_available=base_rate_available))
        if (not base_rate_available
                and any(item.probability_source == "base_rate" for item in card.scenarios)):
            issues.append("本次基率样本未达标，三情景概率来源必须使用 subjective。")
        return card, issues, used

    def _seal_research_prediction(self, card: SuggestionCard, *, evidence: list[dict],
                                  advice_id: str) -> tuple[str | None, str]:
        """把已通过顾问门禁的独立方向预测封存；失败绝不回滚执行建议台账。

        card 的 bull/base/bear 是幅度情景，不能直接映射为上涨/震荡/下跌事件；
        因而只读取综合者显式给出的 research_prediction。重复恢复时按 advice_id
        取回既有自动封存条目，避免因进程中断重复记样本。
        """
        if card.direction == "abstain" or card.research_prediction is None:
            return None, "未形成可封存的方向研究预测；保持观察/拒答，不硬凑样本。"
        existing = self._research_predictions.find_advisor_research(advice_id)
        if existing is not None:
            return existing["id"], "已恢复既有研究预测封存记录。"
        evidence_by_id = {str(item.get("id")): item for item in evidence}
        counter_snapshot = []
        for evidence_id in card.counter_evidence:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                logger.warning("advisor research prediction skipped: missing counter evidence %s", evidence_id)
                return None, "反方证据快照不完整，未自动封存研究预测。"
            text = str(item.get("text") or "").strip()
            if not text:
                return None, "反方证据内容为空，未自动封存研究预测。"
            counter_snapshot.append(f"{item.get('title') or evidence_id}：{text[:900]}")
        forecast = card.research_prediction
        payload = {
            "thscode": card.thscode,
            "name": card.name,
            "as_of_date": _base_cutoff_date(_now_iso()),
            "horizon": card.horizon,
            "p_up": forecast.p_up,
            "p_flat": forecast.p_flat,
            "p_down": forecast.p_down,
            "probability_source": forecast.probability_source,
            "rationale": card.decision_narrative,
            "counter_evidence": counter_snapshot,
            "invalidations": list(card.invalidations),
            "evidence_snapshot": list(evidence),
            "return_threshold_pct": forecast.return_threshold_pct,
            "model_profile_id": getattr(self._model, "profile_id", None),
            "prompt_version": "advisor-research-v1",
            "source": "advisor_research",
            "linked_advice_id": advice_id,
        }
        try:
            entry = self._research_predictions.seal(payload)
        except (AppError, ValueError):
            logger.exception("advisor research prediction seal failed for advice_id=%s", advice_id)
            return None, "研究预测封存未完成；执行建议已独立登记，未将其伪装为已封存。"
        return entry["id"], "方向研究预测已自动封存；不改变执行建议档位。"

    # -- 程序取数（不耗预算） --------------------------------------------------
    def _extract_target(self, question: str) -> tuple[str, str]:
        import re
        match = re.search(r"([0-9]{6})\.(SH|SZ|BJ)|(?<![0-9])([0-9]{6})(?![0-9])", question)
        if not match:
            return "", ""
        code = match.group(1) or match.group(3)
        suffix = match.group(2)
        if suffix is None:
            suffix = "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("4", "8")) else "SZ")
        thscode = f"{code}.{suffix}"
        name = ""
        if self._market is not None:
            try:
                identity = self._market.identity(thscode)
                name = str(getattr(identity, "name", "") or "") if identity is not None else ""
            except (AttributeError, TypeError, ValueError):
                name = ""
        return thscode, name

    async def _fetch_evidence(self, thscode: str, name: str, question: str, allow_web_search: bool) -> list[AdvisorEvidenceItem]:
        pack: list[AdvisorEvidenceItem] = []

        async def safe(dim: str, title: str, coro_factory):
            # 取数器可返回 str 或 (str, sources)；sources 是可追溯的来源引用。
            try:
                result = await coro_factory()
            except AppError as error:
                result = f"本维数据暂缺：{error.message}"
            except (AttributeError, TypeError, KeyError, ValueError):
                logger.warning("advisor fetch failed dim=%s", dim, exc_info=True)
                result = "本维数据暂缺：取数异常。"
            if isinstance(result, tuple) and len(result) == 3:
                text, sources, metrics = result
            elif isinstance(result, tuple):
                text, sources = result
                metrics = []
            else:
                text, sources, metrics = result, [], []
            pack.append(_evidence({"id": f"{dim[0].upper()}1", "dimension": dim, "title": title,
                                   "text": text[:PACK_CHARS], "thscode": thscode or None,
                                   "sources": sources, "metrics": metrics}))

        tasks = []
        if thscode and self._market is not None:
            tasks.append(safe("fund", "基本面证据包", lambda: self._fundamental_pack(thscode)))
            tasks.append(safe("tech", "技术面证据包", lambda: self._technical_pack(thscode)))
            tasks.append(safe("cap", "资金与情绪证据包", lambda: self._capital_pack(thscode)))
        elif thscode:
            # 隔离探针、离线部署等没有行情服务时，缺口是预期状态，不记录误导性的异常堆栈。
            pack.extend([_evidence({"id": item_id, "dimension": dimension, "title": title,
                                    "text": "本维数据暂缺：未接入行情服务。", "thscode": thscode})
                         for item_id, dimension, title in (
                             ("F1", "fund", "基本面证据包"), ("T1", "tech", "技术面证据包"),
                             ("C1", "cap", "资金与情绪证据包"))])
        tasks.append(safe("news", "新闻与外部线索", lambda: self._news_pack(thscode, allow_web_search, name=name)))
        if self._knowledge is not None and thscode:
            tasks.append(safe("know", "私有知识库佐证",
                              lambda: self._knowledge_pack(thscode, question=question)))
        await asyncio.gather(*tasks)
        if thscode:
            try:
                base_text, base_available, closes, return_closes = await self._base_rate_pack(thscode)
            except (AttributeError, TypeError, ValueError):
                logger.warning("advisor base rate failed", exc_info=True)
                base_text, base_available, closes, return_closes = (
                    "历史状态基率暂缺：本机行情仓库不可用。", False, [], [])
            pack.append(_evidence({"id": "A1", "dimension": "algorithm", "title": "历史状态基率",
                                   "text": base_text[:PACK_CHARS], "thscode": thscode,
                                   "base_rate_available": base_available,
                                   "algorithm_data": {"closes": closes, "return_closes": return_closes},
                                   "metrics": _base_rate_metrics(base_text, base_available),
                                   "sources": [{"kind": "market",
                                                "label": "同花顺历史日 K / 本机行情仓库：20 日动量 × 波动状态历史基率"}]}))
            horizon = infer_horizon(question)
            candidate_experiments = self._candidate_strategy_experiments(thscode, horizon)
            strategy_item = self._strategy_pack(thscode, candidate_experiments)
            if strategy_item is not None:
                pack.append(strategy_item)
            # 程序计算的价格状态与期限标签进入策略知识检索语境，不再只用证券代码。
            strategy_knowledge = await self._strategy_knowledge_pack(
                thscode, question=question,
                strategy_ids=[item["strategy_id"] for item in candidate_experiments],
                horizon=horizon, market_regime=_infer_market_regime(return_closes or closes))
            if strategy_knowledge is not None:
                pack.append(strategy_knowledge)
        return pack

    async def _fundamental_pack(self, thscode: str) -> str:
        lines: list[str] = []
        metrics: list[dict] = []
        statements = await self._market.fetch_all_statements(thscode, period="annual")
        by_period: dict[str, dict] = {}
        for result in (statements if isinstance(statements, list) else [statements]):
            scope = getattr(result, "requested_scope", None)
            domain = getattr(scope, "capability", "") or ""
            records = list(getattr(result, "records", []) or [])[:2]
            total = len(list(getattr(result, "records", []) or []))
            lines.append(f"[{domain}] 共 {total} 期，最近样例：")
            for record in records:
                fields = _record_values(record)
                period_key = _record_period(record)
                by_period.setdefault(period_key, {}).update(fields)
                metrics.extend(_field_metrics("F1", period_key, fields))
                lines.append(json.dumps({"period": period_key, **fields}, ensure_ascii=False)[:700])
        periods = sorted(by_period, reverse=True)[:2]
        indicators = await asyncio.gather(*[
            self._market.fetch_indicators(thscode, report=f"{period[:4]}-4") for period in periods
        ], return_exceptions=True)
        for period, result in zip(periods, indicators):
            if isinstance(result, Exception):
                lines.append(f"[指标 {period}] 暂缺：{getattr(result, 'message', '取数失败')}")
                continue
            records = list(getattr(result, "records", []) or [])
            if records:
                by_period[period].update(_record_values(records[0]))
        if periods:
            current = by_period[periods[0]]
            previous = by_period[periods[1]] if len(periods) > 1 else {}
            lines.append("[F-Score] " + _format_f_score(calculate_f_score(current, previous)))
            lines.append("[Z-Score] " + _format_z_score(calculate_z_score(current)))
        valuations = await self._market.fetch_valuations([thscode])
        for record in list(getattr(valuations, "records", []) or [])[:2]:
            fields = _record_values(record)
            lines.append("[估值] " + json.dumps({"period": _record_period(record), **fields}, ensure_ascii=False)[:500])
        return ("\n".join(lines) if lines else "基本面数据暂缺。",
                [{"kind": "market", "label": "同花顺 F10：年报三表、主要指标与估值"}], metrics)

    async def _base_rate_pack(self, thscode: str) -> tuple[str, bool, list[float], list[float]]:
        """返回 (说明文字, 基率是否可用, 价格水平序列, 总收益口径序列)。

        第一个序列是未复权/前复权收盘价，供当前价与情景 EV 使用；第二个序列
        是公司行动总收益指数，供状态基率、波动率与市场状态标签使用。除权日
        在未复权价上会机械性下跌，用错序列会把真实正收益记成负收益。
        """
        as_of_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows: list[dict] = []
        return_rows: list[dict] = []
        price_basis = BASIS_CORPORATE_ACTION_TOTAL_RETURN
        if self._warehouse is not None:
            # ex_date_ms 必须在仓库查询层就被 as-of 截断：adjustment_factors
            # 里含已公告但尚未发生的未来除权日，越过 as-of 即未来信息泄漏。
            history = await asyncio.to_thread(
                self._warehouse.repository.corporate_action_total_return_history,
                thscode, 0, as_of_ms)
            converted = total_return_closes(history.get("items") or [], as_of_ms=as_of_ms)
            rows = converted["raw_closes"]
            return_rows = converted["closes"]
        # 全市场仓库尚未同步时，直接回退到同花顺该标的历史日 K；不能让一个
        # 可选的批量仓库成为单标的顾问分析的硬阻塞点。
        if len(rows) < 81 and self._market is not None:
            history = await self._market.fetch_price_history(
                thscode, start_ms=as_of_ms - 6 * 366 * 86_400_000, end_ms=as_of_ms)
            rows = _base_rate_rows(getattr(history, "records", []) or [])
            # 数据源返回的已是前复权序列，不需要（也无法）再叠加公司行动。
            return_rows = []
            price_basis = BASIS_FORWARD_ADJUSTED_SOURCE
        if not rows:
            return ("历史状态基率暂缺：同花顺历史日 K 与本机行情仓库均无可用记录。",
                    False, [], [])
        result = calculate_base_rate(rows, return_rows=return_rows or None,
                                     price_basis=price_basis, as_of_ms=as_of_ms)
        forward_20 = result["forward_20"]
        forward_60 = result["forward_60"]
        closes = [float(row["close_price"]) for row in rows]
        return_closes = [float(row["close_price"]) for row in (return_rows or rows)]
        if not result["available"]:
            return (f"历史状态基率不可用：20 日样本数 {forward_20['sample_count']}，少于 30；{result['note']}",
                    False, closes, return_closes)
        text = (f"历史状态基率（{result['state_label']}）：后续 20 日样本 {forward_20['sample_count']}，"
                f"中位收益 {forward_20['median_return_pct']}%，胜率 {forward_20['win_rate'] * 100:.1f}%；"
                f"后续 60 日样本 {forward_60['sample_count']}，中位收益 {forward_60['median_return_pct']}%，"
                f"胜率 {forward_60['win_rate'] * 100:.1f}%。{result['note']}")
        return text, True, closes, return_closes

    def _enrich_card(self, card: SuggestionCard, pack: list[dict],
                     *, conflicts: list[str] | None = None) -> SuggestionCard:
        algorithm = next((item.get("algorithm_data", {}) for item in pack if item.get("id") == "A1"), {})
        closes = algorithm.get("closes", [])
        # 收益类计算一律用公司行动总收益口径：未复权价在除权日会机械性下跌，
        # 用错序列会同时污染基率胜率与波动率目标仓位。价格水平类计算（情景 EV
        # 的当前价）仍用原始收盘价。
        return_closes = algorithm.get("return_closes") or closes
        ev = calculate_scenario_ev([scenario.model_dump() for scenario in card.scenarios],
                                   current_price=closes[-1] if closes else None)
        position = calculate_volatility_target(return_closes)
        base = next((item for item in pack if item.get("id") == "A1"), {})
        base_details = calculate_base_rate([{"date_ms": index + 1, "close_price": close}
                                            for index, close in enumerate(return_closes)]) if return_closes else {}
        distribution = base_details.get("forward_20", {})
        kelly = calculate_half_kelly(
            probability_source="base_rate" if base.get("base_rate_available") else "subjective",
            sample_count=distribution.get("sample_count", 0), win_rate=distribution.get("win_rate"),
            average_win_pct=distribution.get("average_win_pct"),
            average_loss_pct=distribution.get("average_loss_pct"))
        position_text = position["note"]
        if position.get("weight") is not None:
            position_text += f" 波动率目标结果约为 {position['weight'] * 100:.1f}%。"
        position_text += " " + kelly["note"]
        if kelly.get("range") is not None:
            low, high = kelly["range"]
            position_text += f" half-Kelly 教育性区间约为 {low * 100:.1f}%–{high * 100:.1f}%。"
        ev_summary = None
        if ev is not None:
            ev_summary = EvSummary.model_validate({key: ev[key] for key in (
                "expected_return_pct", "margin_of_safety_pct", "sensitivity", "note")})
        candidates = [item for item in pack if item.get("id") == "S1"]
        candidate_refs = [source.ref for item in candidates for source in item.sources if source.ref]
        validated_refs = [source.ref for item in candidates for source in item.sources
                          if source.ref and source.strategy_review_decision == "validated"]
        conflicts = conflicts or []
        basis = {**card.confidence_basis,
                 "evidence_quality": "partial" if not base.get("base_rate_available") else "sufficient",
                 "strategy_grade": "candidate" if candidate_refs else "none",
                 "strategy_validation": "validated" if validated_refs else "not_available",
                 "regime_match": "not_verified",
                 "forward_calibration": "not_available",
                 "unresolved_conflicts": bool(conflicts),
                 "base_rate_available": bool(base.get("base_rate_available")),
                 "candidate_experiments": candidate_refs,
                 "final_level": "very_low" if (conflicts or not candidate_refs) else "medium"}
        update = {
            "ev_summary": ev_summary,
            "position_thinking": position_text,
            "confidence_basis": basis,
        }
        narrative = card.decision_narrative[:1800]
        if conflicts and card.direction != "abstain":
            update.update({"direction": "wait", "confidence": "very_low",
                           "decision_narrative": narrative
                           + " 服务器检测到未解决的策略冲突：" + "；".join(conflicts[:2])
                           + "，已按规则降级为等待并标注很低把握度。"})
        elif conflicts:
            # 拒答结构保留：只降把握度，不把 abstain 改写成 wait。
            update["confidence"] = "very_low"
        elif not candidates and card.direction != "abstain":
            update.update({"direction": "wait", "confidence": "very_low",
                           "strategy_refs": [],
                           "decision_narrative": narrative + " 当前没有通过候选门禁的本地策略实验，服务器将结论降为等待；请先完成可复现实验。"})
        elif not candidates:
            # 拒答不是等待：它不应被改写为带空三情景的 wait 卡，也不应凭空产生方向。
            update.update({"confidence": "very_low", "strategy_refs": [],
                           "decision_narrative": narrative
                           + " 当前没有通过候选门禁的本地策略实验，维持拒答并等待可复现实验。"})
        else:
            # 04 前向影子验证尚未完成，历史候选的最高档只能到 medium。
            update["confidence"] = "medium"
        return card.model_copy(update=update)

    @staticmethod
    def _safe_abstain_card(thscode: str, name: str, question: str,
                           pack: list[AdvisorEvidenceItem], rejected: str) -> SuggestionCard:
        """模型建议卡被拒收后的确定性安全输出。

        此卡只陈述本轮已取到的证据与尚缺的验证条件，不输出价格方向、概率或预测。
        因而既可通过同一发布门禁，也不会将模型的未验证内容写入台账。
        """
        refs = {item.id for item in pack if item.publishable}
        core = [ref for ref in ("F1", "T1", "A1") if ref in refs]
        counter = "A1" if "A1" in refs else (core[0] if core else next(iter(refs), "K1"))
        horizon = infer_horizon(question) or "custom"
        display_name = name or thscode
        return SuggestionCard.model_validate({
            "schema_version": "finsight.suggestion.v2",
            "thscode": thscode,
            "name": display_name,
            "direction": "abstain",
            "horizon": horizon,
            "scenarios": [],
            "confidence": "very_low",
            "claims": [
                {"kind": "fact", "text": "本轮已取得结构化基本面、行情技术面与历史状态证据。",
                 "evidence_refs": core, "metric_refs": [], "strategy_refs": []},
                {"kind": "fact", "text": "当前本地策略实验没有满足候选门禁的可用结果。",
                 "evidence_refs": [counter], "metric_refs": [], "strategy_refs": []},
                {"kind": "inference", "text": "在策略可复现性与前向校准尚未完成时，不能将现有信息转化为可执行的方向判断。",
                 "evidence_refs": [], "metric_refs": [], "strategy_refs": []},
            ],
            "key_evidence": core,
            "counter_evidence": [counter],
            "strategy_refs": [],
            "confidence_basis": {"model_card_rejected": True, "strategy_grade": "none"},
            "invalidations": ["完成满足候选门禁的可复现实验，并确认其适用条件。"],
            "watchpoints": ["后续实验记录出现可审计的策略信号与对应的真实结果。"],
            "missing_for_decision": ["通过候选门禁且与当前期限匹配的策略实验。",
                                     "足以覆盖当前市场状态的人工前向验证样本。",
                                     f"模型建议卡被拒收的校验原因：{rejected[:160]}"],
            "decision_narrative": (
                f"本轮已经取得{display_name}的结构化基本面、行情技术面和历史状态证据，"
                "因此并非因为数据来源缺失而停止。排除方向判断的原因是，本地尚无通过候选门禁、"
                "且与本轮研究期限匹配的策略实验；模型生成的建议卡也未能通过事实数字与引用校验。"
                "在缺少可复现策略和前向校准的情况下，继续给出上涨或下跌结论会制造不可验证的确定性。"
                "本次仅保留证据、拒答原因和后续观察条件；当可审计实验与真实结果形成足够样本后，"
                "再重新进行方向判断。"
            ),
            "disclaimer_accepted": True,
        })

    def _candidate_strategy_experiments(self, thscode: str, horizon: str | None) -> list[dict]:
        if self._strategy is None:
            return []
        try:
            experiments = self._strategy.list()
        except (AppError, AttributeError, TypeError, ValueError):
            return []
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        candidates = []
        for item in experiments:
            if item.get("thscode") != thscode or item.get("validation_grade") != "candidate":
                continue
            # 回测时序、成本或验证口径升级后，旧版本实验不得继续作为当前顾问
            # 的候选依据；它们仍保留在实验历史中供审计。
            try:
                if str(item.get("strategy_version") or "") != get_spec(str(item.get("strategy_id") or "")).version:
                    continue
            except (KeyError, ValueError):
                continue
            if not _strategy_matches_horizon(str(item.get("strategy_id") or ""), horizon):
                continue
            as_of = (item.get("result") or {}).get("signal_snapshot", {}).get("as_of")
            try:
                signal_date = date.fromisoformat(str(as_of))
            except ValueError:
                continue
            if signal_date > today or (today - signal_date).days > 7:
                continue
            try:
                item = {**item, "review_decision": self._strategy.review_decision(
                    str(item["strategy_id"]), str(item["strategy_version"]), _horizon_days(horizon)
                )}
            except (AppError, AttributeError, TypeError, ValueError):
                item = {**item, "review_decision": None}
            candidates.append(item)
        return candidates[:3]

    def _strategy_pack(self, thscode: str, candidates: list[dict]) -> AdvisorEvidenceItem | None:
        if not candidates:
            return None
        lines, sources = [], []
        for item in candidates:
            ref = f"{item['strategy_id']}@{item['strategy_version']}#{item['id']}"
            result = item.get("result", {})
            metrics = result.get("metrics", {})
            cost = result.get("spec_snapshot", {}).get("cost_basis") or "单边成本假设"
            lines.append(f"{ref}：候选策略（成本口径：{cost}）；"
                         f"累计收益 {metrics.get('cumulative_return_pct')}%，"
                         f"超额 {metrics.get('excess_return_pct')}%，最大回撤 {metrics.get('max_drawdown_pct')}%。")
            sources.append({"kind": "strategy", "label": f"本地策略实验：{ref}", "ref": ref,
                            "strategy_review_decision": item.get("review_decision")})
        return _evidence({"id": "S1", "dimension": "strategy", "title": "候选策略实验",
                          "text": "\n".join(lines), "thscode": thscode, "sources": sources})

    async def _strategy_knowledge_pack(self, thscode: str, *, question: str, strategy_ids: list[str],
                                       horizon: str | None = None,
                                       market_regime: str | None = None) -> AdvisorEvidenceItem | None:
        """仅检索已审核的策略知识；假设、未审核和退役文档不得进入顾问证据。"""
        if self._knowledge is None:
            return None
        try:
            context = build_strategy_query(question=question, thscode=thscode, horizon=horizon,
                                           strategy_ids=strategy_ids, market_regime=market_regime)
            result = await self._knowledge.search(
                context["query"], top_k=6,
                knowledge_roles=["strategy"], review_states=["approved"],
                horizon=context["horizon"], strategy_family=context["strategy_family"],
                market_regime=context["market_regime"])
        except (AppError, AttributeError, TypeError, ValueError):
            return None
        hits = getattr(result, "hits", []) or []
        if not hits:
            return None
        sources = [{"kind": "kb", "label": f"已审核策略知识：{hit.filename}"
                    + (f"·{hit.heading}" if hit.heading else ""), "ref": hit.document_id,
                    "page": hit.page} for hit in hits]
        return _evidence({"id": "S2", "dimension": "strategy_knowledge", "title": "已审核策略知识",
                          "text": "\n".join(f"[策略知识] {hit.filename}：{hit.excerpt[:160]}" for hit in hits),
                          "thscode": thscode, "sources": sources})

    async def _technical_pack(self, thscode: str) -> str:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - 400 * 86_400_000
        result = await self._market.fetch_price_history(thscode, start_ms=start_ms, end_ms=end_ms)
        bars = []
        for record in getattr(result, "records", []) or []:
            fields = _record_fields(record)
            close = fields.get("close_price") or fields.get("close")
            close_value = _field_value(close)
            if close_value is None:
                continue
            bars.append((_record_period(record)[:10], float(close_value)))
        if not bars:
            return "本地无日 K 数据；技术面按证据缺失处理。"
        closes = [price for _, price in bars]
        latest_date, latest = bars[-1]
        change_20 = (closes[-1] - closes[min(-21, -len(closes))]) / closes[min(-21, -len(closes))] * 100.0 if len(closes) > 20 else None
        high = max(closes)
        low = min(closes)
        summary = [
            f"最新收盘（{latest_date}）：{latest}",
            f"区间最高/最低：{high} / {low}（现价位于区间 {(latest - low) / (high - low) * 100:.0f}% 分位）" if high > low else f"区间最高/最低：{high} / {low}",
        ]
        if change_20 is not None:
            summary.append(f"近 20 根交易日涨跌：{change_20:+.1f}%")
        summary.append("最近 {} 根日 K：".format(min(SNAPSHOT_BARS, len(bars))))
        for bar_date, price in bars[-SNAPSHOT_BARS:]:
            summary.append(f"  {bar_date} 收 {price}")
        metrics = [{"name": "T1.latest_close", "value": str(latest), "unit": "CNY/share", "evidence_refs": ["T1"]}]
        if change_20 is not None:
            metrics.append({"name": "T1.change_20d", "value": str(change_20 / 100), "unit": "ratio", "evidence_refs": ["T1"]})
        return "\n".join(summary), [{"kind": "market", "label": "同花顺日 K（近 400 交易日收盘）"}], metrics

    async def _capital_pack(self, thscode: str) -> str:
        from datetime import datetime, timezone
        today_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        recent = await self._market.fetch_price_history(
            thscode, start_ms=today_ms - 14 * 86_400_000, end_ms=today_ms)
        periods = [_record_period(record)[:10] for record in getattr(recent, "records", []) or []]
        latest_market_day = max((period for period in periods if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period)), default="")
        query_ms = today_ms
        if latest_market_day:
            query_ms = int(datetime.combine(date.fromisoformat(latest_market_day), datetime.min.time(), timezone.utc).timestamp() * 1000)
        result = await self._market.fetch_anomaly_analysis(thscode, date_ms=query_ms)
        lines = []
        metrics: list[dict] = []
        for record in list(getattr(result, "records", []) or [])[:3]:
            fields = _record_values(record)
            lines.append(json.dumps(fields, ensure_ascii=False)[:600])
            metrics.extend(_field_metrics("C1", _record_period(record), fields))
        if getattr(result, "gaps", None):
            lines.append("资金面缺口：" + "；".join(gap.reason for gap in result.gaps[:3]))
        return ("\n".join(lines) if lines else
                "同花顺当日异动接口本次未返回该标的匹配记录；这不代表不存在资金或市场事件。",
                [{"kind": "market", "label": "同花顺当日个股异动原因"}], metrics)

    async def _news_pack(self, thscode: str, allow_web_search: bool, name: str = "") -> tuple[str, list[dict]]:
        lines: list[str] = []
        sources: list[dict] = []
        if self._rss is not None and thscode:
            try:
                items, _total = self._rss.list_items(thscode=thscode, limit=5)
                for item in items:
                    title = str(getattr(item, "title", "") or "")
                    feed = str(getattr(item, "feed_name", "") or "")
                    lines.append(f"[RSS 线索] {feed}：{title}；{str(getattr(item, 'summary', '') or '')[:160]}")
                    sources.append({"kind": "rss", "label": f"RSS《{title}》· {feed}",
                                    "ref": str(getattr(item, "link", "") or "") or None})
            except (AttributeError, TypeError, KeyError, ValueError):
                lines.append("RSS 本地订阅暂不可读取。")
        if allow_web_search and self._web is not None:
            try:
                request = WebSearchRequest(query=f"{name} {thscode} 最新公告 财报 新闻".strip(), count=8)
                search = await self._web.search("bocha", request)
                ticker = thscode.split(".", 1)[0]
                relevant = [item for item in search.items
                            if ticker in f"{item.title} {item.snippet}"
                            or (name and name in f"{item.title} {item.snippet}")]
                for item in relevant[:5]:
                    lines.append(f"[线索] {item.title}（{item.domain}）：{(item.snippet or '')[:160]}")
                    sources.append({"kind": "web", "label": f"博查：{item.title}（{item.domain}）",
                                    "ref": item.url})
                if not relevant:
                    lines.append("联网搜索未返回可由标的代码或名称核验的相关结果。")
            except AppError as error:
                lines.append(f"搜索不可用：{error.message}")
            except (AttributeError, TypeError, KeyError, ValueError):
                lines.append("搜索不可用：服务异常。")
        elif not lines:
            lines.append("未启用联网搜索，且本地 RSS 暂无该标的线索。")
        return "\n".join(lines), sources

    async def _knowledge_pack(self, thscode: str, *, question: str = "") -> str:
        """来源佐证检索：查询必须包含用户问题，不再只用证券代码；退休文档不可命中。"""
        cleaned = re.sub(r"\s+", " ", question or "").strip()[:160]
        query = " ".join(item for item in (thscode, cleaned) if item)
        try:
            result = await self._knowledge.search(query, top_k=4,
                                                  review_states=["approved", "unreviewed"])
        except AppError:
            return "知识库暂无命中。"
        hits = getattr(result, "hits", []) or []
        if not hits:
            return "知识库暂无命中。"
        sources = [{"kind": "kb",
                    "label": (f"知识库：{hit.filename}"
                              + (f"·{hit.heading}" if hit.heading else "")
                              + (f" 第 {hit.page} 页" if hit.page else "")),
                    "ref": hit.document_id, "page": hit.page}
                   for hit in hits]
        return "\n".join(
            f"[佐证] {hit.filename}（{hit.heading or '无标题'} / 第 {hit.page} 页）：{hit.excerpt[:160]}"
            for hit in hits), sources


def _known_elements(profile: dict) -> dict:
    elements: dict[str, str] = {}
    appetite = profile.get("risk_appetite") or "balanced"
    elements["用户风险偏好"] = {"conservative": "稳健", "balanced": "平衡", "aggressive": "进取"}.get(appetite, appetite)
    domains = profile.get("focus_domains") or []
    if domains:
        elements["关注领域"] = "、".join(str(item) for item in domains[:5])
    excluded = profile.get("excluded_thscodes") or []
    if excluded:
        elements["禁入标的"] = "、".join(str(item) for item in excluded[:10])
    return elements


def _has_explicit_decision_elements(question: str) -> bool:
    """判断本轮是否已写明澄清器要求的四类要素。

    只做保守的关键词与代码识别：任何一类没有明确出现时仍交给澄清器追问，
    不猜测用户意图，也不从会话历史补造个人约束。
    """
    text = question.lower()
    has_target = bool(re.search(r"(?<![0-9])[0-9]{6}(?:\.(?:sh|sz|bj))?(?![0-9])", text))
    has_goal = any(word in text for word in ("目标", "收益", "避险", "研究", "验证"))
    has_horizon = any(word in text for word in ("交易日", "天", "周", "月", "季度", "年", "期限", "短期", "中期", "长期"))
    has_position = any(word in text for word in ("仓位", "持仓", "建仓", "不超过", "上限"))
    has_risk = any(word in text for word in ("风险", "回撤", "止损", "亏损", "承受"))
    return has_target and has_goal and has_horizon and has_position and has_risk


def _render_pack(pack: list[AdvisorEvidenceItem]) -> str:
    return "\n\n".join(
        f"[{item['id']}·{item['title']}]\n{item['text']}"
        + ("\n结构化指标：" + "；".join(f"{metric.name}={metric.value} {metric.unit}"
                                      for metric in item.metrics) if item.metrics else "")
        for item in pack)


def _public_evidence(pack: list[AdvisorEvidenceItem]) -> list[dict]:
    """持久化可追溯的事实/线索摘要与来源引用，不保存分析师推理、辩论或风控内部文本。"""
    return [{"id": item.id, "dimension": item.dimension, "title": item.title,
             "text": item.text, "sources": [source.model_dump() for source in item.sources],
             "metrics": [metric.model_dump() for metric in item.metrics]}
            for item in pack if item.publishable]


def _research_prediction_issues(card: SuggestionCard, *, base_rate_available: bool) -> list[str]:
    """自动研究封存的额外数据门禁，独立于执行建议档位。

    只有非拒答结论才必须提供明确的上涨/震荡/下跌事件概率；拒答不能为了
    增加样本被强迫预测。这些问题进入既有的一次修复路径，修复后仍不合格则
    发布程序化拒答卡，而不是写入模糊研究预测。
    """
    if card.direction == "abstain":
        return (["拒答结论的 research_prediction 必须为 null。"]
                if card.research_prediction is not None else [])
    if card.research_prediction is None:
        return ["非拒答结论必须填写 research_prediction 的上涨、震荡、下跌概率与涨跌判定阈值。"]
    if card.research_prediction.probability_source == "base_rate" and not base_rate_available:
        return ["本次基率样本未达标，research_prediction.probability_source 必须使用 model_subjective。"]
    return []


def _evidence(payload: dict) -> AdvisorEvidenceItem:
    """唯一的顾问证据入口，拒绝重复/未声明字段进入综合阶段。"""
    if "dim" in payload:
        payload = {**payload, "dimension": payload.pop("dim")}
    return AdvisorEvidenceItem.model_validate(payload)


def _checkpoint_value(value):
    if isinstance(value, AdvisorEvidenceItem):
        return value.model_dump()
    if isinstance(value, list):
        return [_checkpoint_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _checkpoint_value(item) for key, item in value.items()}
    return value


def _field_metrics(evidence_id: str, period: str, fields: dict) -> list[dict]:
    safe_period = re.sub(r"[^A-Za-z0-9_.-]", "_", period)[:40] or "latest"
    return [{"name": f"{evidence_id}.{safe_period}.{key}"[:96], "value": str(value), "unit": "raw",
             "evidence_refs": [evidence_id]}
            for key, value in fields.items() if isinstance(value, (int, float)) and not isinstance(value, bool)]


def _base_rate_metrics(text: str, available: bool) -> list[dict]:
    if not available:
        return []
    values = re.findall(r"中位收益 ([+-]?\d+(?:\.\d+)?)%|胜率 (\d+(?:\.\d+)?)%", text)
    metrics: list[dict] = []
    for index, (return_pct, win_pct) in enumerate(values):
        value = return_pct or win_pct
        metrics.append({"name": f"A1.stat_{index}", "value": str(float(value) / 100), "unit": "ratio",
                        "evidence_refs": ["A1"]})
    return metrics


def _record_fields(record) -> dict:
    if isinstance(record, dict):
        fields = record.get("fields", {})
    else:
        fields = getattr(record, "fields", {})
    return fields if isinstance(fields, dict) else {}


def _field_value(field):
    if isinstance(field, dict):
        return field.get("value")
    return getattr(field, "value", None)


def _record_period(record) -> str:
    value = record.get("period_key") if isinstance(record, dict) else getattr(record, "period_key", None)
    return str(value or "latest")


def _record_values(record) -> dict:
    values = {}
    for key, field in _record_fields(record).items():
        value = _field_value(field)
        if value is not None:
            values[key] = value
    return values


def _base_rate_rows(records: list) -> list[dict]:
    rows = []
    for record in records:
        close = _field_value(_record_fields(record).get("close_price"))
        period = _record_period(record)[:10]
        if close is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
            continue
        day = date.fromisoformat(period)
        rows.append({
            "date_ms": int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp() * 1000),
            "close_price": close,
        })
    return rows


def _format_f_score(result: dict) -> str:
    items = "；".join(
        f"{item['name']}：" + ("通过" if item["passed"] else "未通过")
        if item["available"] else f"{item['name']}：N/A" for item in result["items"])
    return f"中评级，{result['score']}/{result['available_items']} 项通过（共 9 项）：{items}。{result['note']}"


def _format_z_score(result: dict) -> str:
    if result["available"]:
        return f"Z 值 {result['score']}；{result['note']}"
    return "Z 值暂不计算（缺少" + "、".join(result["missing"]) + "）；" + result["note"]


def _pack_ids(pack: list[dict]) -> str:
    return "/".join(item["id"] for item in pack)


def _strategy_refs(pack: list[AdvisorEvidenceItem]) -> set[str]:
    """只允许本轮 S1 中、且已通过 candidate 门禁的实验被建议卡引用。"""
    return {source.ref for item in pack if item.id == "S1"
            for source in item.sources if source.ref}


def _strategy_matches_horizon(strategy_id: str, horizon: str | None) -> bool:
    """固定策略只进入与自身验证窗口一致的顾问证据包。"""
    if horizon == "days_5":
        return strategy_id == "reversal_5d_v1"
    if horizon == "days_20":
        return strategy_id == "trend_20_60_v1"
    return False


def _horizon_days(horizon: str | None) -> int:
    """顾问的固定期限与策略评审窗口的唯一映射。"""
    if horizon == "days_5":
        return 5
    if horizon == "days_20":
        return 20
    raise ValueError("不支持的策略评审期限。")


# 策略知识里的有效性宣称词：出现即认为文档在宣称"该方法有效"。
# 词表是保守的固定清单，避免把"适用条件/风险提示"误判成有效性结论。
_STRATEGY_VALIDITY_TERMS = (
    "长期有效", "持续有效", "显著有效", "稳定盈利", "胜率高",
    "回测表明", "历史验证", "经过验证", "历史实验证明", "长期跑赢",
)


def _strategy_conflicts(pack: list[AdvisorEvidenceItem], *,
                        candidate_refs: set[str]) -> list[str]:
    """确定性冲突检测：知识库宣称有效而本机没有 candidate 实验支撑。

    策略文档不能越过历史实验等级；检测结果由服务器复核并强制降级，
    不依赖模型自行承认冲突。
    """
    if candidate_refs:
        return []
    conflicts: list[str] = []
    for item in pack:
        if item.id != "S2":
            continue
        if any(term in item.text for term in _STRATEGY_VALIDITY_TERMS):
            conflicts.append(f"{item.title}宣称策略有效，但本机没有通过候选门禁的实验")
    return conflicts


def _infer_market_regime(closes: list[float]) -> str | None:
    """用程序可复现的价格状态生成市场状态标签（词表见 kb 模型）。

    波动分位与策略注册表的 vol_quantile=0.8 语义一致：近期 20 日波动处于
    历史 80 分位以上判为 high_vol，20 分位以下判为 low_vol；否则按均线
    状态给出 trend_up / trend_down / range。样本不足时返回 None。
    """
    import statistics

    if len(closes) < 60:
        return None
    window = closes[-60:]

    def _vol(segment: list[float]) -> float:
        returns = [(b - a) / a for a, b in zip(segment, segment[1:]) if a]
        return statistics.pstdev(returns) * (252 ** 0.5) if len(returns) >= 2 else 0.0

    vol_now = _vol(window[-20:])
    history = closes[-252:]
    vols = sorted(_vol(history[index:index + 20]) for index in range(0, len(history) - 20))
    if not vols:
        return None
    rank = sum(1 for value in vols if value <= vol_now) / len(vols)
    if rank >= 0.8:
        return "high_vol"
    if rank <= 0.2:
        return "low_vol"
    ma20 = sum(window[-20:]) / 20
    ma60 = sum(window) / 60
    last = window[-1]
    if ma20 > ma60 and last >= ma20:
        return "trend_up"
    if ma20 < ma60 and last <= ma20:
        return "trend_down"
    return "range"
