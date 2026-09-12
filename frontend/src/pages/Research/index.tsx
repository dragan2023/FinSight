import { ExperimentOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Input,
  Radio,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  addSupplement,
  cancelResearchTask,
  createResearchTask,
  getResearchReport,
  getResearchTask,
  isTerminalEvent,
  newClientRequestId,
  reanalyzeReport,
  streamResearchEvents,
  taskStatusLabels,
  type ResearchReportView,
  type ResearchScopeType,
  type ResearchTaskView,
  type SupplementView,
} from "../../api/research";
import type { SecurityIdentity } from "../../api/marketData";
import ErrorNotice from "../../components/ErrorNotice";
import SupplementPanel from "../../components/SupplementPanel";
import ResearchReport from "../../components/ResearchReport";
import ReportChat from "../../components/ReportChat";
import SecurityPicker from "../../components/SecurityPicker";

const MAX_QUESTION = 2000;
const MAX_SUPPLEMENT = 8000;
const SCOPE_TYPES: ResearchScopeType[] = ["company_overview", "watchlist_review", "index_strength", "sector_breadth", "market_pulse", "fund_overview", "fund_compare", "market_breadth", "general"];

/**
 * 研究工作台（计划 03 §2.6/2.7）：选标的、问问题、看公开进度、读报告、
 * 补充资料、主动再分析。生成 POST 只发一次（client_request_id 幂等）；
 * 浏览器断线后按 seq 续读，绝不重放生成请求。
 */
