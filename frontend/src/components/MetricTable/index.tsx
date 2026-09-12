import { Table } from "antd";

export default function MetricTable({ metrics }: { metrics: { metric: string; value: string | null; unit?: string; note?: string | null }[] }) {
  return <Table size="small" pagination={false} rowKey="metric" dataSource={metrics} columns={[
    { title: "指标", dataIndex: "metric" },
    { title: "值", dataIndex: "value", render: (value, row) => value === null ? "--" : `${value}${row.unit ? ` ${row.unit}` : ""}` },
    { title: "说明", dataIndex: "note", render: value => value ?? "--" },
  ]} />;
}
