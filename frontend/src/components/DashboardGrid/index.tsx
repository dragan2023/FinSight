import type { ReactNode } from "react";
import { Card, Empty } from "antd";
import type { PanelGridView } from "../../api/research";

/** 后端下发受限格栅；前端只按格子渲染，不解释或重排研究结论。 */
export default function DashboardGrid({ grid, renderCell }: { grid: PanelGridView; renderCell: (reference: string, component: "chart" | "metrics" | "gaps") => ReactNode; }) {
  return <div className="report-dashboard-grid" style={{ gridTemplateColumns: `repeat(${grid.columns}, minmax(0, 1fr))` }}>
    {grid.cells.map((cell, index) => <Card key={`${cell.title}-${index}`} size="small" title={cell.title} style={{ gridColumn: `${cell.column + 1} / span ${cell.column_span}`, gridRow: `${cell.row + 1} / span ${cell.row_span}`, minWidth: 0 }}>
      {renderCell(cell.reference, cell.component) ?? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该组件暂缺可展示数据" />}
    </Card>)}
  </div>;
}
