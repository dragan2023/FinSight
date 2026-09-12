import * as echarts from "echarts/core";
import {
  BarChart,
  CandlestickChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  TreemapChart,
} from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

/** 全库唯一的 echarts 按需注册点（tree-shaking 友好）。 */
echarts.use([
  BarChart,
  CandlestickChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  TreemapChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);
