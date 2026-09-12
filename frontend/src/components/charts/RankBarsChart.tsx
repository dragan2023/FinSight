import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finChartTheme } from "./tokens";

export interface RankItem {
  label: string;
  value: number | null;
  /** 徽标文本（如 #1、+12名）。 */
  badge?: string | null;
  note?: string | null;
}

/**
 * 排名条形雷达（实践 07 基型）：名次从上到下，条长编码热度/数值，
 * 渐变条 + 名次徽标，值标注在条端。
 */
export default function RankBarsChart({
  items,
  accent = "#8b5cf6",
  unit,
  height,
  ariaLabel,
  valueFormat,
}: {
  items: RankItem[];
  accent?: string;
  unit?: string;
  height?: number;
  ariaLabel?: string;
  valueFormat?: (value: number) => string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const t = finChartTheme.light;
    const maxAbs = Math.max(
      1e-9,
      ...items.map((item) =>
        item.value !== null && Number.isFinite(item.value)
          ? Math.abs(item.value)
          : 0,
      ),
    );
    const format =
      valueFormat ??
      ((value: number) =>
        new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(
          value,
        ));
    return {
      ...common,
      grid: { left: 8, right: 70, top: 6, bottom: 6, containLabel: true },
      xAxis: common.valueAxis({
        max: maxAbs * 1.06,
        axisLabel: { show: false },
        splitLine: { show: false },
      }),
      yAxis: common.categoryAxis({
        data: items.map(
          (item, index) => `${item.badge ?? `#${index + 1}`}  ${item.label}`,
        ),
        inverse: true,
        axisLine: { show: false },
        axisLabel: {
          color: t.text,
          fontSize: 11,
          width: 150,
          overflow: "truncate",
        },
      }),
      series: [
        {
          type: "bar",
          barMaxWidth: 10,
          data: items.map((item) => ({
            value: item.value,
            itemStyle: {
              borderRadius: 3,
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 1,
                y2: 0,
                colorStops: [
                  { offset: 0, color: accent },
                  { offset: 1, color: `${accent}55` },
                ],
              },
            },
            label: {
              show: item.value !== null && Number.isFinite(item.value),
              position: "right",
              fontSize: 10,
              color: t.textMuted,
              formatter: () =>
                item.value === null || !Number.isFinite(item.value)
                  ? ""
                  : `${format(item.value)}${unit ?? ""}`,
            },
          })),
        },
      ],
      tooltip: {
        ...common.tooltip,
        trigger: "item",
        formatter: (p: { name?: string; dataIndex?: number }) => {
          const item = items[p.dataIndex ?? -1];
          if (!item) return "";
          const value =
            item.value === null || !Number.isFinite(item.value)
              ? "--"
              : `${format(item.value)}${unit ?? ""}`;
          const lines = [`<b>${item.label}</b>`, `数值 ${value}`];
          if (item.note) lines.push(item.note);
          return lines.join("<br/>");
        },
      },
    };
  }, [items, accent, unit, valueFormat]);
  return (
    <EChart
      option={option}
      height={height ?? Math.max(180, items.length * 30 + 36)}
      ariaLabel={ariaLabel ?? "排名条形图"}
    />
  );
}
