import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finPct, moduleAccent, signColor } from "./tokens";

export interface ScatterPoint {
  name: string;
  x: number | null;
  y: number | null;
  /** 气泡权重（可选），如成交额。 */
  size?: number | null;
  /** 分类着色（可选）：按涨跌或分组。 */
  group?: "up" | "down" | "flat" | string;
}

/**
 * 横截面散点（实践 06/09 基型）：
 * - mode "quadrant"：零轴分四象限 + 点标签（行业强度×加速度作战矩阵）。
 * - mode "plain"：普通散点。
 * 点按 signColor 或 group 着色；null 坐标不绘制（缺口可见）。
 */
export default function ScatterChart({
  points,
  mode = "plain",
  xLabel,
  yLabel,
  height = 320,
  ariaLabel,
  tooltipLines,
}: {
  points: ScatterPoint[];
  mode?: "plain" | "quadrant";
  xLabel?: string;
  yLabel?: string;
  height?: number;
  ariaLabel?: string;
  /** 额外的 tooltip 行生成器（如 MA20/MA60、成交额）。 */
  tooltipLines?: (point: ScatterPoint) => string[];
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const data = points
      .filter((point) => point.x !== null && point.y !== null)
      .map((point) => ({
        value: [point.x, point.y],
        name: point.name,
        symbolSize:
          point.size !== null && point.size !== undefined && point.size > 0
            ? undefined
            : 12,
        itemStyle: {
          color:
            point.group === "up" ||
            point.group === "down" ||
            point.group === "flat"
              ? signColor(
                  point.group === "up" ? 1 : point.group === "down" ? -1 : 0,
                )
              : moduleAccent.sector,
          opacity: 0.88,
        },
      }));
    const pct = { formatter: (v: number) => `${(v * 100).toFixed(0)}%` };
    return {
      ...common,
      grid: common.grid({ top: 26, bottom: 34 }),
      xAxis: common.valueAxis({
        name: xLabel,
        nameTextStyle: { color: "#7d897f", fontSize: 10 },
        axisLabel: pct,
        splitLine: {
          show: mode !== "quadrant",
          lineStyle: { color: "#e8ede4" },
        },
      }),
      yAxis: common.valueAxis({
        name: yLabel,
        nameTextStyle: { color: "#7d897f", fontSize: 10 },
        axisLabel: pct,
        splitLine: {
          show: mode !== "quadrant",
          lineStyle: { color: "#e8ede4" },
        },
      }),
      series: [
        {
          type: "scatter",
          data,
          label:
            mode === "quadrant"
              ? {
                  show: true,
                  position: "right",
                  fontSize: 10,
                  color: "#3f4c44",
                  formatter: (p: { name: string }) => p.name,
                }
              : undefined,
          labelLayout: mode === "quadrant" ? { hideOverlap: true } : undefined,
          emphasis: { scale: 1.4 },
        },
      ],
      ...(mode === "quadrant"
        ? {
            markLine: undefined,
          }
        : {}),
      // 象限零轴用 graphic 外置 markLine：挂在 series 上
      tooltip: {
        ...common.tooltip,
        formatter: (p: { name?: string; value?: [number, number] }) => {
          const point = points.find((item) => item.name === p.name);
          if (!point || !p.value) return "";
          const lines = [
            `<b>${point.name}</b>`,
            `X ${finPct(point.x)} · Y ${finPct(point.y)}`,
          ];
          if (tooltipLines) lines.push(...tooltipLines(point));
          return lines.join("<br/>");
        },
      },
    };
  }, [points, mode, xLabel, yLabel, tooltipLines]);
  const merged = useMemo(() => {
    if (mode !== "quadrant") return option;
    const withMark = { ...option };
    const series = (withMark.series as Record<string, unknown>[])[0];
    series.markLine = {
      silent: true,
      symbol: "none",
      label: { show: false },
      lineStyle: { color: "#c8d1c4", type: "solid", width: 1 },
      data: [{ xAxis: 0 }, { yAxis: 0 }],
    };
    return withMark;
  }, [option, mode]);
  return (
    <EChart
      option={merged}
      height={height}
      ariaLabel={ariaLabel ?? "横截面散点图"}
    />
  );
}
