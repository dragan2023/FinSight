import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finColors } from "./tokens";

export interface TopologyNode { id: string; label: string; category: string; value: number | null; }
export interface TopologyEdge { source: string; target: string; label: string; value: number | null; }

/** 龙虎榜等关系数据的受限拓扑视图；节点和连线均来自后端规格。 */
export default function TopologyGraph({ nodes, edges }: { nodes: TopologyNode[]; edges: TopologyEdge[] }) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const categories = [...new Set(nodes.map((node) => node.category))];
    return {
      ...common,
      tooltip: { ...common.tooltip, formatter: (item: { dataType: string; data: { label?: string; value?: number | null } }) => item.dataType === "edge" ? `关联：${item.data.label ?? "关联"}` : `${item.data.label ?? ""}${item.data.value == null ? "" : `<br/>数值：${item.data.value}`}` },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: false,
        force: { repulsion: 190, edgeLength: [55, 150], gravity: 0.08 },
        categories: categories.map((name) => ({ name })),
        data: nodes.map((node) => ({
          id: node.id, name: node.label, label: node.label, value: node.value,
          category: categories.indexOf(node.category),
          symbolSize: Math.max(28, Math.min(58, 30 + Math.abs(node.value ?? 0))),
          itemStyle: { color: [finColors.brand, finColors.up, "#d9a13b", "#6c7a74"][categories.indexOf(node.category) % 4] },
        })),
        links: edges.map((edge) => ({ source: edge.source, target: edge.target, label: edge.label, value: edge.value })),
        label: { show: true, position: "right", color: "#3f4c44", fontSize: 11 },
        lineStyle: { color: "#aab7ad", curveness: 0.08, opacity: 0.75 },
        edgeLabel: { show: false }, emphasis: { focus: "adjacency" },
      }],
    };
  }, [edges, nodes]);
  return <EChart option={option} height={320} ariaLabel="关系拓扑图" />;
}
