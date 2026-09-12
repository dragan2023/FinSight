import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finColors, finChartTheme } from "./tokens";

/**
 * 净值 × 基准 × 回撤（实践 13-15 回测台基型）：
 * 上图 = 策略净值（绿）与基准净值（灰虚线）；下图 = 水下回撤（红色面积）。
 * 双 grid 联动缩放。
 */
export default function EquityDrawdownChart({
  labels,
  equity,
  benchmark,
  equityName,
  benchmarkName,
  height = 320,
  ariaLabel,
}: {
  labels: string[];
  equity: Array<number | null>;
  benchmark?: Array<number | null>;
  equityName?: string;
  benchmarkName?: string;
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const t = finChartTheme.light;
    const common = chartCommon("light");
    const series: Record<string, unknown>[] = [
      {
        name: equityName ?? "策略净值",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: equity,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 2, color: finColors.brand },
        itemStyle: { color: finColors.brand },
        areaStyle: { opacity: 0.08, color: finColors.brand },
      },
      {
        name: "回撤",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: equity,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 1, color: "rgba(201,48,44,0.75)" },
        itemStyle: { color: "rgba(201,48,44,0.75)" },
        areaStyle: { opacity: 0.18, color: finColors.up },
      },
    ];
    if (benchmark) {
      series.splice(1, 0, {
        name: benchmarkName ?? "基准",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: benchmark,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 1.2, type: "dashed", color: "#9aa69b" },
        itemStyle: { color: "#9aa69b" },
      });
    }
    return {
      ...common,
      legend: common.legend({
        bottom: 0,
        data: [
          equityName ?? "策略净值",
          ...(benchmark ? [benchmarkName ?? "基准"] : []),
          "回撤",
        ],
      }),
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        common.grid({ top: 18, bottom: "38%", right: 20 }),
        common.grid({ top: "66%", bottom: 46, right: 20 }),
      ],
      xAxis: [
        common.categoryAxis({
          gridIndex: 0,
          data: labels,
          axisLabel: { show: false },
        }),
        common.categoryAxis({
          gridIndex: 1,
          data: labels,
          axisLabel: { color: t.textMuted, fontSize: 10 },
        }),
      ],
      yAxis: [
        common.valueAxis({
          gridIndex: 0,
          scale: true,
          axisLabel: { color: t.textMuted, fontSize: 10 },
        }),
        common.valueAxis({
          gridIndex: 1,
          max: 0,
          axisLabel: {
            color: t.textMuted,
            fontSize: 10,
            formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
          },
          splitNumber: 2,
        }),
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1] }],
      series,
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter: (params: unknown) => {
          const list = params as Array<{
            seriesName?: string;
            axisValue?: string;
            value?: number | null;
            dataIndex?: number;
          }>;
          const rows = [`<b>${list[0]?.axisValue ?? ""}</b>`];
          for (const entry of list) {
            if (entry.value === null || entry.value === undefined) continue;
            const text =
              entry.seriesName === "回撤"
                ? `${(entry.value * 100).toFixed(2)}%`
                : String(entry.value);
            rows.push(`${entry.seriesName}：${text}`);
          }
          return rows.join("<br/>");
        },
      },
    };
  }, [labels, equity, benchmark, equityName, benchmarkName]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? "净值与回撤双栏图"}
    />
  );
}
