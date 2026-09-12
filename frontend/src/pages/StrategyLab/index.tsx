import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import EChart from "../../components/charts/EChart";
import ErrorNotice from "../../components/ErrorNotice";
import { fetchStrategyCalibration, listStrategies, listStrategyExperiments, reviewStrategy, runStrategyExperiment, type StrategyCalibration, type StrategyExperiment } from "../../api/strategy";

const label: Record<string, string> = { candidate: "研究候选", experimental: "实验性", insufficient: "样本不足", rejected: "已拒绝" };
const LIFECYCLE_LABEL: Record<string, string> = { validated: "已验证", candidate: "保持候选", retired: "已退休" };
const HORIZON_OPTIONS = [5, 20, 90, 365].map((days) => ({ value: days, label: `${days} 天预测窗口` }));
const SIGNAL_LABEL: Record<string, string> = { enter: "下一交易日进入", hold: "继续持有", exit: "下一交易日退出", flat: "空仓" };

function CalibrationSummary({ data }: { data: StrategyCalibration }) {
  const parts = [
    `独立前向样本 ${data.count} 条`,
    data.brier !== null && `Brier ${data.brier}`,
    data.brier_skill_score !== null && `Skill Score ${data.brier_skill_score}`,
    data.calibration_gap !== null && `校准差 ${data.calibration_gap}`,
    data.high_confidence_error_rate !== null && `高置信错误率 ${Math.round(data.high_confidence_error_rate * 100)}%`,
  ].filter(Boolean);
  const tail = data.eligible ? "已达到前向升级门槛。" : data.reason ?? "未达到前向升级门槛。";
  return (
    <Typography.Paragraph data-testid="calibration-summary">
      {parts.join("；") + "。" + tail}
    </Typography.Paragraph>
  );
}

function ReviewPanel({ experiment }: { experiment: StrategyExperiment }) {
  const qc = useQueryClient();
  const [horizonDays, setHorizonDays] = useState(20);
  const calibration = useQuery({
    queryKey: ["strategy-calibration", experiment.strategy_id, experiment.strategy_version, horizonDays],
    queryFn: () => fetchStrategyCalibration(experiment.strategy_id, experiment.strategy_version, horizonDays),
  });
  const review = useMutation({
    mutationFn: (decision: "validated" | "keep_candidate" | "retire") =>
      reviewStrategy(experiment.strategy_id, { strategy_version: experiment.strategy_version, horizon_days: horizonDays, decision }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      qc.invalidateQueries({ queryKey: ["strategy-calibration"] });
    },
  });
  const snapshot = calibration.data?.calibration;
  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Title level={5}>人工评审（前向校准门槛）</Typography.Title>
      <Typography.Paragraph type="secondary">
        只有独立前向样本（同标的窗口不重叠、具有经验概率）达到 30 条且 Brier、Skill Score、校准差、
        近期退化与高置信错误率全部达标时，才允许升级为已验证；未达标只能保持候选或退休。
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select value={horizonDays} onChange={setHorizonDays} options={HORIZON_OPTIONS} style={{ width: 160 }} aria-label="评审预测窗口" />
        <Button size="small" loading={review.isPending} onClick={() => review.mutate("keep_candidate")}>保持候选</Button>
        {snapshot?.eligible && (
          <Button size="small" type="primary" loading={review.isPending} onClick={() => review.mutate("validated")}>确认为已验证</Button>
        )}
        <Button size="small" danger loading={review.isPending} onClick={() => review.mutate("retire")}>退休</Button>
      </Space>
      {review.error && <ErrorNotice error={review.error} />}
      {calibration.isFetching && <Typography.Text type="secondary">正在读取前向校准快照…</Typography.Text>}
      {snapshot && <CalibrationSummary data={snapshot} />}
    </div>
  );
}

