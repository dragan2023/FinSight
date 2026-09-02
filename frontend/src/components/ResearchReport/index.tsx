import { Alert, Card, Collapse, Space, Typography } from "antd";
import {
  type ResearchReportView,
} from "../../api/research";
import DataGaps from "../DataGaps";

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
  const readable = title.split(/[：:]/).at(-1)?.trim() || title;
  return readerText(readable);
}

function formatMetricValue(value: string | null, unit: string): string {
  if (value === null) return "暂缺";
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "暂缺";
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

/**
 * Report renderer (plan 03 section 2.3/2.6): 数值由程序指标块渲染，模型正文
 * 通过 refs 引用；用户界面只展示经整理的结论与待核验事项。未通过结构校验的
 * 原始模型文本只保留在下载核验材料中，部分结果不因个别章节失败而消失。
 */
export default function ResearchReport({
  report,
}: {
  report: ResearchReportView;
}) {
  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="large">
      {report.limitations.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="阅读这份结论前，请注意"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {report.limitations.map((item, index) => (
                <li key={index}>{readerText(item)}</li>
              ))}
            </ul>
          }
        />
      )}

      {report.metrics.length > 0 && (
        <Card size="small" title="关键数据">
          <Space orientation="vertical" size={2} style={{ width: "100%" }}>
            {report.metrics.slice(0, 6).map((metric) => (
              <Typography.Text key={metric.metric}>
                <strong>{metricLabels[metric.metric] ?? "其他财务指标"}</strong>
                {formatMetricValue(metric.value, metric.unit)}
                <Typography.Text type="secondary">
                  {metric.note ? ` · ${readerText(metric.note)}` : ""}
                </Typography.Text>
              </Typography.Text>
            ))}
          </Space>
        </Card>
      )}

      {report.sections.map((section) => (
        <Card
          key={section.id}
          size="small"
          title={readerTitle(section.title)}
        >
          <Space orientation="vertical" size="small" style={{ width: "100%" }}>
            {section.summary && <Typography.Paragraph style={{ marginBottom: 0 }}>{readerText(section.summary)}</Typography.Paragraph>}
            {section.claims.map((claim, index) => (
              <Typography.Text key={index}>
                {readerText(claim.text)}
              </Typography.Text>
            ))}
            {section.counter_evidence.map((item, index) => (
              <Typography.Text key={index} type="warning">
                风险提示：{readerText(item.text)}
              </Typography.Text>
            ))}
            {section.follow_up_questions.length > 0 && (
              <div>
                <Typography.Text strong>待验证事项</Typography.Text>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                  {section.follow_up_questions.map((item, index) => (
                    <li key={index}>{readerText(item)}</li>
                  ))}
                </ul>
              </div>
            )}
          </Space>
        </Card>
      ))}

      {report.data_gaps.length > 0 && <Collapse size="small" items={[
        { key: "details", label: "查看仍待核验的信息", children: <DataGaps gaps={report.data_gaps} /> },
      ]} />}
    </Space>
  );
}
