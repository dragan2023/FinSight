import { useQuery } from "@tanstack/react-query";
import { Alert, Descriptions, Drawer, Space, Spin, Table, Tag, Typography } from "antd";
import { getEvidence, gapReasonLabels, type DataResultView } from "../../api/marketData";
import ErrorNotice from "../ErrorNotice";

interface EvidenceDrawerProps {
  open: boolean;
  onClose: () => void;
  results: DataResultView[];
}

const statusColors: Record<DataResultView["status"], string> = {
  available: "green",
  partial: "orange",
  unavailable: "red",
};

export default function EvidenceDrawer({ open, onClose, results }: EvidenceDrawerProps) {
  const firstEvidenceId = results.find((result) => result.evidence_ids.length > 0)?.evidence_ids[0] ?? null;
  const evidenceQuery = useQuery({
    queryKey: ["evidence", firstEvidenceId],
    queryFn: () => getEvidence(firstEvidenceId as string),
    enabled: open && firstEvidenceId !== null,
  });
  const evidence = evidenceQuery.data?.evidence ?? null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={640}
      title="数据与证据"
      destroyOnHidden={false}
    >
      <Space orientation="vertical" style={{ width: "100%" }} size="large">
        {results.map((result, index) => (
          <Space key={index} orientation="vertical" style={{ width: "100%" }} size="small">
            <Space size="small" wrap>
              <Tag color={statusColors[result.status]}>{result.status}</Tag>
              <Typography.Text type="secondary">{result.requested_scope.capability}</Typography.Text>
              <Typography.Text type="secondary">
                记录 {result.records.length} 条 · 证据 {result.evidence_ids.length} 份
              </Typography.Text>
            </Space>
            {result.gaps.map((gap) => (
              <Alert
                key={gap.id}
                type="warning"
                showIcon
                message={`缺失：${gapReasonLabels[gap.reason] ?? gap.reason}`}
                description={gap.impact + (gap.remedies.length > 0 ? " 建议：" + gap.remedies.join("；") : "")}
              />
            ))}
            {result.records.length > 0 && (
              <Table
                size="small"
                rowKey={(record) => record.period_key}
                pagination={{ pageSize: 5 }}
                dataSource={result.records.slice(0, 20)}
                columns={[
                  { title: "期间", dataIndex: "period_key", key: "period" },
                  { title: "口径", dataIndex: "basis", key: "basis" },
                  {
                    title: "字段",
                    key: "fields",
                    render: (_, record) => (
                      <Space size="small" wrap>
                        {Object.entries(record.fields).slice(0, 4).map(([name, field]) => (
                          <Typography.Text key={name} type="secondary">
                            {field.label}: {field.value ?? "--"}
                            {field.value !== null ? ` ${field.unit}` : ""}
                          </Typography.Text>
                        ))}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </Space>
        ))}
        {firstEvidenceId && evidenceQuery.isPending && <Spin />}
        {firstEvidenceId && evidenceQuery.error && (
          <ErrorNotice error={evidenceQuery.error} retry={() => void evidenceQuery.refetch()} />
        )}
        {evidence && (
          <Descriptions
            size="small"
            bordered
            column={1}
            title="证据出处"
            items={[
              { key: "capability", label: "能力", children: evidence.capability },
              { key: "endpoint", label: "端点", children: evidence.endpoint_path },
              { key: "basis", label: "口径", children: evidence.basis },
              { key: "source_time", label: "源数据时间", children: evidence.source_time ?? "未提供" },
              {
                key: "fetched_at",
                label: "本机获取时间",
                children: evidence.fetched_at.slice(0, 19).replace("T", " "),
              },
              { key: "policy", label: "原文保留", children: evidence.storage_policy },
              { key: "hash", label: "内容哈希", children: evidence.content_hash.slice(0, 16) + "…" },
            ]}
          />
        )}
      </Space>
    </Drawer>
  );
}
