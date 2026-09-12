import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Collapse, Input, List, Popconfirm, Select, Space, Tabs, Tag, Typography } from "antd";
import { PlusOutlined, SendOutlined, DeleteOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ErrorNotice from "../../components/ErrorNotice";
import ResearchPredictionPanel from "../../components/ResearchPredictionPanel";
import {
  createAdvisorSession, deleteAdvisorSession, listAdvisorMessages, listAdvisorSessions,
  listAdviceLedger, listSessionRuns, startAdvisorRun, streamAdvisorRunEvents, cancelAdvisorRun, resumeAdvisorRun, settleAdvice, settleEligibleAdvice, type AdvisorEvidence, type AdvisorEvidenceSource, type AdvisorMessage, type AdvisorRun, type AdvisorSession,
  type SuggestionCardView,
} from "../../api/advisor";

const EVENT_LABEL: Record<string, string> = { positive_return: "上涨事件", negative_return: "下跌事件" };
const FORECAST_SOURCE_LABEL: Record<string, string> = {
  unavailable: "暂无经验样本，不伪造概率",
  strategy_empirical_pending: "等待策略经验样本积累",
};
const DIRECTION_LABEL: Record<string, string> = {
  accumulate: "倾向买入", hold: "继续持有", wait: "暂不买入，等待时机",
  reduce: "建议减仓", avoid: "回避", abstain: "证据不足，暂不表态",
};
const CONFIDENCE_LABEL: Record<string, string> = {
  very_low: "把握度：很低", low: "把握度：较低", medium: "把握度：中等", high: "把握度：较高",
};
const RECOMMENDATION_TIER_LABEL: Record<string, string> = {
  strong_recommended: "强烈推荐", recommended: "推荐", observe: "观察",
  trial_buy_signal: "试探买入信号", short_term_not_considered: "短期不考虑",
  data_insufficient: "数据不足/拒答",
};
const RECOMMENDATION_TIER_COLOR: Record<string, string> = {
  strong_recommended: "green", recommended: "blue", observe: "gold",
  trial_buy_signal: "orange", short_term_not_considered: "red", data_insufficient: "default",
};
const SCENARIO_LABEL: Record<string, string> = { bull: "乐观情景", base: "基准情景", bear: "悲观情景" };
// 置信度由服务器按固定规则写入（阶段03 §3.6）：这里只做展示映射，不反推数值。
const EVIDENCE_QUALITY_LABEL: Record<string, string> = {
  sufficient: "证据较全", partial: "证据不全", missing: "证据缺失",
};
const STRATEGY_GRADE_LABEL: Record<string, string> = {
  candidate: "有候选实验", none: "无候选实验", experimental: "仅实验级", rejected: "已淘汰",
};
const REGIME_MATCH_LABEL: Record<string, string> = {
  matched: "状态适配", mismatched: "状态不适配", not_verified: "状态适配未核验",
};
const FORWARD_CALIBRATION_LABEL: Record<string, string> = {
  available: "已有前向校准", not_available: "暂无前向校准样本",
};
const FINAL_LEVEL_LABEL: Record<string, string> = {
  very_low: "很低", low: "较低", medium: "中等", high: "较高",
};
const STATUS_LABEL: Record<string, string> = { tracking: "跟踪中", settled: "已结算", void: "已作废" };
const RUN_STATUS_LABEL: Record<string, string> = {
  queued: "排队中", running: "进行中", cancelled: "已取消", failed: "失败", interrupted: "因重启中断",
};

function EvidenceSourceLink({ source }: { source: AdvisorEvidenceSource }) {
  // 知识库来源回溯到本机文档原文抽屉；http(s) 引用在新窗口打开；其余仅展示标签。
  if (source.kind === "kb" && source.ref) {
    const params = new URLSearchParams({ document: source.ref });
    if (source.page) params.set("page", String(source.page));
    return <Link to={`/knowledge?${params.toString()}`}>{source.label}</Link>;
  }
  if (source.ref && /^https?:\/\//.test(source.ref)) {
    return <a href={source.ref} target="_blank" rel="noreferrer">{source.label}</a>;
  }
  return <span>{source.label}</span>;
}

function EvidenceList({ evidence }: { evidence: AdvisorEvidence[] }) {
  return (
    <ul className="advisor-phases">
      {evidence.map((item) => (
        <li key={item.id}>
          <strong>{item.id}·{item.title}</strong>：{item.text}
          {(item.sources ?? []).length > 0 && (
            <span className="advisor-evidence-sources"> 来源：
              {item.sources!.map((source, index) => (
                <span key={index}>
                  <EvidenceSourceLink source={source} />
                  {index < item.sources!.length - 1 ? "；" : ""}
                </span>
              ))}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function SuggestionCard({ card, evidence = [] }: { card: SuggestionCardView; evidence?: AdvisorEvidence[] }) {
  const byId = new Map(evidence.map((item) => [item.id, item]));
  const evidenceNames = (refs: string[]) => refs.map((ref) => byId.get(ref) ? `${ref}·${byId.get(ref)!.title}` : ref).join("；");
  return (
    <div className="advisor-card">
      <div className="advisor-card-head">
        <Typography.Text strong>{card.name}（{card.thscode}）</Typography.Text>
        {card.recommendation_tier && (
          <Tag color={RECOMMENDATION_TIER_COLOR[card.recommendation_tier]}>
            {RECOMMENDATION_TIER_LABEL[card.recommendation_tier] ?? card.recommendation_tier}
          </Tag>
        )}
        <Tag color="green">{DIRECTION_LABEL[card.direction] ?? card.direction}</Tag>
        <Tag>{CONFIDENCE_LABEL[card.confidence] ?? card.confidence}</Tag>
      </div>
      {card.scenarios.length > 0 && (
        <ul className="advisor-scenarios">
          {card.scenarios.map((scenario) => (
            <li key={scenario.name}>
              <strong>{SCENARIO_LABEL[scenario.name] ?? scenario.name} {scenario.probability_pct}%</strong>
              <span>（{scenario.probability_source === "base_rate" ? "历史同类案例统计" : "分析判断"}）：{scenario.trigger_condition}</span>
            </li>
          ))}
        </ul>
      )}
      {card.recommendation_tier_reason && (
        <p className="advisor-block"><strong>建议档位依据：</strong>{card.recommendation_tier_reason}</p>
      )}
      <p className="advisor-block"><strong>什么情况下这个判断作废：</strong>{card.invalidations.join("；")}</p>
      <p className="advisor-block"><strong>跟踪哨：</strong>{card.watchpoints.join("；")}</p>
      {card.missing_for_decision.length > 0 && (
        <p className="advisor-block"><strong>还缺什么：</strong>{card.missing_for_decision.join("；")}</p>
      )}
      {card.strategy_refs && card.strategy_refs.length > 0 && (
        <p className="advisor-block"><strong>引用的候选策略实验：</strong>
          {card.strategy_refs.join("；")}
          {typeof card.confidence_basis?.strategy_grade === "string"
            && STRATEGY_GRADE_LABEL[card.confidence_basis.strategy_grade]
            && (`（${STRATEGY_GRADE_LABEL[card.confidence_basis.strategy_grade]}）`)}
        </p>
      )}
      {card.confidence_basis && Object.keys(card.confidence_basis).length > 0 && (
        <p className="advisor-block"><strong>置信度依据：</strong>
          把握度组成：证据{EVIDENCE_QUALITY_LABEL[String(card.confidence_basis.evidence_quality)] ?? "未评定"}、
          策略{STRATEGY_GRADE_LABEL[String(card.confidence_basis.strategy_grade)] ?? "未评定"}、
          {REGIME_MATCH_LABEL[String(card.confidence_basis.regime_match)] ?? "状态适配未核验"}、
          {FORWARD_CALIBRATION_LABEL[String(card.confidence_basis.forward_calibration)] ?? "暂无前向校准样本"}；
          {card.confidence_basis.unresolved_conflicts === true ? "存在未解决冲突；" : "无未解决冲突；"}
          服务器终档：{FINAL_LEVEL_LABEL[String(card.confidence_basis.final_level)] ?? "未评定"}。
          {Array.isArray(card.confidence_basis.candidate_experiments) && card.confidence_basis.candidate_experiments.length > 0
            ? `已匹配 ${card.confidence_basis.candidate_experiments.length} 个候选实验；`
            : "未匹配到候选实验；"}
          {card.confidence_basis.base_rate_available === true ? "历史基率样本可用。" : "历史基率样本不足或不可用。"}
          历史实验不等于未来结果；完成真实前向校准前，把握度最高只标注为中等。
        </p>
      )}
      {card.legacy_unverified ? (
        <Alert type="warning" showIcon message="历史建议：未经过新版引用校验" style={{ marginBottom: 8 }} />
      ) : (
        <>
          <p className="advisor-block"><strong>支持结论的证据：</strong>{evidenceNames(card.key_evidence)}</p>
          <p className="advisor-block"><strong>最强反方事实：</strong>{evidenceNames(card.counter_evidence ?? [])}</p>
        </>
      )}
      <p className="advisor-block advisor-narrative">{card.decision_narrative}</p>
      {evidence.length > 0 && (
        <Collapse size="small" items={[{
          key: "evidence", label: "查看本次证据摘要",
          children: <EvidenceList evidence={evidence} />,
        }]} />
      )}
      <p className="advisor-disclaimer">研究参考，不构成投资建议。</p>
    </div>
  );
}

function MessageLine({ message }: { message: AdvisorMessage }) {
  const isUser = message.role === "user";
  const rawSuggestion = message.suggestion as (SuggestionCardView & { evidence?: AdvisorEvidence[] }) | null;
  return (
    <li className={isUser ? "advisor-msg advisor-msg-user" : "advisor-msg"}>
      <span className="advisor-msg-role">{isUser ? "你" : "顾问"}</span>
      <div>
        <span className="advisor-msg-text">{message.content}</span>
        {!isUser && rawSuggestion && (
          <SuggestionCard card={rawSuggestion} evidence={rawSuggestion.evidence ?? []} />
        )}
      </div>
    </li>
  );
}

export default function Advisor() {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [allowWeb, setAllowWeb] = useState(false);
  const [analysisMode, setAnalysisMode] = useState<"standard" | "deep">("standard");
  const [run, setRun] = useState<AdvisorRun | null>(null);

  const sessionsQuery = useQuery({ queryKey: ["advisor-sessions"], queryFn: listAdvisorSessions });
  const messagesQuery = useQuery({
    queryKey: ["advisor-messages", activeId],
    queryFn: () => listAdvisorMessages(activeId as string),
    enabled: activeId !== null,
  });
  const ledgerQuery = useQuery({ queryKey: ["advisor-ledger"], queryFn: listAdviceLedger });

  const createMutation = useMutation({
    mutationFn: () => createAdvisorSession(""),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["advisor-sessions"] });
      setActiveId(data.session.id);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteAdvisorSession,
    onSuccess: () => {
      setActiveId(null);
      queryClient.invalidateQueries({ queryKey: ["advisor-sessions"] });
    },
  });
  const sendMutation = useMutation({
    mutationFn: () => startAdvisorRun(activeId as string, draft.trim(), allowWeb, analysisMode),
    onSuccess: (data) => {
      setDraft("");
      setRun(data.run);
    },
  });
  const runsQuery = useQuery({
    queryKey: ["advisor-runs", activeId],
    queryFn: () => listSessionRuns(activeId as string),
    enabled: activeId !== null,
  });
  const invalidateRuns = () => queryClient.invalidateQueries({ queryKey: ["advisor-runs", activeId] });
  const cancelRunMutation = useMutation({
    mutationFn: cancelAdvisorRun,
    onSuccess: (data) => { setRun(data.run); invalidateRuns(); },
  });
  const resumeRunMutation = useMutation({
    mutationFn: resumeAdvisorRun,
    onSuccess: (data) => { setRun(data.run); invalidateRuns(); },
  });
  const settleMutation = useMutation({
    mutationFn: settleAdvice,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["advisor-ledger"] }),
  });
  const settleEligibleMutation = useMutation({
    mutationFn: settleEligibleAdvice,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["advisor-ledger"] }),
  });

  const notice = createMutation.error ?? deleteMutation.error ?? sendMutation.error ?? settleMutation.error ?? settleEligibleMutation.error ?? cancelRunMutation.error ?? resumeRunMutation.error;
  const sessions: AdvisorSession[] = sessionsQuery.data?.sessions ?? [];
  const messages: AdvisorMessage[] = messagesQuery.data?.messages ?? [];
  const entries = ledgerQuery.data?.entries ?? [];
  const calibration = ledgerQuery.data?.calibration;
  const streamingRunId = run && !["completed", "cancelled", "failed", "interrupted"].includes(run.status)
    ? run.id
    : null;
  useEffect(() => {
    if (!streamingRunId) return;
    const controller = new AbortController();
    void streamAdvisorRunEvents(streamingRunId, 0, {
      signal: controller.signal,
      onEvent: (event) => setRun(event.run),
    }).catch(() => {
      // 已显示的阶段保留；用户可取消、恢复或新建会话，避免把流断开伪装成模型结论。
    });
    return () => controller.abort();
  }, [streamingRunId]);
  useEffect(() => {
    if (run?.status === "completed") {
      queryClient.invalidateQueries({ queryKey: ["advisor-messages", activeId] });
      queryClient.invalidateQueries({ queryKey: ["advisor-ledger"] });
      queryClient.invalidateQueries({ queryKey: ["advisor-runs", activeId] });
    }
  }, [activeId, queryClient, run?.status]);
  const recoverableRuns = (runsQuery.data?.runs ?? [])
    .filter((item) => ["cancelled", "failed", "interrupted"].includes(item.status) && item.id !== run?.id)
    .slice(0, 3);

  return (
    <div className="page-stack">
      <header className="page-heading">
        <h1>顾问工作台</h1>
        <p>多轮对话的投资研究顾问：先澄清目标，再取证分析，输出带三情景与失效条件的结构化建议，并登记台账可复盘。</p>
      </header>
      {notice && <ErrorNotice error={notice} retry={() => {
        createMutation.reset(); deleteMutation.reset(); sendMutation.reset(); settleMutation.reset(); cancelRunMutation.reset(); resumeRunMutation.reset();
      }} />}
      <Alert type="warning" showIcon
        title="研究参考，不构成投资建议"
        description="顾问输出为研究分析框架与可核验证据；不承诺收益，不代客决策。" />
      <div className="advisor-layout">
        <div className="advisor-sessions">
          <Button block icon={<PlusOutlined aria-hidden />} loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}>
            新建会话
          </Button>
          <List
            size="small"
            dataSource={sessions}
            locale={{ emptyText: "尚无会话；新建后即可开始提问。" }}
            renderItem={(session) => (
              <List.Item
                className={session.id === activeId ? "advisor-session active" : "advisor-session"}
                onClick={() => setActiveId(session.id)}
                actions={[
                  <Button key="del" size="small" type="text" danger aria-label="删除会话"
                    icon={<DeleteOutlined aria-hidden />}
                    onClick={(event) => { event.stopPropagation(); deleteMutation.mutate(session.id); }} />,
                ]}
              >
                <Typography.Text ellipsis style={{ maxWidth: 150 }}>
                  {session.title || "未命名会话"}
                </Typography.Text>
              </List.Item>
            )}
          />
        </div>
        <div className="advisor-main">
          <ul className="news-timeline advisor-thread">
            {messages.map((message) => <MessageLine key={message.id} message={message} />)}
          </ul>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder={activeId ? "例如：帮我看下 601799 现在能不能建仓，目标一个月内" : "先新建或选择一个会话"}
              value={draft}
              maxLength={4000}
              disabled={!activeId}
              onChange={(event) => setDraft(event.target.value)}
              onPressEnter={() => draft.trim() && sendMutation.mutate()}
              aria-label="顾问对话输入"
            />
            <Button type="primary" icon={<SendOutlined aria-hidden />} disabled={!activeId || !draft.trim()}
              loading={sendMutation.isPending} onClick={() => sendMutation.mutate()}>
              发送
            </Button>
          </Space.Compact>
          {run && run.status !== "completed" && (
            <Space style={{ marginTop: 8 }}>
              <Typography.Text type="secondary">深度任务：{run.status === "queued" ? "已提交" : run.status === "running" ? "分析中" : run.status === "cancelled" ? "已取消" : "失败"}</Typography.Text>
              {(run.status === "queued" || run.status === "running") && <Button size="small" onClick={() => cancelRunMutation.mutate(run.id)}>取消</Button>}
              {(run.status === "cancelled" || run.status === "failed" || run.status === "interrupted") && <Button size="small" onClick={() => resumeRunMutation.mutate(run.id)}>恢复</Button>}
            </Space>
          )}
          {run?.error && <Alert type="error" showIcon style={{ marginTop: 8 }} title="本次顾问任务未完成" description={run.error} />}
          {recoverableRuns.length > 0 && (
            <Space wrap size={4} style={{ marginTop: 8 }}>
              {recoverableRuns.map((item) => (
                <Space key={item.id} size={4}>
                  <Typography.Text type="secondary">
                    历史任务{RUN_STATUS_LABEL[item.status] ?? item.status}，可恢复续跑：
                  </Typography.Text>
                  <Button size="small" onClick={() => resumeRunMutation.mutate(item.id)}>恢复</Button>
                  <Button size="small" onClick={() => cancelRunMutation.mutate(item.id)}>不再跟踪</Button>
                </Space>
              ))}
            </Space>
          )}
          <Checkbox checked={allowWeb} onChange={(event) => setAllowWeb(event.target.checked)}
            style={{ marginTop: 8 }}>
            本次允许联网搜索（使用已配置的付费搜索服务，仅返回标题与摘要）
          </Checkbox>
          <Space style={{ marginTop: 8 }}>
            <Typography.Text type="secondary">分析模式</Typography.Text>
            <Select value={analysisMode} disabled={sendMutation.isPending} style={{ width: 180 }}
              aria-label="分析模式"
              onChange={(value: "standard" | "deep") => setAnalysisMode(value)}
              options={[
                { value: "standard", label: "标准（最多 4 次模型调用）" },
                { value: "deep", label: "深度（多分析师与辩论）" },
              ]} />
          </Space>
          {run?.phases && run.phases.length > 0 && (
            <Collapse size="small" style={{ marginTop: 8 }} items={[{
              key: "phases",
              label: "查看本次分析过程",
              children: (
                <ul className="advisor-phases">
                  {run.phases.map((phase, index) => (
                    <li key={index}>{phase.detail}</li>
                  ))}
                </ul>
              ),
            }]} />
          )}
        </div>
      </div>
      <Tabs items={[{
        key: "ledger",
        label: "执行建议台账",
        children: (
          <div>
            {calibration && calibration.window > 0 && (
              <p>最近 {calibration.window} 条已结算建议的方向命中率为 {Math.round((calibration.hit_rate ?? 0) * 100)}%，覆盖率 {Math.round((calibration.coverage_rate ?? 0) * 100)}%。
                {calibration.warn_high && " 高把握度建议的实际命中偏低，后续建议的把握度标注会更保守。"}</p>
            )}
            {calibration && !calibration.sample_sufficient && <Typography.Paragraph type="secondary">校准样本不足：{calibration.insufficiency_reason ?? "尚未达到门槛。"}</Typography.Paragraph>}
            {calibration?.brier !== null && calibration?.brier !== undefined && <Typography.Paragraph>经验概率 Brier：{calibration.brier}；校准差：{calibration.calibration_gap ?? "—"}</Typography.Paragraph>}
            {calibration?.track_comparison && (
              <Typography.Paragraph>
                双轨对照：策略原始轨 {calibration.track_comparison.strategy_only.count} 条
                {calibration.track_comparison.strategy_only.brier !== null ? `，Brier ${calibration.track_comparison.strategy_only.brier}` : "，暂无可评分样本"}；
                AI 综合轨 {calibration.track_comparison.advisor_combined.count} 条
                {calibration.track_comparison.advisor_combined.brier !== null ? `，Brier ${calibration.track_comparison.advisor_combined.brier}` : "，暂无可评分样本"}。
              </Typography.Paragraph>
            )}
            <Popconfirm
              title="批量结算会按每条记录的跟踪窗口调用同花顺行情核对，且不自动刷新行情；确认继续？"
              okText="确认结算" cancelText="取消"
              onConfirm={() => settleEligibleMutation.mutate()}>
              <Button size="small" loading={settleEligibleMutation.isPending}>结算已到期记录（最多 50 条）</Button>
            </Popconfirm>
            <List
              size="small"
              dataSource={entries}
              locale={{ emptyText: "台账暂无登记建议；每次完整建议都会自动登记，可跟踪与复盘。" }}
              renderItem={(entry) => (
                <List.Item
                  actions={entry.status === "tracking" ? [
                    <Button key="settle" size="small" loading={settleMutation.isPending}
                      onClick={() => settleMutation.mutate(entry.id)}>
                      结算
                    </Button>,
                  ] : []}
                >
                  <div>
                    <Space wrap>
                      <Typography.Text strong>{entry.name}（{entry.thscode}）</Typography.Text>
                      <Tag color="green">{DIRECTION_LABEL[entry.direction] ?? entry.direction}</Tag>
                      <Tag>{STATUS_LABEL[entry.status] ?? entry.status}</Tag>
                      {(entry.strategy_refs?.length ?? 0) > 0 && (
                        <Tag color="blue">策略依据：{entry.strategy_refs!.join("、")}</Tag>
                      )}
                    </Space>
                    <div className="advisor-block" style={{ marginTop: 4 }}>
                      {(() => {
                        const forecast = (entry.forecast_event ?? {}) as Record<string, unknown>;
                        const parts: string[] = [];
                        if (forecast.event) parts.push(`预测事件：${EVENT_LABEL[String(forecast.event)] ?? String(forecast.event)}`);
                        if (forecast.horizon_days) parts.push(`预测窗口 ${String(forecast.horizon_days)} 天`);
                        parts.push(forecast.probability !== null && forecast.probability !== undefined
                          ? `经验概率 ${String(forecast.probability)}`
                          : "经验概率：暂无（不伪造）");
                        parts.push(`概率来源：${FORECAST_SOURCE_LABEL[String(forecast.source)] ?? "未登记"}`);
                        const created = entry.created_at ? new Date(entry.created_at) : null;
                        const windowDays = Number(forecast.horizon_days ?? 0);
                        if (created && windowDays > 0 && entry.status === "tracking") {
                          const due = new Date(created.getTime() + windowDays * 86_400_000);
                          parts.push(due.getTime() <= Date.now() ? "已到最早尝试日（仍需核对完整交易日）" : "跟踪中（未到最早尝试日）");
                        }
                        return <Typography.Text type="secondary">{parts.join("；")}。</Typography.Text>;
                      })()}
                      {(entry.strategy_forecasts?.length ?? 0) > 0 && (
                        <Typography.Text type="secondary">
                          策略原始轨：{entry.strategy_forecasts!.map((item) =>
                            `${item.strategy_ref}=${item.signal}（数据截至 ${item.signal_as_of ?? "未知"}${item.eligible ? "，可评分" : `，不评分：${item.reason ?? "条件不足"}`}）`).join("；")}。
                        </Typography.Text>
                      )}
                      {entry.direction === "wait" && (
                        <Typography.Text type="secondary">
                          跟踪哨：{(entry.card as Record<string, unknown>)?.watchpoints instanceof Array
                            && ((entry.card as Record<string, unknown>).watchpoints as string[]).length > 0
                            ? ((entry.card as Record<string, unknown>).watchpoints as string[]).join("；")
                            : "未登记观察哨"}。等待触发后人工复盘，不纳入方向命中统计。
                        </Typography.Text>
                      )}
                      {entry.direction === "abstain" && (
                        <Typography.Text type="secondary">
                          主动拒答{(entry.card as Record<string, unknown>)?.missing_for_decision instanceof Array
                            && ((entry.card as Record<string, unknown>).missing_for_decision as string[]).length > 0
                            ? `：缺失 ${((entry.card as Record<string, unknown>).missing_for_decision as string[]).join("、")}`
                            : ""}。计入覆盖率，不计入方向错误率。
                        </Typography.Text>
                      )}
                      {entry.status === "void" && entry.settlement != null && (
                        <Typography.Text type="secondary">
                          {(() => {
                            const s = entry.settlement as Record<string, unknown>;
                            if (s.kind === "abstain") {
                              return String(s.reason ?? "主动拒答：证据不足，不计入方向错误率。");
                            }
                            if (s.kind === "wait") {
                              const sentinels = (s.sentinels as { watchpoint: string; triggered: boolean | null; trigger_date: string | null; note: string | null }[]) ?? [];
                              const hit = sentinels.filter((x) => x.triggered === true);
                              const manual = sentinels.filter((x) => x.triggered === null);
                              const detail = hit.length > 0
                                ? `已触发 ${hit.length}/${sentinels.length}（${hit.map((x) => x.trigger_date).join("、")}）`
                                : "窗口内未触发";
                              const tail = manual.length > 0 ? `；${manual.length} 项观察哨需人工复盘` : "";
                              return `观察哨评估：${detail}${tail}；不纳入方向命中统计。`;
                            }
                            return `已作废：${String(s.detail ?? "该方向不构成可直接按涨跌判定的价格判断。")}`;
                          })()}
                        </Typography.Text>
                      )}
                      {entry.status === "settled" && entry.settlement != null && (
                        <Typography.Text type="secondary">
                          {(() => {
                            const s = entry.settlement as Record<string, unknown>;
                            return `以 ${String(s.base_date)} 收盘 ${String(s.base_close)} 为基准，${String(s.final_date)} 收盘 ${String(s.final_close)}，`
                              + `涨跌 ${String(s.change_pct)}%，${s.hit ? "方向命中" : "方向未命中"}`
                              + (s.squared_error !== null && s.squared_error !== undefined ? `；Brier 样本平方误差 ${String(s.squared_error)}` : "；无经验概率，不参与 Brier");
                          })()}。
                        </Typography.Text>
                      )}
                    </div>
                    {(entry.evidence?.length ?? 0) > 0 && (
                      <Collapse size="small" style={{ marginTop: 6 }} items={[{
                        key: "evidence", label: `证据摘要（${entry.evidence!.length} 段）`,
                        children: <EvidenceList evidence={entry.evidence!} />,
                      }]} />
                    )}
                  </div>
                </List.Item>
              )}
            />
          </div>
        ),
      }, {
        key: "research-predictions",
        label: "研究预测封存",
        children: <ResearchPredictionPanel />,
      }]} />
    </div>
  );
}
