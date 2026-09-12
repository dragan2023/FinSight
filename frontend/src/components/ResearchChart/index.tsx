import { useMemo } from "react";
import EChart from "../charts/EChart";
import { chartCommon, finColors } from "../charts/tokens";

export interface ChartSeries {
  name: string;
  values: (number | null)[];
  type?: "line" | "bar";
}

/** 通用研究图表（报告规格驱动的基型）；配色与坐标遵循金融设计令牌。 */
export default function ResearchChart({
  labels,
  series,
  unit,
}: {
  labels: string[];
  series: ChartSeries[];
  unit: string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const palette = [
      finColors.brand,
      finColors.up,
      "#d9a13b",
      "#3d6fd8",
      "#8b5cf6",
      "#8a958c",
    ];
    return {
      ...common,
      color: palette,
      legend: series.length > 1 ? common.legend({ bottom: 0 }) : undefined,
      grid: common.grid({ bottom: series.length > 1 ? 46 : 26 }),
      xAxis: common.categoryAxis({ data: labels }),
      yAxis: common.valueAxis({
        name: unit,
        nameTextStyle: { color: "#7d897f", fontSize: 10 },
      }),
      series: series.map((item) => ({
        name: item.name,
        type: item.type ?? "line",
        data: item.values,
        connectNulls: false,
        smooth: item.type !== "bar",
        showSymbol: item.type !== "bar" && item.values.length <= 24,
        symbolSize: 5,
        barMaxWidth: 30,
        lineStyle: { width: 2 },
        itemStyle: {
          borderRadius: item.type === "bar" ? [3, 3, 0, 0] : undefined,
        },
        areaStyle: item.type === "line" ? { opacity: 0.07 } : undefined,
        emphasis: { focus: "series" },
      })),
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
      },
    };
  }, [labels, series, unit]);
  return <EChart option={option} height={280} ariaLabel="研究数据图表" />;
}
