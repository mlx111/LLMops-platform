import React from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { UniversalTransition } from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsReactProps } from 'echarts-for-react';
import EChartCore from './EChartCore';

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  UniversalTransition,
  CanvasRenderer,
]);

const CartesianChart: React.FC<EChartsReactProps> = (props) => (
  <EChartCore echarts={echarts} {...props} />
);

export default CartesianChart;