export default function Research() {
  const [searchParams] = useSearchParams();
  const requestedScope = SCOPE_TYPES.find((scope) => scope === searchParams.get("scope")) ?? null;
  const requestedCode = searchParams.get("thscode")?.toUpperCase() ?? "";
  const requestedEnd = Number(searchParams.get("end_ms"));
  const typedScope = requestedScope !== null && requestedScope !== "general";
  const scopeNeedsCode = requestedScope !== "market_pulse" && requestedScope !== "market_breadth" && requestedScope !== "watchlist_review";
  const [identity, setIdentity] = useState<SecurityIdentity | null>(null);
  const [question, setQuestion] = useState("");
  const [task, setTask] = useState<ResearchTaskView | null>(null);
  const [report, setReport] = useState<ResearchReportView | null>(null);
  const [streamError, setStreamError] = useState<unknown>(null);
  const [streaming, setStreaming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [supplements, setSupplements] = useState<SupplementView[]>([]);
  const [supplementKind, setSupplementKind] = useState<"user_provided" | "user_assumption">(
    "user_provided",
  );
  const [supplementText, setSupplementText] = useState("");
  const [savingSupplement, setSavingSupplement] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [selectedSupplementIds, setSelectedSupplementIds] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const lastSeqRef = useRef(0);

  useEffect(() => () => abortRef.current?.abort(), []);

  const loadReport = useCallback(async (taskId: string) => {
    const detail = await getResearchTask(taskId);
    setTask(detail.task);
    if (detail.task.report_id) {
      const body = await getResearchReport(detail.task.report_id);
      setReport(body.report);
    }
  }, []);

  const listen = useCallback(
    (taskId: string, after: number) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setStreamError(null);
      void streamResearchEvents(taskId, after, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "snapshot") {
            // 快照帧携带任务全貌（旧事件被清理后也能恢复），不计入增量列表。
            const snapshot: Record<string, unknown> = { ...event };
            delete snapshot.type;
            setTask(snapshot as unknown as ResearchTaskView);
            return;
          }
          if (typeof event.seq === "number") {
            lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
          }
          if (isTerminalEvent(event.type)) {
            void loadReport(taskId).catch(() => undefined);
          }
        },
      })
        .then(async () => {
          if (controller.signal.aborted) return;
          // 流自然结束（服务端在 end 帧后关闭）：以任务快照兜底刷新一次。
          await loadReport(taskId);
        })
        .catch((failure: unknown) => {
          if (!controller.signal.aborted) setStreamError(failure);
        })
        .finally(() => {
          if (!controller.signal.aborted) setStreaming(false);
        });
    },
    [loadReport],
  );

  const start = async () => {
    const targetCode = identity?.thscode ?? requestedCode;
    if (!question.trim() || (!targetCode && typedScope && scopeNeedsCode)) return;
    setStarting(true);
    setStreamError(null);
    setReport(null);
    setSupplements([]);
    setSelectedSupplementIds([]);
    lastSeqRef.current = 0;
    try {
      // client_request_id 幂等：重试/重放不会产生第二次计费任务。
      const body = await createResearchTask(typedScope
        ? { question: question.trim(), scope: { type: requestedScope, thscodes: targetCode ? [targetCode] : [], ...(Number.isSafeInteger(requestedEnd) && requestedEnd > 0 ? { end_ms: requestedEnd } : {}) }, client_request_id: newClientRequestId() }
        : { question: question.trim(), thscode: targetCode || undefined, client_request_id: newClientRequestId() });
      setTask(body.task);
      listen(body.task.id, 0);
    } catch (failure) {
      setStreamError(failure);
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!task) return;
    setCancelling(true);
    try {
      const body = await cancelResearchTask(task.id);
      setTask(body.task);
    } catch (failure) {
      setStreamError(failure);
    } finally {
      setCancelling(false);
    }
  };

  const saveSupplement = async () => {
    if (!report || !supplementText.trim()) return;
    setSavingSupplement(true);
    try {
      const body = await addSupplement(report.id, {
        kind: supplementKind,
        content: supplementText.trim(),
      });
      setSupplements((prev) => [...prev, body.supplement]);
      setSelectedSupplementIds((prev) => [...prev, body.supplement.id]);
      setSupplementText("");
    } catch (failure) {
      setStreamError(failure);
    } finally {
      setSavingSupplement(false);
    }
  };

  const reanalyze = async () => {
    if (!report) return;
    setReanalyzing(true);
    setStreamError(null);
    try {
      const body = await reanalyzeReport(report.id, {
        supplement_ids: selectedSupplementIds,
        client_request_id: newClientRequestId(),
      });
      setReport(null);
      setSupplements([]);
      setSelectedSupplementIds([]);
      lastSeqRef.current = 0;
      setTask(body.task);
      listen(body.task.id, 0);
    } catch (failure) {
      setStreamError(failure);
    } finally {
      setReanalyzing(false);
    }
  };

  const running = task !== null && (task.status === "queued" || task.status === "running");
  const needsContinuation =
    report !== null &&
    (report.output_integrity === "truncated" || report.output_integrity === "unstructured");

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">RESEARCH WORKBENCH</div>
          <h1>研究工作台</h1>
          <p>{typedScope ? `当前范围：${requestedScope}；范围、来源和预算将在任务中保存。` : "问一个问题，FinSight 取证、计算、解释，并把缺口如实列出。"}</p>
        </div>
        <Tag variant="filled">AI 研究助手</Tag>
      </div>

      <Space orientation="vertical" style={{ width: "100%" }} size="large">
        {streamError ? (
          <ErrorNotice
            error={streamError}
            retry={streaming ? undefined : () => task && listen(task.id, lastSeqRef.current)}
          />
        ) : null}

        <Card title="发起新研究">
          <Space orientation="vertical" style={{ width: "100%" }} size="large">
            <SecurityPicker
              value={identity?.thscode ?? null}
              onChange={(_code, confirmed) => setIdentity(confirmed)}
            />
            <Input.TextArea
              aria-label="研究问题"
              placeholder="例如：600519 盈利质量如何？当前价格隐含了什么预期？"
              value={question}
              maxLength={MAX_QUESTION}
              autoSize={{ minRows: 2, maxRows: 4 }}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <Typography.Text type="secondary">
              系统会使用已连接的数据与分析能力生成报告；暂时无法核验的信息会在报告中单独说明。
            </Typography.Text>
            <Space size="small" wrap>
              <Button
                type="primary"
                icon={<ExperimentOutlined aria-hidden />}
                disabled={((!identity && !requestedCode) && (!typedScope || scopeNeedsCode)) || question.trim().length === 0 || running}
                loading={starting}
                onClick={() => void start()}
              >
                开始研究
              </Button>
              {running && (
                <Button danger loading={cancelling} onClick={() => void cancel()}>
                  取消任务
                </Button>
              )}
              {running && !streaming && (
                <Button onClick={() => task && listen(task.id, lastSeqRef.current)}>
                  刷新续读
                </Button>
              )}
            </Space>
          </Space>
        </Card>

        {task && (
          <Card
            title={
              <Space size="small" wrap>
                <span>任务进度</span>
                <Tag color={running ? "blue" : task.status === "completed" ? "green" : "red"}>
                  {taskStatusLabels[task.status]}
                </Tag>
              </Space>
            }
          >
            <Space orientation="vertical" style={{ width: "100%" }} size="small">
              {streaming && (
                <Space size="small">
                  <Spin size="small" />
                  <Typography.Text type="secondary">
                    正在整理数据并生成可读结论…
                  </Typography.Text>
                </Space>
              )}
              {!streaming && task.status === "completed" && (
                <Typography.Text type="secondary">研究已完成，报告已生成。</Typography.Text>
              )}
              {task.error && <Typography.Text type="danger">{task.error}</Typography.Text>}
            </Space>
          </Card>
        )}

        {report && (
          <Card title={"研究报告 · " + report.question}>
            <Space orientation="vertical" style={{ width: "100%" }} size="large">
              <ResearchReport report={report} />
              <ReportChat reportId={report.id} />
              <Card size="small" title="补充资料">
                <Space orientation="vertical" style={{ width: "100%" }} size="small">
                  {supplements.length > 0 && (
                    <Alert
                      type="success"
                      showIcon
                      title="已保存，尚未再分析"
                      description={
                        <Space orientation="vertical" size={2}>
                          {supplements.map((item) => (
                            <Checkbox
                              key={item.id}
                              checked={selectedSupplementIds.includes(item.id)}
                              onChange={(event) =>
                                setSelectedSupplementIds((prev) =>
                                  event.target.checked
                                    ? [...prev, item.id]
                                    : prev.filter((id) => id !== item.id),
                                )
                              }
                            >
                              {"[" + (item.kind === "user_provided" ? "用户补充" : "用户假设") + "] "}
                              {item.content.slice(0, 40)}
                              {item.content.length > 40 ? "…" : ""}
                            </Checkbox>
                          ))}
                        </Space>
                      }
                    />
                  )}
                  <Radio.Group
                    value={supplementKind}
                    onChange={(event) => setSupplementKind(event.target.value)}
                  >
                    <Radio value="user_provided">用户补充（有来源）</Radio>
                    <Radio value="user_assumption">用户假设</Radio>
                  </Radio.Group>
                  <Input.TextArea
                    aria-label="补充内容"
                    placeholder="按缺口补充文字/数值说明，或写下你的假设；只保存，不会自动外发。"
                    value={supplementText}
                    maxLength={MAX_SUPPLEMENT}
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) => setSupplementText(event.target.value)}
                  />
                  <Space size="small" wrap>
                    <Button
                      disabled={supplementText.trim().length === 0}
                      loading={savingSupplement}
                      onClick={() => void saveSupplement()}
                    >
                      保存补充
                    </Button>
                    <Button
                      type="primary"
                      loading={reanalyzing}
                      disabled={running}
                      onClick={() => void reanalyze()}
                    >
                      {needsContinuation ? "继续完成报告（再分析）" : "基于这些资料再分析"}
                    </Button>
                    <Typography.Text type="secondary">
                      再分析创建新版本，旧报告保持可读；不会自动触发。
                    </Typography.Text>
                  </Space>
                  <SupplementPanel
                    reportId={report.id}
                    supplements={supplements}
                    onSupplementsChanged={(items) =>
                      setSupplements((prev) => {
                        const known = new Set(prev.map((item) => item.id));
                        return [...prev, ...items.filter((item) => !known.has(item.id))];
                      })
                    }
                  />
                </Space>
              </Card>
            </Space>
          </Card>
        )}
      </Space>
    </>
  );
}
