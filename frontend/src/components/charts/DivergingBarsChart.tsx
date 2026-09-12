import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finNum, signColor } from "./tokens";

export interface DivergingItem {
  label: string;
  /** 正=流入/上涨（右，红），负=流出/下跌（左，绿），0=平。 */
  value: number | null;
  /** 次级说明，如 "机构 +10.9亿 / 游资 +13.0亿"。 */
  note?: string | null;
}

/**
 * 中轴分流条（实践 08/16 基型）：左右分流阅读净流入与净流出，
 * 中轴左侧为负、右侧为正，标签附在条端。
 */
export default function DivergingBarsChart({
  items,
  unit,
  height,
  ariaLabel,
  valueFormat,
}: {
  items: DivergingItem[];
  /** 数值单位说明（如 "亿元"）。传入后数值按原样展示并附单位。 */
  unit?: string;
  height?: number;
  ariaLabel?: string;
  valueFormat?: (value: number) => string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    const usable = items.filter((item) => item.value !== null);
    const maxAbs = Math.max(
      1e-9,
      ...usable.map((item) => Math.abs(item.value as number)),
    );
    const format = valueFormat ?? ((value: number) => finNum(value));
    return {
      ...common,
      grid: { left: 8, right: 64, top: 8, bottom: 8, containLabel: true },
      xAxis: common.valueAxis({
        min: -maxAbs * 1.08,
        max: maxAbs * 1.08,
        axisLabel: { show: false },
        splitLine: { show: false },
      }),
      yAxis: common.categoryAxis({
        data: items.map((item) => item.label),
        inverse: true,
        axisLine: { show: false },
        axisLabel: {
          color: "#3f4c44",
          fontSize: 11,
          width: 104,
          overflow: "truncate",
        },
      }),
      series: [
        {
          type: "bar",
          barMaxWidth: 12,
          itemStyle: { borderRadius: 3 },
          data: items.map((item) => ({
            value: item.value,
            itemStyle: { color: signColor(item.value) },
            label: {
              show: item.value !== null,
              position: (item.value ?? 0) >= 0 ? "right" : "left",
              fontSize: 10,
              color: signColor(item.value),
              formatter: () =>
                item.value === null ? "" : `${format(item.value)}${unit ?? ""}`,
            },
          })),
        },
      ],
      tooltip: {
        ...common.tooltip,
        trigger: "item",
        formatter: (p: { name?: string; value?: number }) => {
          const item = items.find((entry) => entry.label === p.name);
          if (!item) return "";
          const value =
            item.value === null ? "--" : `${format(item.value)}${unit ?? ""}`;
          const lines = [`<b>${item.label}</b>`, `净额 ${value}`];
          if (item.note) lines.push(item.note);
          return lines.join("<br/>");
        },
      },
    };
  }, [items, unit, valueFormat]);
  return (
    <EChart
      option={option}
      height={height ?? Math.max(180, items.length * 30 + 40)}
      ariaLabel={ariaLabel ?? "正负分流条形图"}
    />
  );
}
