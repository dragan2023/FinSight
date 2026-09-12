/**
 * 市场复盘 dashboard 的数据契约与守卫（与组件分离，便于 Fast Refresh）。
 */
export interface MarketReviewDashboardData {
  overview: {
    cards: Array<{
      label: string;
      value: number | null;
      unit: string;
      description: string;
      tone: string;
    }>;
    structure: Array<{ label: string; value: number; tone: string }>;
    interpretation: string;
  };
  ladder: {
    levels: Array<{ label: string; value: number }>;
    stocks: Array<{
      level: number;
      name: string;
      thscode: string;
      next_day_sealed: boolean | null;
    }>;
    description: string;
  };
  hot: {
    top: Array<{
      rank: number | null;
      name: string;
      thscode: string;
      heat: number | null;
      rank_change: number | null;
      trend: string | null;
    }>;
    movement: { up: number; down: number; flat: number };
    description: string;
  };
  dragon_tiger: {
    reason_distribution: Array<{ label: string; value: number }>;
    stocks: Array<{
      name: string;
      thscode: string;
      reason: string;
      window: string;
    }>;
    description: string;
  };
  coverage: Array<{
    label: string;
    status: string;
    count: number;
    gaps: number;
  }>;
  notice: string;
}

/**
 * 市场复盘记录会保存在本地数据库中。早于可视化版本的记录只有空的
 * dashboard 对象，不能把它当作完整图表数据直接渲染，否则一次字段读取
 * 异常就会让整页白屏。
 */
export function isMarketReviewDashboard(
  value: unknown,
): value is MarketReviewDashboardData {
  if (!value || typeof value !== "object") return false;
  const dashboard = value as Partial<MarketReviewDashboardData>;
  return Boolean(
    dashboard.overview &&
    Array.isArray(dashboard.overview.cards) &&
    Array.isArray(dashboard.overview.structure) &&
    typeof dashboard.overview.interpretation === "string" &&
    dashboard.ladder &&
    Array.isArray(dashboard.ladder.levels) &&
    Array.isArray(dashboard.ladder.stocks) &&
    dashboard.hot &&
    Array.isArray(dashboard.hot.top) &&
    dashboard.hot.movement &&
    dashboard.dragon_tiger &&
    Array.isArray(dashboard.dragon_tiger.reason_distribution) &&
    Array.isArray(dashboard.dragon_tiger.stocks) &&
    Array.isArray(dashboard.coverage) &&
    typeof dashboard.notice === "string",
  );
}
