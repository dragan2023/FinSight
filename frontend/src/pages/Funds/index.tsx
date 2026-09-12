import { useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Input, Row, Select, Space, Table, Tabs, Typography } from "antd";
import SecurityPicker from "../../components/SecurityPicker";
import ErrorNotice from "../../components/ErrorNotice";
import EvidenceDrawer from "../../components/EvidenceDrawer";
import LocalDataVisualSummary from "../../components/LocalDataVisualSummary";
import { compareFunds, getFundNews, getFundOfferings, getFundReportDates, refreshFund, type FundModule, type FundRefreshView, type FundReportDate, type ProviderFundType } from "../../api/funds";
import type { DataResultView, SecurityIdentity } from "../../api/marketData";

const MODULES: Array<{ value: FundModule; label: string }> = [
  { value: "profile", label: "基本资料" }, { value: "portfolio", label: "持仓与配置" },
  { value: "performance", label: "净值与回撤" }, { value: "people", label: "经理、持有人与分红" },
  { value: "financials", label: "基金财务" }, { value: "diagnostics", label: "供应商诊断" },
  { value: "news", label: "资讯元数据" }, { value: "market", label: "ETF 行情" },
];

const isFund = (identity: SecurityIdentity | null) => Boolean(identity && ["fund-otc", "fund-etf", "fund-lof"].includes(identity.asset_type));
const suggestedType = (identity: SecurityIdentity | null): ProviderFundType => identity?.asset_type === "fund-otc" ? "otc" : "exchange";
const display = (value: unknown) => value === null || value === undefined || value === "" ? "--" : String(value);

function resultRows(data: FundRefreshView | null) {
  return Object.entries(data?.modules ?? {}).flatMap(([module, results]) => results.map((result, index) => ({
    key: `${module}-${index}`, module, status: result.status, count: result.records.length,
    gaps: result.gaps.map((gap) => gap.reason).join("、") || "--",
  })));
}

