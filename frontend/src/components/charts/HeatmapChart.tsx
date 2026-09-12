import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finColors, finPct } from "./tokens";

export interface HeatmapRow {
  name: string;
  values: Array<number | null>;
}

/**
 * 区间表现热力带（实践 09 的强度热带基型）：红=强、绿=弱，
 * 空值留白并在脚注可见，不做插值填补。
 */
export default function HeatmapChart({
  rows,
  periods,
  height = 280,
  ariaLabel,
}: {
  rows: HeatmapRow[];
  /** 列（周期）标签，默认 5/20/60 日。 */
  periods?: string[];
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const columns = periods ?? ["5 日", "20 日", "60 日"];
    const data = rows.flatMap((row, y) =>
      row.values
        .map((value, x) => (value === null ? null : [x, y, value]))
        .filter(Boolean),
    );
    return {
      ...common,
      grid: { left: 12, right: 14, top: 10, bottom: 44, containLabel: true },
      xAxis: common.categoryAxis({
        data: columns,
        position: "top",
        axisLabel: { color: "#7d897f", fontSize: 11 },
      }),
      yAxis: common.categoryAxis({
        data: rows.map((row) => row.name),
        axisLabel: {
          color: "#3f4c44",
          fontSize: 11,
          width: 96,
          overflow: "truncate",
        },
      }),
      visualMap: {
        min: -0.1,
        max: 0.1,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 90,
        text: ["强势", "弱势"],
        textStyle: { color: "#7d897f", fontSize: 10 },
        inRange: {
          color: [
            finColors.down,
            "#7cc5a4",
            "#f2f4ec",
            "#e2907f",
            finColors.up,
          ],
        },
      },
      series: [
        {
          type: "heatmap",
          data,
          label: {
            show: true,
            fontSize: 10,
            color: "#31403a",
            formatter: (p: { value: [number, number, number] }) =>
              finPct(p.value[2], 1),
          },
          itemStyle: {
            borderColor: "#ffffff",
            borderWidth: 2,
            borderRadius: 3,
          },
          emphasis: {
            itemStyle: { shadowBlur: 8, shadowColor: "rgba(34,56,50,0.3)" },
          },
        },
      ],
      tooltip: {
        ...common.tooltip,
        formatter: (p: { value: [number, number, number] }) =>
          `<b>${rows[p.value[1]].name}</b> · ${columns[p.value[0]]}<br/>区间表现 ${finPct(p.value[2])}`,
      },
    };
  }, [rows, periods]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? "多周期区间表现热力带"}
    />
  );
}
