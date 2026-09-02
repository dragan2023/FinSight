import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Collapse, Input, Space, Typography } from "antd";
import { useState } from "react";
import { askReportFollowUp, getReportConversation } from "../../api/research";
import ErrorNotice from "../ErrorNotice";

/** A concise reader-facing conversation. Provenance stays available but secondary. */
export default function ReportChat({ reportId }: { reportId: string }) {
  const [question, setQuestion] = useState("");
  const [useWebSearch, setUseWebSearch] = useState(false);
  const client = useQueryClient();
  const history = useQuery({ queryKey: ["report-conversation", reportId], queryFn: () => getReportConversation(reportId) });
  const ask = useMutation({
    mutationFn: () => askReportFollowUp(reportId, { question: question.trim(), use_web_search: useWebSearch }),
    onSuccess: () => { setQuestion(""); void client.invalidateQueries({ queryKey: ["report-conversation", reportId] }); },
  });
  return <Card title="继续追问" size="small">
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Text type="secondary">围绕这份报告继续问。助手会结合报告、这段对话和你本次明确授权的联网搜索回答。</Typography.Text>
      {history.error && <ErrorNotice error={history.error} retry={() => void history.refetch()} />}
      {(history.data?.messages ?? []).map(message => <div key={message.id}>
        <Typography.Text strong>{message.role === "user" ? "你" : "FinSight"}</Typography.Text>
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: "4px 0" }}>{message.content}</Typography.Paragraph>
        {message.role === "assistant" && message.sources.length > 0 && <Collapse size="small" items={[{ key: "sources", label: "查看本次联网材料", children: <ul style={{ margin: 0, paddingLeft: 18 }}>{message.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{" · "}{source.domain}</li>)}</ul> }]} />}
      </div>)}
      <Input.TextArea aria-label="报告追问" value={question} onChange={event => setQuestion(event.target.value)} maxLength={6000}
        autoSize={{ minRows: 2, maxRows: 5 }} placeholder="例如：现金流弱于利润时，接下来应重点核验什么？" />
      <Checkbox checked={useWebSearch} onChange={event => setUseWebSearch(event.target.checked)}>本次允许联网搜索补充最新公开信息（可能产生费用）</Checkbox>
      {useWebSearch && <Alert type="info" showIcon title="仅本次追问会发送搜索关键词" description="搜索结果作为补充线索，不会替代报告内的已保存数据；未配置博查时不会发起请求。" />}
      {ask.error && <ErrorNotice error={ask.error} retry={() => ask.mutate()} />}
      <Button type="primary" loading={ask.isPending} disabled={!question.trim()} onClick={() => ask.mutate()}>发送追问</Button>
    </Space>
  </Card>;
}