export default function Funds() {
  const [thscode, setThscode] = useState<string>();
  const [identity, setIdentity] = useState<SecurityIdentity | null>(null);
  const [fundType, setFundType] = useState<ProviderFundType>("otc");
  const [modules, setModules] = useState<FundModule[]>(["profile", "performance"]);
  const [rangeName, setRangeName] = useState("year");
  const [reportDates, setReportDates] = useState<FundReportDate[]>([]);
  const [selectedReport, setSelectedReport] = useState<string>();
  const [result, setResult] = useState<FundRefreshView | null>(null);
  const [evidenceResults, setEvidenceResults] = useState<DataResultView[] | null>(null);
  const [comparison, setComparison] = useState<{ notice: string; items: Array<{ fund_id: string; reports: unknown[] }> } | null>(null);
  const [compareCodes, setCompareCodes] = useState("");
  const [news, setNews] = useState<{ notice: string; rows: Array<Record<string, unknown>>; hasMore: boolean; offset?: string } | null>(null);
  const [offerings, setOfferings] = useState<{ result: DataResultView; notice: string } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null);
  const select = (code: string, item: SecurityIdentity | null) => {
    setThscode(code); setIdentity(item); setResult(null); setEvidenceResults(null); setNews(null); setReportDates([]); setSelectedReport(undefined);
    if (isFund(item)) setFundType(suggestedType(item));
  };
  const refresh = async () => {
    if (!thscode || !isFund(identity) || !modules.length) return;
    const selected = reportDates.find((item) => `${item.report_type}|${item.end_date}` === selectedReport);
    try { setBusy(true); setError(null); setResult(await refreshFund(thscode, { provider_fund_type: fundType, modules, range_name: rangeName, ...(selected ? { report_type: selected.report_type, report_end: selected.end_date } : {}) })); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const loadNews = async (offset?: string) => {
    if (!thscode || !isFund(identity)) return;
    try {
      setBusy(true); setError(null); const response = await getFundNews(thscode, fundType, offset);
      const rows = response.result.records.map((record) => Object.fromEntries(Object.entries(record.fields).map(([key, field]) => [key, field.value ?? field.raw_value])));
      setNews({ notice: response.notice, rows: offset && news ? [...news.rows, ...rows] : rows, hasMore: Boolean(response.result.returned_scope.has_more), offset: String(response.result.returned_scope.offset ?? "") || undefined });
    } catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const loadReportDates = async () => {
    if (!thscode || !isFund(identity)) return;
    try { setBusy(true); setError(null); const response = await getFundReportDates(thscode, fundType); setReportDates(response.items); setSelectedReport(undefined); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const loadOfferings = async () => {
    try { setBusy(true); setError(null); setOfferings(await getFundOfferings()); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const compare = async () => {
    const ids = compareCodes.split(",").map((value) => value.trim()).filter(Boolean);
    if (ids.length < 2 || ids.length > 10) return;
    try { setBusy(true); setError(null); setComparison(await compareFunds(ids)); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const rows = resultRows(result);
  const visualResults = Object.values(result?.modules ?? {}).flat();
  return <>
    <div className="page-heading"><div><div className="eyebrow">FUND RESEARCH</div><h1>基金全域研究</h1><p>默认不刷新；只有明确点击后才访问数据源。持仓为最近披露，绝不代表实时持仓。</p></div></div>
    <Alert type="info" showIcon title="基金口径边界" description="单位净值和复权净值分列；无 IOPV 不计算实时折溢价；供应商诊断不参与自研评分；资讯仅保存标题、时间、来源与 URL。" />
    <Card title="1. 确认基金身份" style={{ marginTop: 18 }}><SecurityPicker value={thscode} onChange={select} />
      {identity && !isFund(identity) && <Alert type="error" showIcon title="请选择已确认的基金标的" description="股票和指数不能发送至基金接口。" style={{ marginTop: 12 }} />}
    </Card>
    <Card title="2. 显式刷新模块" style={{ marginTop: 18 }}><Space orientation="vertical" style={{ width: "100%" }}>
      <Row gutter={[12, 12]}><Col xs={24} sm={12}><Typography.Text>供应商基金类型</Typography.Text><Select aria-label="供应商基金类型" value={fundType} onChange={setFundType} style={{ width: "100%" }} options={["otc", "exchange", "reits"].map((value) => ({ value, label: value }))} /></Col><Col xs={24} sm={12}><Typography.Text>净值范围</Typography.Text><Select aria-label="基金净值范围" value={rangeName} onChange={setRangeName} style={{ width: "100%" }} options={["month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"].map((value) => ({ value, label: value }))} /></Col></Row>
      <Checkbox.Group aria-label="基金刷新模块" value={modules} onChange={(values) => setModules(values as FundModule[])} options={MODULES.map((item) => ({ value: item.value, label: item.label }))} />
      <Button type="primary" loading={busy} disabled={!thscode || !isFund(identity) || !modules.length} onClick={() => void refresh()}>刷新所选模块</Button>
      {thscode && isFund(identity) && <a href={`/research?scope=fund_overview&thscode=${encodeURIComponent(thscode)}`}>基于当前资料开始基金研究</a>}
    </Space></Card>
    {error && <ErrorNotice error={error} retry={() => void refresh()} />}
    {result && <><Card title="本次数据覆盖" style={{ marginTop: 18 }}><Typography.Paragraph type="secondary">{result.notice}</Typography.Paragraph><Table size="small" pagination={false} dataSource={rows} columns={[{ title: "数据板块", dataIndex: "module", render: value => MODULES.find(item => item.value === value)?.label ?? value }, { title: "获取状态", dataIndex: "status", render: value => value === "available" ? "已获取" : value === "partial" ? "部分获取" : "暂不可用" }, { title: "可用记录", dataIndex: "count", render: value => `${value} 条` }, { title: "数据缺口", dataIndex: "gaps" }]} /><Space wrap style={{ marginTop: 12 }}><Button onClick={() => setEvidenceResults(visualResults)}>查看数据与证据</Button><Typography.Text type="secondary">原始字段、口径、缺口和出处已下沉到证据抽屉。</Typography.Text></Space><Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>调用预算：{Object.entries(result.budget).map(([name, count]) => `${name} ${count}`).join(" · ")}</Typography.Paragraph></Card><LocalDataVisualSummary results={visualResults} title="基金本地指标与趋势" /></>}
    <Card title="3. 研究内容" style={{ marginTop: 18 }}><Tabs items={[
      { key: "overview", label: "概览与净值", children: <Typography.Paragraph>请刷新“基本资料”和“净值与回撤”模块。单位净值与复权净值在同一日期下分别展示，不将复权净值称为累计净值。</Typography.Paragraph> },
      { key: "portfolio", label: "持仓与配置", children: <Space orientation="vertical" style={{ width: "100%" }}><Typography.Paragraph>先读取可选报告期，再选择某一已披露期；下一次刷新“持仓与配置”时才请求对应历史持仓。未披露部分不补零、不归一。</Typography.Paragraph><Button onClick={() => void loadReportDates()} loading={busy} disabled={!thscode || !isFund(identity)}>读取可选披露期</Button>{reportDates.length > 0 && <Select aria-label="基金披露报告期" value={selectedReport} onChange={setSelectedReport} placeholder="选择已披露报告期（可选）" options={reportDates.map((item) => ({ value: `${item.report_type}|${item.end_date}`, label: `${item.holding === "stock" ? "股票" : "债券"} · ${item.report_type} · ${item.end_date}` }))} style={{ width: "100%" }} />}</Space> },
      { key: "people", label: "经理、持有人与分红", children: <Space orientation="vertical" style={{ width: "100%" }}><Typography.Paragraph>merged 与 separate 持有人记录分别保存、不相加；经理按实际任期展示。</Typography.Paragraph><Typography.Text type="secondary">募集列表仅供信息浏览，不构成投资建议，也不并入已选基金的业绩或持仓结论。</Typography.Text><Space wrap><Button onClick={() => void loadOfferings()} loading={busy}>读取当前募集信息</Button>{offerings && <Button onClick={() => setEvidenceResults([offerings.result])}>查看募集数据与证据</Button>}</Space>{offerings && <Typography.Text type="secondary">{offerings.notice} · {offerings.result.records.length} 条记录</Typography.Text>}</Space> },
      { key: "financials", label: "财务与诊断", children: <Typography.Paragraph>基金领域财务字段不复用上市公司评分。供应商诊断明确标注为供应商评价。</Typography.Paragraph> },
      { key: "news", label: "资讯", children: <Space orientation="vertical" style={{ width: "100%" }}><Typography.Text type="secondary">{news?.notice ?? "点击加载资讯才会调用接口；不会提取正文。"}</Typography.Text><Button onClick={() => void loadNews()} loading={busy} disabled={!thscode || !isFund(identity)}>加载资讯</Button><Table size="small" pagination={false} rowKey={(row) => String(row.id ?? row.title)} dataSource={news?.rows ?? []} columns={[{ title: "标题", dataIndex: "title", render: display }, { title: "来源", dataIndex: "source", render: display }, { title: "时间", dataIndex: "publish_time_ms", render: display }, { title: "URL", dataIndex: "url", render: (value) => value ? <a href={String(value)} target="_blank" rel="noreferrer">打开原文</a> : "--" }]} />{news?.hasMore && <Button onClick={() => void loadNews(news.offset)} loading={busy}>加载下一页</Button>}</Space> },
    ]} /></Card>
    <Card title="4. 多基金比较" style={{ marginTop: 18 }}><Typography.Paragraph type="secondary">最多比较 10 只；仅在本地报告存在共同期间和一致口径时输出指标。</Typography.Paragraph><Input aria-label="比较基金代码" value={compareCodes} onChange={(event) => setCompareCodes(event.target.value)} placeholder="以逗号输入已确认基金代码，例如 510300.SH, 159919.SZ" onPressEnter={() => void compare()} /><Button style={{ marginTop: 12 }} disabled={compareCodes.split(",").filter(Boolean).length < 2} onClick={() => void compare()}>比较已选基金</Button>{comparison && <Typography.Paragraph style={{ marginTop: 12 }}>{comparison.notice}（已读取 {comparison.items.length} 只）</Typography.Paragraph>}</Card>
    {evidenceResults && <EvidenceDrawer open onClose={() => setEvidenceResults(null)} results={evidenceResults} />}
  </>;
}
