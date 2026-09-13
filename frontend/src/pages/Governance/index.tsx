import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Input, Space, Table, Tag, Typography, message } from "antd";
import { useState } from "react";
import ErrorNotice from "../../components/ErrorNotice";
import {
  batchStatistics,
  createTestRunExport,
  currentBatch,
  listBatches,
  listHypotheses,
  listRuleChanges,
  listTestRunExports,
} from "../../api/governance";

const KIND_LABEL: Record<string, string> = {
  strategy: "策略", factor: "因子", rule: "规则", scan: "扫描",
};
const STATUS_LABEL: Record<string, string> = {
  draft: "草稿", frozen: "已冻结", retired: "已退役",
  open: "进行中", closed: "已关闭",
};
const STATUS_COLOR: Record<string, string> = {
  draft: "default", frozen: "green", retired: "red",
  open: "processing", closed: "default",
};

function parse(value: string | undefined): Record<string, string> {
  try {
    return (JSON.parse(value ?? "{}") as Record<string, string>) ?? {};
  } catch {
    return {};
  }
}

export default function Governance() {
  const qc = useQueryClient();
  const [date, setDate] = useState("");
  const [lastExport, setLastExport] = useState<string | null>(null);

  const hypotheses = useQuery({ queryKey: ["governance-hypotheses"], queryFn: listHypotheses });
  const batches = useQuery({ queryKey: ["governance-batches"], queryFn: listBatches });
  const current = useQuery({ queryKey: ["governance-current-batch"], queryFn: currentBatch });
  const changes = useQuery({ queryKey: ["governance-changes"], queryFn: listRuleChanges });
  const runs = useQuery({ queryKey: ["governance-test-runs"], queryFn: listTestRunExports });

  const openBatchId = current.data?.batch_id ?? null;
  const statistics = useQuery({
    queryKey: ["governance-statistics", openBatchId],
    queryFn: () => batchStatistics(openBatchId as string),
    enabled: Boolean(openBatchId),
  });

  const save = useMutation({
    mutationFn: () => createTestRunExport(date ? { date } : {}),
    onSuccess: (result) => {
      setLastExport(result.directory);
      qc.invalidateQueries({ queryKey: ["governance-test-runs"] });
      if (result.sample_is_empty) {
        message.warning("已落盘，但当日没有测试数据（文件里已如实写明原因）");
      } else {
        message.success(`已落盘到 ${result.directory}`);
      }
    },
  });

  const stopCriteria = parse(current.data?.batch?.stop_criteria_json);

  return (
    <div className="page-stack">
      <header className="page-heading">
        <h1>治理与落盘</h1>
        <p>
          预注册台账、评估批次与规则变更都在这里；「一键落盘」把当日测试数据导成三份文件。
          导出**只在你点击时发生**，系统不做任何后台定时任务。
        </p>
      </header>

      <Alert
        type="info"
        showIcon
        message="冻结期改规则会被数据库直接拒绝"
        description="已冻结的假设、冻结/已关闭批次里的规则都不可改写；唯一合法路径是登记一条规则变更，系统会自动关闭旧批次并开启新批次（停止条件默认继承，不会被顺手放松）。"
      />

      <Card title="一键落盘当日测试数据" style={{ marginTop: 12 }}>
        <Typography.Paragraph type="secondary">
          产物目录：<code>data/exports/test-runs/&lt;日期&gt;/</code>，含
          <code>daily.md</code>（给人看）、<code>daily.jsonl</code>（逐条一行，给统计脚本吃）、
          <code>batch.json</code>（批次号、参数哈希、数据快照 sha256、模型配置 ID，审计用）。
        </Typography.Paragraph>
        <Space wrap>
          <Input
            value={date}
            onChange={(event) => setDate(event.target.value)}
            placeholder="留空 = 今天（Asia/Shanghai）"
            style={{ width: 240 }}
            aria-label="导出日期"
          />
          <Button type="primary" loading={save.isPending} onClick={() => save.mutate()}>
            导出当日测试数据
          </Button>
        </Space>
        {save.error && <ErrorNotice error={save.error} />}
        {lastExport && (
          <Typography.Paragraph style={{ marginTop: 12 }}>
            最近一次导出：<code>{lastExport}</code>
          </Typography.Paragraph>
        )}
        {save.data && (
          <Descriptions size="small" column={3} style={{ marginTop: 8 }}>
            <Descriptions.Item label="范围">{save.data.scope_label}</Descriptions.Item>
            <Descriptions.Item label="建议">{save.data.counts.advice}</Descriptions.Item>
            <Descriptions.Item label="实验">{save.data.counts.experiments}</Descriptions.Item>
            <Descriptions.Item label="双轨记录">{save.data.counts.tracks}</Descriptions.Item>
            <Descriptions.Item label="研究预测">{save.data.counts.predictions}</Descriptions.Item>
            <Descriptions.Item label="样本为空">
              {save.data.sample_is_empty ? "是（文件里已写明原因）" : "否"}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card title="历史导出" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey="date"
          dataSource={runs.data?.runs ?? []}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "日期", dataIndex: "date" },
            {
              title: "文件",
              dataIndex: "files",
              render: (files: Record<string, { bytes: number }>) =>
                Object.entries(files ?? {}).map(([name, meta]) => (
                  <Tag key={name}>{`${name} ${Math.round(meta.bytes / 1024)}KB`}</Tag>
                )),
            },
            { title: "目录", dataIndex: "path" },
          ]}
        />
      </Card>

      <Card title="当前评估批次" style={{ marginTop: 12 }}>
        {current.data?.batch ? (
          <>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="批次号">
                <code>{current.data.batch.id.slice(0, 8)}</code>
              </Descriptions.Item>
              <Descriptions.Item label="名称">{current.data.batch.name}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[current.data.batch.status]}>
                  {STATUS_LABEL[current.data.batch.status] ?? current.data.batch.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="开始时间">{current.data.batch.started_at}</Descriptions.Item>
            </Descriptions>
            <Typography.Title level={5}>预注册停止条件（达标即停，不延长也不降标准）</Typography.Title>
            <Table
              size="small"
              rowKey="key"
              pagination={false}
              dataSource={Object.entries(stopCriteria).map(([key, value]) => ({ key, value }))}
              columns={[
                { title: "检查点", dataIndex: "key", width: 220 },
                { title: "停止条件", dataIndex: "value" },
              ]}
            />
            <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
              本批次成员：
              {statistics.data
                ? Object.entries(statistics.data.statistics.member_counts)
                    .map(([kind, count]) => `${kind} ${count}`)
                    .join("；") || "（空）"
                : "读取中…"}
              ；统计只按批次聚合，跨批次合并默认被拒绝。
            </Typography.Paragraph>
          </>
        ) : (
          <Typography.Paragraph type="secondary">
            当前没有进行中的评估批次。**没有批次时拒绝登记建议与实验**——这是刻意的：样本必须能绑定批次才可审计。
          </Typography.Paragraph>
        )}
      </Card>

      <Card title="预注册台账" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey="id"
          dataSource={hypotheses.data?.hypotheses ?? []}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "reference", dataIndex: "reference", render: (value: string) => <code>{value}</code> },
            { title: "类型", dataIndex: "kind", render: (value: string) => KIND_LABEL[value] ?? value },
            {
              title: "状态",
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={STATUS_COLOR[value]}>{STATUS_LABEL[value] ?? value}</Tag>
              ),
            },
            {
              title: "探索性",
              dataIndex: "exploratory",
              render: (value: number) => (value ? <Tag color="orange">是</Tag> : "否"),
            },
            { title: "试验次数", dataIndex: "trial_count" },
            { title: "参数哈希", dataIndex: "params_hash", render: (value: string) => <code>{value}</code> },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          「试验次数」用于多重检验计数：方案要求统计**全部尝试过的**因子/规则数（含放弃的），
          而不是最终保留数。
        </Typography.Paragraph>
      </Card>

      <Card title="规则变更留痕" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey="id"
          dataSource={changes.data?.changes ?? []}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "时间", dataIndex: "changed_at" },
            { title: "字段", dataIndex: "field", render: (value: string) => <code>{value}</code> },
            { title: "旧哈希", dataIndex: "old_hash", render: (value: string) => <code>{value}</code> },
            { title: "新哈希", dataIndex: "new_hash", render: (value: string) => <code>{value}</code> },
            { title: "触发的新批次", dataIndex: "forced_new_batch_id",
              render: (value: string | null) => <code>{(value ?? "—").slice(0, 8)}</code> },
          ]}
        />
      </Card>

      <Card title="历史批次" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey="id"
          dataSource={batches.data?.batches ?? []}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "批次号", dataIndex: "id", render: (value: string) => <code>{value.slice(0, 8)}</code> },
            { title: "名称", dataIndex: "name" },
            {
              title: "状态",
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={STATUS_COLOR[value]}>{STATUS_LABEL[value] ?? value}</Tag>
              ),
            },
            { title: "开始", dataIndex: "started_at" },
            { title: "关闭", dataIndex: "closed_at" },
            {
              title: "被谁接替",
              dataIndex: "superseded_by",
              render: (value: string | null) => (value ? <code>{value.slice(0, 8)}</code> : "—"),
            },
          ]}
        />
      </Card>
    </div>
  );
}
