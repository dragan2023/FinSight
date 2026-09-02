import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Input, List, Modal, Select, Space, Tag, message } from "antd";
import { createExport, deleteReports, downloadExport, listReports, previewDeletion, reportVersionDiff, reportVersions, setReportLabels, type LibraryItem } from "../../api/library";
import ErrorNotice from "../../components/ErrorNotice";
import ReportVersionDiff, { type VersionDiff } from "../../components/ReportVersionDiff";

export default function Library() {
  const [q, setQ] = useState("");
  const [format, setFormat] = useState<"markdown" | "html" | "json" | "csv" | "source_markdown">("markdown");
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [labelEditor, setLabelEditor] = useState<LibraryItem | null>(null);
  const [labels, setLabels] = useState("");
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["library", q], queryFn: () => listReports(q) });
  const remove = async (id: string) => {
    try {
      const impact = (await previewDeletion([id])).items[0];
      const detail = impact.blocked_by.length ? `无法删除：${impact.blocked_by.join("；")}` : `将删除此报告以及 ${impact.attachments} 个附件记录、${impact.excerpts} 个本地摘录、${impact.notes} 条笔记和 ${impact.exports} 个导出文件。`;
      Modal.confirm({ title: "确认删除本地报告", content: detail, okButtonProps: { danger: true, disabled: impact.blocked_by.length > 0 }, onOk: async () => { await deleteReports([id]); message.success("报告已删除"); await client.invalidateQueries({ queryKey: ["library"] }); } });
    } catch (error) { message.error(error instanceof Error ? error.message : "无法读取删除影响"); }
  };
  const showDiff = async (item: LibraryItem) => {
    try {
      const versions = (await reportVersions(item.id)).items;
      if (versions.length < 2) { message.info("此研究链目前只有一个版本。"); return; }
      setDiff(await reportVersionDiff(versions[0].id, versions[versions.length - 1].id));
    } catch (error) { message.error(error instanceof Error ? error.message : "无法读取版本差异"); }
  };
  const exportReport = async (item: LibraryItem) => {
    const output = await createExport(item.id, format); const blob = await downloadExport(output.export.id); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = output.export.filename; anchor.click(); URL.revokeObjectURL(url);
  };
  const editLabels = async () => { if (!labelEditor) return; await setReportLabels(labelEditor.id, labels.split(/[，,]/).map(value => value.trim()).filter(Boolean)); await client.invalidateQueries({ queryKey: ["library"] }); setLabelEditor(null); };
  return <><div className="page-heading"><div><div className="eyebrow">LOCAL RESEARCH LIBRARY</div><h1>研究资产库</h1><p>默认导出的是可读报告；“核验材料（MD）”另含已保存的结构化数据和联网检索结果，供人工核对。</p></div></div><Input.Search placeholder="按研究问题检索" value={q} onChange={event => setQ(event.target.value)} style={{ maxWidth: 460 }} />{query.error && <ErrorNotice error={query.error} retry={() => void query.refetch()} />}<Card style={{ marginTop: 18 }}><List dataSource={query.data?.items ?? []} locale={{ emptyText: "尚无本地报告" }} renderItem={item => <List.Item actions={[<Space.Compact key="export"><Select value={format} onChange={setFormat} options={[{value:"markdown",label:"报告（MD）"},{value:"source_markdown",label:"核验材料（MD）"},{value:"html",label:"报告（HTML）"},{value:"json",label:"报告（JSON）"},{value:"csv",label:"指标（CSV）"}]} /><Button onClick={() => void exportReport(item)}>下载</Button></Space.Compact>, <Button key="labels" onClick={() => { setLabelEditor(item); setLabels(item.labels.join(", ")); }}>标签</Button>, <Button key="versions" onClick={() => void showDiff(item)}>版本差异</Button>, <Button key="delete" danger onClick={() => void remove(item.id)}>删除</Button>]}><List.Item.Meta title={item.question} description={<>{`${item.created_at} · v${item.version} · ${item.thscode ?? "无标的"}`} {item.labels.map(label => <Tag key={label}>{label}</Tag>)}</>} /><Tag>{item.coverage}</Tag><Tag>{item.output_integrity}</Tag></List.Item>} /></Card><Modal title="版本结构化差异" open={Boolean(diff)} footer={<Button onClick={() => setDiff(null)}>关闭</Button>} onCancel={() => setDiff(null)}>{diff && <ReportVersionDiff diff={diff} />}</Modal><Modal title="编辑本地标签" open={Boolean(labelEditor)} onCancel={() => setLabelEditor(null)} onOk={() => void editLabels()}><Input aria-label="报告标签" value={labels} onChange={event => setLabels(event.target.value)} placeholder="例如：长期跟踪，待复核" /></Modal></>;
}
