import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finNum } from "./tokens";

export interface DonutSlice {
  name: string;
  value: number | null;
  color?: string;
}

const PALETTE = [
  "#c05a3c",
  "#d9a13b",
  "#246653",
  "#3d6fd8",
  "#8b5cf6",
  "#8a958c",
];

/**
 * 结构环形图（实践 02 资产结构基型）：中心 KPI + 图例清单。
 * null 值切片不绘制，图例显示 --（缺口可见）。
 */
export default function StructureDonutChart({
  slices,
  center,
  sub,
  height = 240,
  ariaLabel,
}: {
  slices: DonutSlice[];
  /** 中心主值（已格式化）。 */
  center: string;
  /** 中心副标题，如 "负债占比"。 */
  sub?: string;
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const usable = slices.filter(
      (slice) => slice.value !== null && Number.isFinite(slice.value),
    );
    const colorOf = (slice: DonutSlice, index: number) =>
      slice.color ?? PALETTE[index % PALETTE.length];
    return {
      ...common,
      title: {
        text: center,
        subtext: sub ?? "",
        left: "30%",
        top: "38%",
        textAlign: "center",
        textStyle: { fontSize: 20, fontWeight: 700, color: "#223832" },
        subtextStyle: { fontSize: 10, color: "#8a958c" },
      },
      series: [
        {
          type: "pie",
          radius: ["60%", "84%"],
          center: ["30%", "50%"],
          avoidLabelOverlap: true,
          padAngle: 2,
          itemStyle: { borderRadius: 4, borderColor: "#fff", borderWidth: 2 },
          label: { show: false },
          emphasis: { scaleSize: 4 },
          data: usable.map((slice, index) => ({
            name: slice.name,
            value: slice.value,
            itemStyle: {
              color: colorOf(
                slice,
                slices.indexOf(slice) < 0 ? index : slices.indexOf(slice),
              ),
            },
          })),
        },
      ],
      tooltip: {
        ...common.tooltip,
        trigger: "item",
        formatter: (p: { name?: string; value?: number; percent?: number }) =>
          `<b>${p.name}</b><br/>${p.value === undefined || p.value === null ? "--" : finNum(p.value)}（${p.percent ?? "--"}%）`,
      },
    };
  }, [slices, center, sub]);
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <EChart
        option={option}
        height={height}
        ariaLabel={ariaLabel ?? "结构占比环形图"}
      />
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexWrap: "wrap",
          gap: "6px 16px",
        }}
      >
        {slices.map((slice, index) => (
          <li
            key={slice.name}
            style={{
              fontSize: 11,
              color: "#5f6d63",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <i
              style={{
                width: 8,
                height: 8,
                borderRadius: 2,
                background: slice.color ?? PALETTE[index % PALETTE.length],
                display: "inline-block",
              }}
            />
            {slice.name}：
            <b style={{ fontVariantNumeric: "tabular-nums" }}>
              {slice.value === null ? "--" : finNum(slice.value)}
            </b>
          </li>
        ))}
      </ul>
    </div>
  );
}
