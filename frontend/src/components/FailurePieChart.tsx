import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import PieChart from './charts/PieChart';

const COLORS = [
  '#ff4d4f', '#ff7a45', '#ffa940', '#ffc53d', '#ffec3d',
  '#bae637', '#73d13d', '#36cfc9', '#1677ff', '#722ed1',
];

interface Props {
  distribution: Record<string, number>;
  title?: string;
}

const FailurePieChart: React.FC<Props> = ({ distribution, title }) => {
  const { t } = useTranslation();
  const entries = useMemo(
    () => Object.entries(distribution).sort((a, b) => b[1] - a[1]),
    [distribution]
  );

  if (entries.length === 0) {
    return <div style={{ color: '#999', padding: 16 }}>{t('failurePieChart.noData')}</div>;
  }

  const option = {
    tooltip: {
      trigger: 'item',
      formatter: (params: { name: string; value: number; percent: number }) =>
        `${params.name.replace(/_/g, ' ')}<br/>${params.value} cases (${params.percent}%)`,
    },
    legend: {
      type: 'scroll' as const,
      orient: 'vertical' as const,
      right: 10,
      top: 20,
      bottom: 20,
      formatter: (name: string) => name.replace(/_/g, ' '),
    },
    series: [
      {
        name: title || t('failurePieChart.title'),
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['40%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: '#fff',
          borderWidth: 2,
        },
        label: {
          show: true,
          position: 'outside',
          formatter: (params: { name: string; percent: number }) =>
            `${params.name.replace(/_/g, ' ')} ${params.percent}%`,
        },
        emphasis: {
          label: { show: true, fontSize: 14, fontWeight: 'bold' },
        },
        data: entries.map(([name, value], index) => ({
          value,
          name,
          itemStyle: {
            color: COLORS[index % COLORS.length],
          },
        })),
      },
    ],
  };

  return <PieChart option={option} style={{ height: 400 }} />;
};

export default FailurePieChart;
