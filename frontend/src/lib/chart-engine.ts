import { init, use as registerCharts } from "echarts/core";
import { BarChart, CandlestickChart, LineChart, PieChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomInsideComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

registerCharts([BarChart, CandlestickChart, LineChart, PieChart, AxisPointerComponent,
  DataZoomInsideComponent, GridComponent, TooltipComponent, CanvasRenderer]);

export { init };
