import { useMemo } from "react";
import EChart from "./EChart";
import { chartCommon, finColors } from "./tokens";

export interface BacktestPoint { period: string; net_value: number | null; drawdown_pct: number | null; information_coefficient: number | null; }

/** 回测净值、回撤和信息系数的静态数据曲线；不在浏览器重新计算回测。 */
export default function BacktestCurveChart({ points }: { points: BacktestPoint[] }) {
  const option = useMemo(() => {
    const common = chartCommon("light");
    return {
      ...common, color: [finColors.brand, finColors.down, "#d9a13b"],
      legend: common.legend({ bottom: 0 }), grid: common.grid({ bottom: 46 }),
      xAxis: common.categoryAxis({ data: points.map((point) => point.period) }),
      yAxis: [common.valueAxis({ name: "净值 / 回撤" }), common.valueAxis({ name: "信息系数" })],
      tooltip: { ...common.tooltip, trigger: "axis" },
      series: [
        { name: "净值", type: "line", data: points.map((point) => point.net_value), smooth: false, showSymbol: points.length < 32 },
        { name: "回撤", type: "line", data: points.map((point) => point.drawdown_pct), smooth: false, showSymbol: points.length < 32, areaStyle: { opacity: 0.08 } },
        { name: "信息系数", type: "line", yAxisIndex: 1, data: points.map((point) => point.information_coefficient), smooth: false, showSymbol: points.length < 32 },
      ],
    };
  }, [points]);
  return <EChart option={option} height={300} ariaLabel="回测净值、回撤与信息系数曲线" />;
}
