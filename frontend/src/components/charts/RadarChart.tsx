import React from 'react';
import * as echarts from 'echarts/core';
import { RadarChart as EChartsRadarChart } from 'echarts/charts';
import { LegendComponent, RadarComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsReactProps } from 'echarts-for-react';
import EChartCore from './EChartCore';

echarts.use([
  EChartsRadarChart,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const RadarChart: React.FC<EChartsReactProps> = (props) => (
  <EChartCore echarts={echarts} {...props} />
);

export default RadarChart;
