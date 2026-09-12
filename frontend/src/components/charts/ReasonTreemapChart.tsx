import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finNum } from "./tokens";

/**
 * 关键词树图（实践 12 涨停原因分布基型）：面积编码数量，
 * 深红主色系；标签放不下自动隐藏，tooltip 兜底。
 */
export default function ReasonTreemapChart({
  items,
  unit = "家",
  height = 260,
  ariaLabel,
}: {
  items: Array<{ label: string; value: number }>;
  unit?: string;
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const shades = [
      "#b3443c",
      "#c9302c",
      "#d0483d",
      "#dd6a52",
      "#e58a68",
      "#eba583",
      "#f0bfa0",
      "#f4d6c0",
      "#a3776b",
      "#96655c",
      "#8a544e",
    ];
    return {
      ...common,
      series: [
        {
          type: "treemap",
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          itemStyle: {
            borderColor: "#fff",
            borderWidth: 2,
            gapWidth: 2,
            borderRadius: 4,
          },
          label: {
            fontSize: 11,
            color: "#fff",
            formatter: (p: { name: string; value: number }) =>
              `${p.name}\n${finNum(p.value, 0)}${unit}`,
          },
          upperLabel: { show: false },
          data: items.map((item, index) => ({
            name: item.label,
            value: item.value,
            itemStyle: { color: shades[index % shades.length] },
          })),
        },
      ],
      tooltip: {
        ...common.tooltip,
        formatter: (p: { name?: string; value?: number }) =>
          `<b>${p.name}</b><br/>${p.value === undefined ? "--" : `${finNum(p.value, 0)} ${unit}`}`,
      },
    };
  }, [items, unit]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? "原因关键词树图"}
    />
  );
}
