import { useMemo, useState } from "react";
import { Segmented } from "antd";
import EChart from "./EChart";
import { chartCommon, finChartTheme, finColors, finNum, finYi } from "./tokens";
import { sma, toNum } from "./series";

export interface CandleBar {
  date: string;
  open: number | null;
  close: number | null;
  low: number | null;
  high: number | null;
  /** 可选成交额（元）；存在时在副图渲染。 */
  turnover?: number | null;
}

const RANGES = [
  { label: "30 日", value: "30" },
  { label: "90 日", value: "90" },
  { label: "160 日", value: "160" },
  { label: "全部", value: "all" },
];

/**
 * 单股行情工作台（实践 01）：K 线 + MA20/MA60 + 成交额副图 + 区间切换。
 * 红涨绿跌；均线由已保存收盘价在本机计算，只做展示不落库。
 */
export default function CandlestickChart({
  bars,
  height = 360,
  ariaLabel,
}: {
  bars: CandleBar[];
  height?: number;
  ariaLabel?: string;
}) {
  const [range, setRange] = useState("all");
  const option = useMemo(() => {
    const usable = bars.filter(
      (bar) => bar.close !== null || bar.open !== null,
    );
    const closes = usable.map((bar) => bar.close);
    const ma20 = sma(closes, 20);
    const ma60 = sma(closes, 60);
    const windowed = range === "all" ? usable : usable.slice(-Number(range));
    const offset = usable.length - windowed.length;
    const dates = windowed.map((bar) => bar.date);
    const t = finChartTheme.light;
    const common = chartCommon("light");
    const candleData = windowed.map((bar) =>
      bar.open === null ||
      bar.close === null ||
      bar.low === null ||
      bar.high === null
        ? "-"
        : [bar.open, bar.close, bar.low, bar.high],
    );
    const turnoverData = windowed.map((bar) => {
      const value = toNum(bar.turnover ?? null);
      if (value === null || bar.open === null || bar.close === null)
        return {
          value: null as number | null,
          itemStyle: { color: finColors.flat },
        };
      return {
        value: value / 1e8,
        itemStyle: {
          color: bar.close >= bar.open ? finColors.upSoft : finColors.downSoft,
        },
      };
    });
    return {
      ...common,
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
        lineStyle: { color: t.axisLine },
      },
      legend: common.legend({
        data: ["日 K", "MA20", "MA60", "成交额"],
        bottom: 0,
        selectedMode: false,
      }),
      grid: [
        common.grid({ top: 16, bottom: 96, right: 20 }),
        common.grid({ top: "62%", bottom: 40, right: 20 }),
      ],
      xAxis: [
        common.categoryAxis({
          gridIndex: 0,
          data: dates,
          boundary: true,
          axisLabel: { show: false },
        }),
        common.categoryAxis({
          gridIndex: 1,
          data: dates,
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
          axisLabel: {
            color: t.textMuted,
            fontSize: 10,
            formatter: (v: number) => `${v}亿`,
          },
          splitNumber: 2,
        }),
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1] }],
      series: [
        {
          name: "日 K",
          type: "candlestick",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: candleData,
          barMaxWidth: 14,
          itemStyle: {
            color: finColors.up,
            color0: finColors.down,
            borderColor: finColors.up,
            borderColor0: finColors.down,
          },
        },
        {
          name: "MA20",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: ma20.slice(offset),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.4, color: "#d9a13b" },
          itemStyle: { color: "#d9a13b" },
        },
        {
          name: "MA60",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: ma60.slice(offset),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.4, color: "#3d8fbd" },
          itemStyle: { color: "#3d8fbd" },
        },
        {
          name: "成交额",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: turnoverData,
          barMaxWidth: 10,
        },
      ],
      tooltip: {
        ...common.tooltip,
        trigger: "axis",
        axisPointer: { type: "cross", label: { backgroundColor: "#5a6a5f" } },
        formatter: (params: unknown) => {
          const list = params as Array<{
            seriesName?: string;
            dataIndex?: number;
            value?: unknown;
          }>;
          const index = list[0]?.dataIndex ?? 0;
          const bar = windowed[index];
          if (!bar) return "";
          const rows = [
            `<b>${bar.date}</b>`,
            `开盘 ${finNum(bar.open ?? NaN)} · 收盘 ${finNum(bar.close ?? NaN)}`,
            `最高 ${finNum(bar.high ?? NaN)} · 最低 ${finNum(bar.low ?? NaN)}`,
          ];
          const turnover = toNum(bar.turnover ?? null);
          if (turnover !== null) rows.push(`成交额 ${finYi(turnover)}`);
          if (bar.close !== null && bar.open !== null) {
            const change = ((bar.close - bar.open) / bar.open) * 100;
            rows.push(
              `<span style="color:${change >= 0 ? finColors.up : finColors.down}">${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(2)}%（对开盘）</span>`,
            );
          }
          return rows.join("<br/>");
        },
      },
    };
  }, [bars, range]);
  return (
    <div>
      <div
        style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}
      >
        <Segmented
          size="small"
          aria-label="K线区间"
          options={RANGES}
          value={range}
          onChange={(v) => setRange(String(v))}
        />
      </div>
      <EChart
        option={option}
        height={height}
        ariaLabel={ariaLabel ?? "日K线与成交额，可滚动缩放查看区间"}
      />
    </div>
  );
}
