import { Card, Col, Empty, Row, Statistic, Typography } from "antd";
import { useMemo } from "react";
import type { DataResultView, NormalizedFieldView } from "../../api/marketData";
import ResearchChart from "../ResearchChart";

type NumericPoint = { period: string; value: number | null };
type NumericSeries = { label: string; unit: string; points: NumericPoint[] };

const numeric = (field: NormalizedFieldView) => {
  const value = Number(field.value ?? field.raw_value);
  return Number.isFinite(value) ? value : null;
};

function buildSeries(results: DataResultView[]): NumericSeries[] {
  const series = new Map<string, NumericSeries>();
  for (const result of results) for (const record of result.records) {
    for (const field of Object.values(record.fields)) {
      const value = numeric(field);
      // A field which never acquired a business label stays in the evidence
      // drawer. It should not be promoted to a reader-facing chart.
      if (value === null || field.label.startsWith("供应商字段：")) continue;
      const key = `${field.label}|${field.unit}`;
      const current = series.get(key) ?? { label: field.label, unit: field.unit, points: [] };
      current.points.push({ period: record.period_key, value });
      series.set(key, current);
    }
  }
  return [...series.values()]
    .filter(item => item.points.length >= 2)
    .sort((left, right) => right.points.length - left.points.length)
    .slice(0, 3);
}

function buildMetrics(results: DataResultView[]) {
  const metrics: Array<{ label: string; value: number; unit: string }> = [];
  const seen = new Set<string>();
  for (const result of results) for (const record of result.records) for (const field of Object.values(record.fields)) {
    const value = numeric(field); const key = `${field.label}|${field.unit}`;
    if (value === null || seen.has(key) || field.label.startsWith("供应商字段：")) continue;
    seen.add(key); metrics.push({ label: field.label, value, unit: field.unit });
    if (metrics.length === 6) return metrics;
  }
  return metrics;
}

export default function LocalDataVisualSummary({ results, title = "本地指标摘要" }: { results: DataResultView[]; title?: string }) {
  const series = useMemo(() => buildSeries(results), [results]);
  const metrics = useMemo(() => buildMetrics(results), [results]);
  if (!results.some(result => result.records.length)) return null;
  return <Card title={title} style={{ marginTop: 18 }}>
    <Typography.Paragraph type="secondary">仅展示本机能够识别口径和单位的字段；其余原始信息保留在“数据与证据”中，避免将字段代码误当成指标。</Typography.Paragraph>
    {metrics.length > 0 && <Row gutter={[14, 14]}>{metrics.map(metric => <Col xs={12} md={8} key={`${metric.label}-${metric.unit}`}><Card size="small" title={metric.label}><Statistic value={metric.value} suffix={metric.unit || undefined} /></Card></Col>)}</Row>}
    {series.length ? <Row gutter={[18, 18]} style={{ marginTop: 18 }}>{series.map(item => <Col xs={24} xl={series.length === 1 ? 24 : 12} key={`${item.label}-${item.unit}`}><Card type="inner" title={item.label}><ResearchChart unit={item.unit || "数值"} labels={item.points.map(point => point.period)} series={[{ name: item.label, type: "line", values: item.points.map(point => point.value) }]} /></Card></Col>)}</Row> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次已保存数据不足以形成同口径的时间序列；可在数据与证据中查看明细。" />}
  </Card>;
}
