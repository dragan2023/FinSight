import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finNum, toneColor, type FinTone } from "./tokens";

export interface ToneBar {
  label: string;
  value: number | null;
  tone: FinTone;
}

/** 语义着色柱（实践 01/12 的结构柱基型）：涨红/跌绿/警示橙/平灰。 */
export default function ToneBarsChart({
  items,
  unit,
  height = 240,
  ariaLabel,
}: {
  items: ToneBar[];
  unit?: string;
  height?: number;
  ariaLabel?: string;
}) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    return {
      ...common,
      grid: common.grid({ top: 20, bottom: 20 }),
      xAxis: common.categoryAxis({
        data: items.map((item) => item.label),
        axisLabel: { color: "#3f4c44", fontSize: 11, interval: 0 },
      }),
      yAxis: common.valueAxis({
        axisLabel: {
          color: "#7d897f",
          fontSize: 10,
          formatter: (v: number) => finNum(v, 0),
        },
      }),
      series: [
        {
          type: "bar",
          barMaxWidth: 34,
          itemStyle: { borderRadius: [4, 4, 0, 0] },
          data: items.map((item) => ({
            value: item.value,
            itemStyle: { color: toneColor(item.tone) },
          })),
          label: {
            show: true,
            position: "top",
            fontSize: 10,
            color: "#5a6a5f",
            formatter: (p: { value: number | null }) =>
              p.value === null ? "--" : `${finNum(p.value, 0)}${unit ?? ""}`,
          },
        },
      ],
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: unknown) => {
          const list = params as Array<{
            name: string;
            value: number | null;
            dataIndex: number;
          }>;
          const entry = list[0];
          if (!entry) return "";
          const item = items[entry.dataIndex];
          return `<b>${item.label}</b><br/>${item.value === null ? "--" : `${finNum(item.value)}${unit ?? ""}`}`;
        },
      },
    };
  }, [items, unit]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={ariaLabel ?? "语义着色柱状图"}
    />
  );
}
