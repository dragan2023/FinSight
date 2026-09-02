import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Input, Space, Spin, Table, Tag } from "antd";
import { companyResearch, type QualityDimension } from "../../api/company";
import ErrorNotice from "../../components/ErrorNotice";
import ResearchChart from "../../components/ResearchChart";

export default function Company() {
  const [code, setCode] = useState("");
  const [queryCode, setQueryCode] = useState("");
  const query = useQuery({ queryKey: ["company", queryCode], queryFn: () => companyResearch(queryCode), enabled: !!queryCode });
  const dimensions = query.data?.quality.dimensions ?? [];
  return <>
    <div className="page-heading"><div><div className="eyebrow">LOCAL COMPANY RESEARCH</div><h1>公司财务体检</h1><p>仅读取已保存证据；更新数据请在研究工作台主动发起。</p></div></div>
    <Space.Compact style={{ maxWidth: 460, width: "100%" }}><Input value={code} onChange={e => setCode(e.target.value.toUpperCase())} placeholder="已确认标的，例如 600519.SH" /><Button type="primary" onClick={() => setQueryCode(code.trim())}>读取本地资料</Button></Space.Compact>
    {query.isPending && <Spin style={{ margin: 24 }} />}
    {query.error && <ErrorNotice error={query.error} retry={() => void query.refetch()} />}
    {query.data && <><Alert style={{ marginTop: 20 }} type="info" showIcon message={`${query.data.identity.name} · ${query.data.evidence_ids.length} 份本地证据`} description="不自动刷新、不联网、不生成交易建议。" />
      <Card title="可用指标概览" style={{marginTop:18}}><ResearchChart unit="原始展示值" labels={dimensions.flatMap(item=>item.metrics).filter(item=>item.value!==null).map(item=>item.metric)} series={[{name:"本地已保存指标",type:"bar",values:dimensions.flatMap(item=>item.metrics).filter(item=>item.value!==null).map(item=>{const value=Number(item.value);return Number.isFinite(value)?value:null;})}]}/><p className="muted">图表仅显示可转为数值的本地指标；完整单位、口径和缺口请以紧邻的表格为准，空值不会被连线或填补。</p></Card>
      <div className="status-grid" style={{ marginTop: 18 }}>{dimensions.map((dimension: QualityDimension) => <Card key={dimension.id} title={dimension.label} extra={<Tag color={dimension.status === "available" ? "green" : "default"}>{dimension.status === "available" ? "有数据" : dimension.status === "not_applicable" ? "不适用" : "尚无数据"}</Tag>}><Table size="small" pagination={false} rowKey="metric" dataSource={dimension.metrics} columns={[{ title: "指标", dataIndex: "metric" }, { title: "值", dataIndex: "value", render: (value: string | null) => value ?? "--" }, { title: "说明", dataIndex: "note", render: (value: string | null) => value ?? "--" }]} />{dimension.notes.map(note => <p key={note} className="muted">{note}</p>)}</Card>)}</div>
    </>}
  </>;
}
