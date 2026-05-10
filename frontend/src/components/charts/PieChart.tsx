import React from 'react';
import * as echarts from 'echarts/core';
import { PieChart as EChartsPieChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent } from 'echarts/components';
import { LabelLayout, UniversalTransition } from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsReactProps } from 'echarts-for-react';
import EChartCore from './EChartCore';

echarts.use([
  EChartsPieChart,
  LegendComponent,
  TooltipComponent,
  LabelLayout,
  UniversalTransition,
  CanvasRenderer,
]);

const PieChart: React.FC<EChartsReactProps> = (props) => (
  <EChartCore echarts={echarts} {...props} />
);

export default PieChart;
