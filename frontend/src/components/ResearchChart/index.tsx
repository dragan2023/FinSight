import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export interface ChartSeries { name:string; values:(number|null)[]; type?:"line"|"bar" }
export default function ResearchChart({labels,series,unit}:{labels:string[];series:ChartSeries[];unit:string}) {
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{const node=ref.current;if(!node)return;const chart=echarts.init(node);chart.setOption({tooltip:{trigger:"axis"},legend:{},grid:{left:48,right:18,top:40,bottom:48},xAxis:{type:"category",data:labels},yAxis:{type:"value",name:unit},series:series.map(item=>({name:item.name,type:item.type??"line",data:item.values,connectNulls:false}))});const observer=new ResizeObserver(()=>chart.resize());observer.observe(node);return()=>{observer.disconnect();chart.dispose();};},[labels,series,unit]);
  return <div ref={ref} style={{height:280,width:"100%"}} aria-label="研究数据图表" />;
}
