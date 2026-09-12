import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finColors, finChartTheme, finNum } from "./tokens";

/**
 * 排名×价格双轴同框（实践 11 基型）：
 * 上轴 = 排名（#1 在顶部，方向反转，蓝线）；下轴 = 价格（红线）；
 * 可叠加基准虚线（灰，如沪深300 指数化价格）。
 */
export default function DualAxisRankPriceChart({
  labels,
  ranks,
  price,
  priceName,
  benchmark,
  benchmarkName,
  height = 340,
  ariaLabel,
}: {
  labels: string[];
  /** 排名序列（1 最好；null = 当日未上榜）。 */
  ranks: Array<number | null>;
  price: Array<number | null>;
  priceName: string;
  benchmark?: { name: string; values: Array<number | null> };
  benchmarkName?: string;
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const t = finChartTheme.light;
    const common = chartCommon("light");
    const rankValues = ranks.filter((value): value is number => value !== null);
    const maxRank = rankValues.length ? Math.max(...rankValues) : 100;
    const series: Record<string, unknown>[] = [
      {
        name: "热榜排名",
        type: "line",
        yAxisIndex: 0,
        data: ranks,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 2, color: "#3d6fd8" },
        itemStyle: { color: "#3d6fd8" },
      },
      {
        name: priceName,
        type: "line",
        yAxisIndex: 1,
        data: price,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: 2, color: finColors.up },
        itemStyle: { color: finColors.up },
      },
    ];
    if (benchmark) {
      series.push({
        name: benchmarkName ?? benchmark.name,
        type: "line",
        yAxisIndex: 1,
        data: benchmark.values,
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
          "热榜排名",
          priceName,
          ...(benchmark ? [benchmarkName ?? benchmark.name] : []),
        ],
      }),
      grid: common.grid({ top: 26, bottom: 52 }),
      xAxis: common.categoryAxis({ data: labels }),
      yAxis: [
        common.valueAxis({
          name: "排名（#1 顶部）",
          nameTextStyle: { color: t.textMuted, fontSize: 10 },
          inverse: true,
          min: 1,
          max: Math.ceil(maxRank * 1.05),
          splitLine: { show: false },
          axisLabel: {
            color: t.textMuted,
            fontSize: 10,
            formatter: (v: number) => `#${v}`,
          },
        }),
        common.valueAxis({
          name: "价格",
          nameTextStyle: { color: t.textMuted, fontSize: 10 },
          scale: true,
          axisLabel: { color: t.textMuted, fontSize: 10 },
        }),
      ],
      series,
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: { type: "cross", label: { backgroundColor: "#5a6a5f" } },
        formatter: (params: unknown) => {
          const list = params as Array<{
            seriesName?: string;
            value?: number | null;
            axisValue?: string;
          }>;
          const rows = [`<b>${list[0]?.axisValue ?? ""}</b>`];
          for (const entry of list) {
            if (entry.value === undefined || entry.value === null) continue;
            rows.push(
              `${entry.seriesName}：${entry.seriesName === "热榜排名" ? (entry.value === null ? "未上榜" : `#${entry.value}`) : finNum(entry.value)}`,
            );
          }
          return rows.join("<br/>");
        },
      },
    };
  }, [labels, ranks, price, priceName, benchmark, benchmarkName]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? "排名与价格双轴走势图"}
    />
  );
}
