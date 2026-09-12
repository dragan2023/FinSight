import { useState } from "react";
import { Alert, Button, Card, Collapse, Drawer, Segmented, Space, Spin, Typography, message } from "antd";
import { type ChartSpecView, type ResearchReportView } from "../../api/research";
import { getEvidence, type EvidenceView } from "../../api/marketData";
import { createExport, downloadExport } from "../../api/library";
import DataGaps from "../DataGaps";
import ResearchChart from "../ResearchChart";
import DashboardGrid from "../DashboardGrid";
import { BacktestCurveChart, HeatmapChart, TopologyGraph } from "../charts";

const metricLabels: Record<string, string> = {
  revenue_yoy: "营收同比", net_profit_yoy: "归母净利润同比", gross_margin: "毛利率",
  net_margin: "净利率", debt_ratio: "资产负债率", ocf_vs_net_profit: "经营现金流 / 净利润",
  ocf_vs_parent_profit: "经营现金流 / 归母净利润", range_change: "区间涨跌", max_drawdown: "最大回撤",
  revenue_y3: "三年营收变化", net_profit_y3: "三年净利润变化", interest_expenses: "利息支出",
  pe_ttm: "市盈率（TTM）", pb_mrq: "市净率（MRQ）", dividend_yield: "股息率",
};

const readerTermLabels: Record<string, string> = {
  annual_report: "年报披露口径", income: "利润表", balance: "资产负债表", cashflow: "现金流量表",
  revenue_y3: "三年营收变化", net_profit_y3: "三年净利润变化", revenue_yoy: "营收同比",
  net_profit_yoy: "归母净利润同比", net_margin: "净利率", debt_ratio: "资产负债率",
  ocf_vs_net_profit: "经营现金流与净利润的比值", range_change: "区间涨跌", max_drawdown: "最大回撤",
};

function readerText(text: string): string {
  let result = text.replace(/（?引用[：:][^）)\n]+[）)]?/g, "");
  for (const [term, label] of Object.entries(readerTermLabels)) result = result.replaceAll(term, label);
  return result.replace(/\b(supported|conditional|unavailable|unstructured)\b/g, "").trim();
}

function readerTitle(title: string): string {
  return readerText(title.split(/[：:]/).at(-1)?.trim() || title);
}

