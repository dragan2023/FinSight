import { Descriptions } from "antd";

export default function DataFreshness({ sourceTime, fetchedAt, basis, evidenceCount }: { sourceTime?: string | null; fetchedAt?: string | null; basis?: string; evidenceCount: number }) {
  return <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
    { key: "source", label: "源数据时间", children: sourceTime ?? "上游未提供" },
    { key: "fetched", label: "本机获取", children: fetchedAt ?? "尚未获取" },
    { key: "basis", label: "口径", children: basis ?? "未确认" },
    { key: "evidence", label: "证据", children: `${evidenceCount} 份` },
  ]} />;
}
