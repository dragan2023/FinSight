import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finChartTheme, finColors, type FinTheme } from "./tokens";

export interface TimeSeriesProps {
  labels: string[];
  values: Array<number | null>;
  name: string;
  unit: string;
  /** 基准/对比序列（虚线，灰），例如沪深300。 */
  compare?: { name: string; values: Array<number | null> };
  /** 涨跌语义着色（对首值求变化），如基金净值走势。默认品牌绿。 */
  semantic?: boolean;
  theme?: FinTheme;
  height?: number;
  ariaLabel?: string;
}

/** 时间序列趋势（实践 02/03/11 的折线基型）：细网格 + 软面积 + 可选基准虚线。 */
export default function TimeSeriesChart({
  labels,
  values,
  name,
  unit,
  compare,
  semantic = false,
  theme = "light",
  height = 300,
  ariaLabel,
}: TimeSeriesProps) {
  const option = useMemo(() => {
    const t = finChartTheme[theme];
    const common = chartCommon(theme);
    const base = values.find((value) => value !== null) ?? null;
    const lineColor =
      semantic && base
        ? t === finChartTheme.light
          ? "#c05a3c"
          : "#e0644f"
        : theme === "dark"
          ? "#7fd0a8"
          : finColors.brand;
    const areaFrom = semantic
      ? "rgba(192,90,60,0.16)"
      : theme === "dark"
        ? "rgba(127,208,168,0.16)"
        : "rgba(36,102,83,0.14)";
    const series: Record<string, unknown>[] = [
      {
        name,
        type: "line",
        data: values,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 2, color: lineColor },
        itemStyle: { color: lineColor },
        areaStyle: {
          opacity: 1,
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: areaFrom },
              { offset: 1, color: "rgba(0,0,0,0)" },
            ],
          },
        },
        emphasis: { focus: "series" },
      },
    ];
    if (compare) {
      series.push({
        name: compare.name,
        type: "line",
        data: compare.values,
        connectNulls: false,
        showSymbol: false,
        lineStyle: {
          width: 1.2,
          type: "dashed",
          color: theme === "dark" ? "#8d96a2" : "#9aa69b",
        },
        itemStyle: { color: theme === "dark" ? "#8d96a2" : "#9aa69b" },
      });
    }
    return {
      ...common,
      legend: compare
        ? common.legend({ bottom: 0, data: [name, compare.name] })
        : undefined,
      grid: common.grid({ bottom: compare ? 46 : 26 }),
      xAxis: common.categoryAxis({ data: labels }),
      yAxis: common.valueAxis({
        name: unit,
        nameTextStyle: { color: t.textMuted, fontSize: 10 },
        scale: true,
      }),
      series,
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: {
          type: "cross",
          label: { backgroundColor: theme === "dark" ? "#3a4148" : "#5a6a5f" },
        },
      },
    };
  }, [labels, values, name, unit, compare, semantic, theme]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? `${name}时间序列趋势图`}
    />
  );
}