function formatMetricValue(value: string | null, unit: string): string {
  if (value === null || !Number.isFinite(Number(value))) return "暂缺";
  const numberValue = Number(value);
  if (unit === "ratio") return `${(numberValue * 100).toFixed(1)}%`;
  if (unit === "CNY/share") return `${numberValue.toFixed(2)} 元/股`;
  if (unit === "CNY") {
    const absolute = Math.abs(numberValue);
    if (absolute >= 100_000_000) return `${(numberValue / 100_000_000).toFixed(2)} 亿元`;
    if (absolute >= 10_000) return `${(numberValue / 10_000).toFixed(2)} 万元`;
    return `${numberValue.toFixed(2)} 元`;
  }
  if (unit === "times" || unit === "x") return `${numberValue.toFixed(2)} 倍`;
  return `${numberValue.toFixed(2)}${unit ? ` ${unit}` : ""}`;
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 报告正文与受限图表规格渲染。所有数值均来自已校验的指标与证据。 */
export default function ResearchReport({ report }: { report: ResearchReportView }) {
  const [mode, setMode] = useState<"charts" | "text">("charts");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceView[]>([]);
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [exporting, setExporting] = useState(false);
  const specs = report.chart_specs ?? [];
  const metricsBlock = report.metrics.length > 0 ? <Space orientation="vertical" size={2} style={{ width: "100%" }}>
    {report.metrics.slice(0, 6).map((metric) => <Typography.Text key={metric.metric}>
      <strong>{metricLabels[metric.metric] ?? "其他财务指标"}</strong>：<span>{formatMetricValue(metric.value, metric.unit)}</span>
      <Typography.Text type="secondary">{metric.note ? ` · ${readerText(metric.note)}` : ""}</Typography.Text>
    </Typography.Text>)}
  </Space> : null;

  const openEvidence = async () => {
    setDrawerOpen(true);
    setLoadingEvidence(true);
    try { setEvidence(await Promise.all(report.evidence_ids.slice(0, 16).map(getEvidence))); }
    catch (error) { message.error(error instanceof Error ? error.message : "无法读取图表依据"); }
    finally { setLoadingEvidence(false); }
  };
  const exportHtml = async () => {
    setExporting(true);
    try {
      const output = await createExport(report.id, "html");
      download(await downloadExport(output.export.id), output.export.filename);
      message.success("已导出不含运行时脚本的网页报告");
    } catch (error) { message.error(error instanceof Error ? error.message : "导出失败"); }
    finally { setExporting(false); }
  };
  const renderChartBody = (spec: ChartSpecView) => {
    if (spec.chart_type === "topology") return <TopologyGraph nodes={spec.topology_nodes ?? []} edges={spec.topology_edges ?? []} />;
    if (spec.chart_type === "backtest_curve") return <BacktestCurveChart points={spec.backtest_points ?? []} />;
    if (spec.chart_type === "relation_heat" && spec.relation_heat) return <HeatmapChart periods={spec.relation_heat.x_labels} rows={spec.relation_heat.y_labels.map((name, index) => ({ name, values: spec.relation_heat!.values[index] ?? [] }))} ariaLabel={spec.title} />;
    const metrics = spec.metric_refs.map((ref) => report.metrics.find((metric) => metric.metric === ref)).filter((metric): metric is NonNullable<typeof metric> => Boolean(metric));
    if (!metrics.length) return null;
    return <ResearchChart unit="原始指标值" labels={metrics.map((metric) => metricLabels[metric.metric] ?? "其他指标")} series={[{ name: spec.title, type: spec.chart_type === "line" ? "line" : "bar", values: metrics.map((metric) => Number.isFinite(Number(metric.value)) ? Number(metric.value) : null) }]} />;
  };
  const renderChartCard = (spec: ChartSpecView) => {
    const body = renderChartBody(spec);
    return body ? <Card key={spec.id} size="small" title={spec.title} extra={<Button type="link" size="small" onClick={() => void openEvidence()}>查看图表依据</Button>}>{body}</Card> : null;
  };
  const dashboards = specs.filter((spec) => spec.chart_type === "panel_grid" && spec.panel_grid).map((spec) => <DashboardGrid key={spec.id} grid={spec.panel_grid!} renderCell={(reference, component) => {
    if (component === "metrics") return metricsBlock;
    if (component === "gaps") return <DataGaps gaps={report.data_gaps} />;
    const target = specs.find((item) => item.id === reference);
    return target ? renderChartBody(target) : null;
  }} />);

  return <Space orientation="vertical" style={{ width: "100%" }} size="large">
    {report.limitations.length > 0 && <Alert type="warning" showIcon title="阅读这份结论前，请注意" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{report.limitations.map((item, index) => <li key={index}>{readerText(item)}</li>)}</ul>} />}
    <Space wrap><Segmented value={mode} onChange={(value) => setMode(value as "charts" | "text")} options={[{ value: "charts", label: "图表版" }, { value: "text", label: "文字版" }]} /><Button loading={exporting} onClick={() => void exportHtml()}>导出网页报告</Button></Space>
    {mode === "charts" && <>{dashboards}{dashboards.length === 0 && report.metrics.length > 0 && <Card size="small" title="关键数据">{metricsBlock}</Card>}{specs.filter((spec) => spec.chart_type !== "panel_grid").map(renderChartCard)}</>}
    {mode === "text" && report.metrics.length > 0 && <Card size="small" title="关键数据">{metricsBlock}</Card>}
    {report.sections.map((section) => <Card key={section.id} size="small" title={readerTitle(section.title)}><Space orientation="vertical" size="small" style={{ width: "100%" }}>
      {section.summary && <Typography.Paragraph style={{ marginBottom: 0 }}>{readerText(section.summary)}</Typography.Paragraph>}
      {section.claims.map((claim, index) => <Typography.Text key={index}>{readerText(claim.text)}</Typography.Text>)}
      {section.counter_evidence.map((item, index) => <Typography.Text key={index} type="warning">风险提示：{readerText(item.text)}</Typography.Text>)}
      {section.follow_up_questions.length > 0 && <div><Typography.Text strong>待验证事项</Typography.Text><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{section.follow_up_questions.map((item, index) => <li key={index}>{readerText(item)}</li>)}</ul></div>}
    </Space></Card>)}
    {report.data_gaps.length > 0 && <Collapse size="small" items={[{ key: "details", label: "查看仍待核验的信息", children: <DataGaps gaps={report.data_gaps} /> }]} />}
    <Drawer title="图表依据" open={drawerOpen} onClose={() => setDrawerOpen(false)} size="large">{loadingEvidence ? <Spin /> : <Space orientation="vertical" style={{ width: "100%" }}>
      {evidence.length === 0 ? <Typography.Text type="secondary">本图尚无可读取的已保存证据。</Typography.Text> : evidence.map((item) => <Card key={item.evidence.id} size="small" title="已保存数据依据"><Space orientation="vertical" size={2}><Typography.Text>数据能力：{item.evidence.capability}</Typography.Text><Typography.Text>获取时间：{item.evidence.fetched_at}</Typography.Text><Typography.Text>记录数：{item.evidence.records.length}</Typography.Text><Typography.Text type="secondary">口径：{item.evidence.basis}</Typography.Text></Space></Card>)}
    </Space>}</Drawer>
  </Space>;
}