export default function StrategyLab() {
  const qc = useQueryClient();
  const [active, setActive] = useState<StrategyExperiment | null>(null);
  const specs = useQuery({ queryKey: ["strategies"], queryFn: listStrategies });
  const experiments = useQuery({ queryKey: ["strategy-experiments"], queryFn: listStrategyExperiments });
  const run = useMutation({
    mutationFn: runStrategyExperiment,
    onSuccess: ({ experiment }) => {
      setActive(experiment);
      qc.invalidateQueries({ queryKey: ["strategy-experiments"] });
    },
  });
  const [form] = Form.useForm();
  const result = active?.result;
  return (
    <div className="page-stack">
      <header className="page-heading"><h1>策略实验</h1><p>单标的、固定规则的历史研究实验；结果不构成交易建议。</p></header>
      <Alert type="warning" showIcon message="仅使用前复权日 K；信号在收盘后形成，下一交易日开盘执行。" />
      {run.error && <ErrorNotice error={run.error} />}
      <Card title="新建实验" style={{ marginTop: 12 }}>
        <Form form={form} layout="inline" initialValues={{ strategy_id: "trend_20_60_v1", one_way_cost_bps: 10, refresh: false }}
          onFinish={(v) => run.mutate({ ...v, start_ms: Date.parse(v.start), end_ms: Date.parse(v.end) })}>
          <Form.Item name="thscode" rules={[{ required: true }]}><Input placeholder="已确认标的，如 600519.SH" /></Form.Item>
          <Form.Item name="strategy_id">
            <Select style={{ width: 220 }} options={(specs.data?.strategies ?? []).filter((x) => x.family !== "benchmark" && x.family !== "risk_overlay")
              .map((x) => ({ value: x.id, label: `${x.name}（${LIFECYCLE_LABEL[x.lifecycle] ?? "未评审"}）` }))} />
          </Form.Item>
          <Form.Item name="start" rules={[{ required: true }]}><Input placeholder="开始 YYYY-MM-DD" /></Form.Item>
          <Form.Item name="end" rules={[{ required: true }]}><Input placeholder="结束 YYYY-MM-DD" /></Form.Item>
          <Form.Item name="one_way_cost_bps"><Select style={{ width: 100 }} options={[5, 10, 20].map((x) => ({ value: x, label: `${x} bps` }))} /></Form.Item>
          <Button htmlType="submit" type="primary" loading={run.isPending}>运行</Button>
        </Form>
      </Card>
      {result && (
        <Card title="实验结果" style={{ marginTop: 12 }}
          extra={<Tag color={active.validation_grade === "candidate" ? "green" : "default"}>{label[active.validation_grade]}</Tag>}>
          <Typography.Paragraph>{result.validation_reasons.join("；")}</Typography.Paragraph>
          {result.signal_snapshot && (
            <Typography.Paragraph>
              最新确定性信号：{SIGNAL_LABEL[result.signal_snapshot.action] ?? result.signal_snapshot.action}；
              数据截至 {result.signal_snapshot.as_of}；波动闸门{result.signal_snapshot.volatility_gate_passed ? "通过" : "未通过"}。
            </Typography.Paragraph>
          )}
          <Space wrap>{Object.entries(result.metrics).slice(0, 8).map(([k, v]) => <Tag key={k}>{k}: {String(v)}</Tag>)}</Space>
          <EChart height={280} ariaLabel="策略与基准净值及回撤" option={{
            tooltip: { trigger: "axis" }, legend: {},
            xAxis: { type: "category", data: result.curve.map((x) => x.period) }, yAxis: { type: "value" },
            series: [
              { name: "策略净值", type: "line", data: result.curve.map((x) => x.net_value) },
              { name: "基准净值", type: "line", data: result.curve.map((x) => x.benchmark_net_value) },
              { name: "回撤", type: "line", data: result.curve.map((x) => x.drawdown_pct) },
            ] }} />
          <Table size="small" rowKey="segment" pagination={false} dataSource={result.segment_metrics}
            columns={Object.keys(result.segment_metrics[0] ?? {}).map((key) => ({ title: key, dataIndex: key }))} />
          {(result.year_metrics?.length ?? 0) > 0 && (
            <>
              <Typography.Title level={5} style={{ marginTop: 12 }}>按自然年分段</Typography.Title>
              <Table size="small" rowKey="year" pagination={false} dataSource={result.year_metrics}
                columns={Object.keys(result.year_metrics![0] ?? {}).map((key) => ({ title: key, dataIndex: key }))} />
            </>
          )}
          {active.validation_grade === "candidate"
            ? <ReviewPanel experiment={active} />
            : <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
                实验级结果不进入人工评审；只有通过候选门禁的策略才会进入前向校准评审。
              </Typography.Paragraph>}
        </Card>
      )}
      <Card title="历史实验" style={{ marginTop: 12 }}>
        <Table size="small" rowKey="id" dataSource={experiments.data?.experiments ?? []}
          onRow={(x) => ({ onClick: () => setActive(x) })}
          columns={[
            { title: "标的", dataIndex: "thscode" },
            { title: "策略", dataIndex: "strategy_id" },
            { title: "等级", dataIndex: "validation_grade", render: (x) => <Tag>{label[x]}</Tag> },
            { title: "时间", dataIndex: "created_at" },
          ]} />
      </Card>
    </div>
  );
}
