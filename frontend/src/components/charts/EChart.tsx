import { memo, useEffect, useRef } from "react";
import { init } from "echarts/core";
import type * as echarts from "echarts/core";
import "./register";

export interface EChartProps {
  /** 完整 echarts option；请在调用方用 useMemo 稳定引用，避免交互输入触发重绘。 */
  option: echarts.EChartsCoreOption;
  height?: number;
  ariaLabel?: string;
}

/** 环境探测：无 canvas 2d 上下文（jsdom 等）时优雅降级为空容器。 */
function canvasAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext && canvas.getContext("2d"));
  } catch {
    return false;
  }
}

/**
 * 统一 echarts 容器：初始化、自适应尺寸、卸载释放。
 * 无 canvas 环境下降级为空容器；ResizeObserver 缺失时退化为 window resize。
 */
function EChartInner({ option, height = 300, ariaLabel }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || !canvasAvailable()) return;
    let chart: echarts.ECharts | null = null;
    try {
      chart = init(node);
    } catch {
      return;
    }
    chart.setOption(option);
    const resize = () => chart?.resize();
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver === "function") {
      observer = new ResizeObserver(resize);
      observer.observe(node);
    } else {
      window.addEventListener("resize", resize);
    }
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      chart?.dispose();
    };
  }, [option]);
  return (
    <div
      ref={ref}
      style={{ height, width: "100%" }}
      role="img"
      aria-label={ariaLabel ?? "金融数据图表"}
    />
  );
}

const EChart = memo(EChartInner);
export default EChart;
