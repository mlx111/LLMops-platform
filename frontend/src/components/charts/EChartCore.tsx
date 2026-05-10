import React from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import type { EChartsReactProps } from 'echarts-for-react';

interface Props extends EChartsReactProps {
  echarts: EChartsReactProps['echarts'];
}

const EChartCore: React.FC<Props> = ({ echarts, ...props }) => (
  <ReactEChartsCore echarts={echarts} {...props} />
);

export default EChartCore;
