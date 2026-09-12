/**
 * 金融图表设计令牌 —— 提炼自同花顺金融数据 API 文档站 16 个最佳实践
 * （https://fuyao.aicubes.cn/best-practices/，2026-09 抓取）。
 *
 * 要点：
 * - A股语义色：红涨绿跌，全站统一，涨跌方向不允许出现第二种配色。
 * - 模块强调色：复盘/涨停线用深红，热度用紫，龙虎榜用金，板块用蓝，
 *   回测用紫罗兰；品牌绿只用于界面框架，不参与涨跌语义。
 * - 浅色为默认主题；深色仅用于暗色看板（市场复盘等）。
 */

export type FinTheme = "light" | "dark";
export type FinTone = "up" | "down" | "flat" | "warn" | "neutral";

/** 语义与品牌色。 */
export const finColors = {
  brand: "#246653",
  brandSoft: "#e9f0e6",
  /** 上涨 / 净流入（A股红涨）。 */
  up: "#c9302c",
  upSoft: "#e0644f",
  upTint: "#f7e5e0",
  /** 下跌 / 净流出（A股绿跌）。 */
  down: "#1f9d66",
  downSoft: "#53bd90",
  downTint: "#e2f2ea",
  flat: "#8a958c",
  flatTint: "#eef1ec",
  warn: "#d68a2d",
  text: "#223832",
  textMuted: "#6c7a74",
} as const;

/** 各业务模块的强调色（对应实践 01-16 的分模块主色）。 */
export const moduleAccent = {
  company: "#c05a3c",
  sector: "#3d6fd8",
  review: "#d0483d",
  heat: "#8b5cf6",
  dragon: "#c9992e",
  fund: "#246653",
  backtest: "#7c5cd6",
} as const;

export type ModuleAccent = keyof typeof moduleAccent;

/** echarts 主题片段。 */
export interface FinChartTheme {
  text: string;
  textMuted: string;
  axisLine: string;
  splitLine: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  tooltipShadow: string;
}

export const finChartTheme: Record<FinTheme, FinChartTheme> = {
  light: {
    text: "#223832",
    textMuted: "#7d897f",
    axisLine: "#d9e0d6",
    splitLine: "#e8ede4",
    tooltipBg: "#ffffff",
    tooltipBorder: "#dfe5da",
    tooltipText: "#223832",
    tooltipShadow: "rgba(36,74,52,0.14)",
  },
  dark: {
    text: "#e8ebf0",
    textMuted: "#9aa3ae",
    axisLine: "rgba(255,255,255,0.16)",
    splitLine: "rgba(255,255,255,0.08)",
    tooltipBg: "#1c2027",
    tooltipBorder: "rgba(255,255,255,0.14)",
    tooltipText: "#e8ebf0",
    tooltipShadow: "rgba(0,0,0,0.45)",
  },
};

export const finFont = 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif';

/** 数字统一等宽呈现（表格大数、KPI、坐标轴）。 */
export const finNum = (value: number, digits = 2): string =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("zh-CN", {
        maximumFractionDigits: digits,
        minimumFractionDigits: 0,
      }).format(value)
    : "--";

/** 小数比值转百分比文案；null/非有限值显示 --。 */
export const finPct = (ratio: number | null | undefined, digits = 2): string =>
  ratio === null || ratio === undefined || !Number.isFinite(ratio)
    ? "--"
    : `${(ratio * 100).toFixed(digits)}%`;

/** 元转亿元文案（输入为 CNY 绝对值）。 */
export const finYi = (cny: number | null | undefined, digits = 2): string =>
  cny === null || cny === undefined || !Number.isFinite(cny)
    ? "--"
    : `${finNum(cny / 1e8, digits)} 亿`;

export const toneColor = (tone: FinTone): string =>
  tone === "up"
    ? finColors.up
    : tone === "down"
      ? finColors.down
      : tone === "warn"
        ? finColors.warn
        : tone === "flat"
          ? finColors.flat
          : finColors.brand;

/** 按数值正负给出涨跌色（0 视为平盘）。 */
export const signColor = (value: number | null | undefined): string =>
  value === null ||
  value === undefined ||
  !Number.isFinite(value) ||
  value === 0
    ? finColors.flat
    : value > 0
      ? finColors.up
      : finColors.down;

export const signTone = (value: number | null | undefined): FinTone =>
  value === null ||
  value === undefined ||
  !Number.isFinite(value) ||
  value === 0
    ? "flat"
    : value > 0
      ? "up"
      : "down";

/** echarts 公共片段：tooltip / 坐标轴 / 图例 / 动画。 */
export const chartCommon = (theme: FinTheme) => {
  const t = finChartTheme[theme];
  return {
    animationDuration: 480,
    animationDurationUpdate: 380,
    animationEasing: "cubicOut" as const,
    textStyle: { fontFamily: finFont, color: t.text },
    tooltip: {
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: t.tooltipText, fontSize: 12 },
      extraCssText: `box-shadow: 0 6px 20px ${t.tooltipShadow};border-radius:8px;padding:10px 12px;`,
    },
    categoryAxis: (extra?: Record<string, unknown>) => ({
      type: "category" as const,
      axisLine: { lineStyle: { color: t.axisLine } },
      axisTick: { show: false },
      axisLabel: { color: t.textMuted, hideOverlap: true, fontSize: 11 },
      ...extra,
    }),
    valueAxis: (extra?: Record<string, unknown>) => ({
      type: "value" as const,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: t.splitLine } },
      axisLabel: { color: t.textMuted, fontSize: 11 },
      ...extra,
    }),
    legend: (extra?: Record<string, unknown>) => ({
      textStyle: { color: t.textMuted, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 14,
      icon: "roundRect",
      ...extra,
    }),
    grid: (extra?: Record<string, unknown>) => ({
      left: 12,
      right: 18,
      top: 34,
      bottom: 12,
      containLabel: true,
      ...extra,
    }),
  };
};

/** echarts 按值正负着色的柱状/散点 itemStyle。 */
export const signedItemStyle = (
  value: number | null | undefined,
  soft = false,
): { color: string } => ({
  color: soft
    ? value !== null && value !== undefined && value > 0
      ? finColors.upSoft
      : value !== null && value !== undefined && value < 0
        ? finColors.downSoft
        : finColors.flat
    : signColor(value),
});
