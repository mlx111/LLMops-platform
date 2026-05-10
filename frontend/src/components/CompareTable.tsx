import React from 'react';
import { Table, Tag } from 'antd';
import { useTranslation } from 'react-i18next';
import { CaretUpOutlined, CaretDownOutlined, MinusOutlined } from '@ant-design/icons';
import type { CompareCaseEntry } from '../types';

const deltaStyle = (delta: number) => {
  if (delta > 0) return { color: '#52c41a', icon: <CaretUpOutlined />, prefix: '+' };
  if (delta < 0) return { color: '#ff4d4f', icon: <CaretDownOutlined />, prefix: '' };
  return { color: '#999', icon: <MinusOutlined />, prefix: '' };
};

interface Props {
  entries: CompareCaseEntry[];
}

const CompareTable: React.FC<Props> = ({ entries }) => {
  const { t } = useTranslation();

  if (entries.length === 0) return <div style={{ color: '#999', padding: 8 }}>{t('compareTable.none')}</div>;

  const columns = [
    { title: t('versionCompare.caseId'), dataIndex: 'case_id', key: 'case_id', width: 70 },
    {
      title: t('versionCompare.input'), dataIndex: 'input', key: 'input', ellipsis: true,
      render: (v: string | undefined) => v ?? '-',
    },
    {
      title: t('versionCompare.run1Score'), dataIndex: 'run1_score', key: 'run1_score', width: 110,
      render: (v: number) => v.toFixed(3),
    },
    {
      title: t('versionCompare.run2Score'), dataIndex: 'run2_score', key: 'run2_score', width: 110,
      render: (v: number) => v.toFixed(3),
    },
    {
      title: t('versionCompare.delta'), dataIndex: 'delta', key: 'delta', width: 120,
      render: (v: number) => {
        const { color, icon, prefix } = deltaStyle(v);
        return (
          <span style={{ color, fontWeight: 600 }}>
            {icon} {prefix}{v.toFixed(3)}
          </span>
        );
      },
    },
    {
      title: t('versionCompare.run1Status'), dataIndex: 'run1_status', key: 'run1_status', width: 90,
      render: (s: string) => <Tag color={s === 'passed' ? 'success' : 'error'}>{s}</Tag>,
    },
    {
      title: t('versionCompare.run2Status'), dataIndex: 'run2_status', key: 'run2_status', width: 90,
      render: (s: string) => <Tag color={s === 'passed' ? 'success' : 'error'}>{s}</Tag>,
    },
  ];

  return (
    <Table
      dataSource={entries}
      columns={columns}
      rowKey="case_id"
      size="small"
      pagination={{ pageSize: 10, size: 'small' }}
    />
  );
};

export default CompareTable;
