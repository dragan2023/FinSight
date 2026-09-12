import type { ReactNode } from "react";
import { Segmented } from "antd";
import { finColors, toneColor, type FinTone } from "./tokens";

/**
 * 图表面板容器 —— 对应实践的统一版式：
 * 眉题（模块英文名）+ 标题 + 口径说明 + 工具条 + 图表主体 + 出处脚注。
 * 脚注是 FinSight "缺口可见" 原则的落点：口径、数据截至、局限都必须写明。
 */
export function ChartPanel({
  eyebrow,
  title,
  scope,
  toolbar,
  footnote,
  children,
  dark = false,
  tone,
}: {
  eyebrow?: string;
  title: ReactNode;
  scope?: ReactNode;
  toolbar?: ReactNode;
  footnote?: ReactNode;
  children: ReactNode;
  dark?: boolean;
  tone?: string;
}) {
  return (
    <section
      className={dark ? "fin-panel fin-panel--dark" : "fin-panel"}
      style={tone ? { ["--fin-accent" as string]: tone } : undefined}
    >
      <header className="fin-panel__head">
        <div className="fin-panel__titles">
          {eyebrow && <div className="fin-eyebrow">{eyebrow}</div>}
          <h3 className="fin-panel__title">{title}</h3>
          {scope && <p className="fin-panel__scope">{scope}</p>}
        </div>
        {toolbar && <div className="fin-panel__toolbar">{toolbar}</div>}
      </header>
      <div className="fin-panel__body">{children}</div>
      {footnote && <footer className="fin-panel__foot">{footnote}</footer>}
    </section>
  );
}

export interface KpiItem {
  label: string;
  /** 已格式化的展示值（含单位前后的排版由组件处理）。 */
  value: ReactNode;
  sub?: ReactNode;
  tone?: FinTone;
  hint?: string;
}

/** KPI 指标卡行 —— 对应实践 01/04/06/08/12 顶部的统计卡组。 */
export function KpiCards({
  items,
  dark = false,
}: {
  items: KpiItem[];
  dark?: boolean;
}) {
  return (
    <div className={dark ? "fin-kpis fin-kpis--dark" : "fin-kpis"} role="list">
      {items.map((item) => (
        <div
          className="fin-kpi"
          role="listitem"
          key={item.label}
          title={item.hint}
        >
          <span className="fin-kpi__label">{item.label}</span>
          <strong
            className="fin-kpi__value"
            style={
              item.tone && item.tone !== "neutral"
                ? { color: toneColor(item.tone) }
                : undefined
            }
          >
            {item.value}
          </strong>
          {item.sub && <span className="fin-kpi__sub">{item.sub}</span>}
        </div>
      ))}
    </div>
  );
}

/** 范围切换条 —— 对应实践 01 的 30日/90日/160日/全部。 */
export function RangeChips({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: Array<{ label: string; value: string }>;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  return (
    <Segmented
      size="small"
      aria-label={ariaLabel}
      options={options}
      value={value}
      onChange={(next) => onChange(String(next))}
    />
  );
}

/** 图例圆点（用于自定义图例行）。 */
export function LegendDot({
  color,
  label,
  dashed = false,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <span className="fin-legend-dot">
      <i
        style={
          dashed
            ? {
                backgroundImage: `repeating-linear-gradient(90deg, ${color} 0 4px, transparent 4px 7px)`,
              }
            : { background: color }
        }
      />
      {label}
    </span>
  );
}

/** 涨跌语义 chip：+2.38% 红色 / -1.2% 绿色。 */
export function ToneBadge({
  value,
  text,
}: {
  value: number | null | undefined;
  text?: string;
}) {
  const tone =
    value === null ||
    value === undefined ||
    !Number.isFinite(value) ||
    value === 0
      ? "flat"
      : value > 0
        ? "up"
        : "down";
  const arrow = tone === "up" ? "↑" : tone === "down" ? "↓" : "—";
  return (
    <span className={`fin-tone-badge fin-tone-badge--${tone}`}>
      {arrow}{" "}
      {text ??
        (value === null || value === undefined || !Number.isFinite(value)
          ? "--"
          : `${Math.abs(value).toFixed(2)}%`)}
    </span>
  );
}

/** 口径与出处脚注行。 */
export function Provenance({ children }: { children: ReactNode }) {
  return <span className="fin-provenance">{children}</span>;
}

/** 内联分数条（对应实践 10 质量分列）。 */
export function ScoreBar({
  value,
  max = 100,
  tone = finColors.brand,
}: {
  value: number | null;
  max?: number;
  tone?: string;
}) {
  const ratio =
    value === null || !Number.isFinite(value)
      ? 0
      : Math.max(0, Math.min(1, value / max));
  return (
    <span className="fin-scorebar" aria-hidden="true">
      <i style={{ width: `${(ratio * 100).toFixed(1)}%`, background: tone }} />
    </span>
  );
}
