import React, { useEffect, useMemo, useState } from 'react';
import { Card, Col, Row, Statistic, Table, Tag, Spin } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  CheckCircleOutlined,
  ThunderboltOutlined,
  DollarOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import { dashboardApi, runApi } from '../api/client';
import CartesianChart from '../components/charts/CartesianChart';
import type { DashboardStats, EvalRun } from '../types';

const statusColors: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  paused: 'warning',
};

const Dashboard: React.FC = () => {
  const { t } = useTranslation();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      dashboardApi.stats(),
      runApi.list({ limit: 30 }),
    ]).then(([statsRes, runsRes]) => {
      setStats(statsRes.data);
      setRuns(runsRes.data.items);
    }).finally(() => setLoading(false));
  }, []);

  const completedRuns = useMemo(
    () => runs.filter((r) => r.status === 'completed').reverse(),
    [runs]
  );

  const passRateOption = useMemo(() => ({
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: completedRuns.map((r) => r.name.substring(0, 12)),
      axisLabel: { rotate: 30, fontSize: 10 },
    },
    yAxis: { type: 'value', name: t('dashboard.stats.passRate'), max: 100 },
    series: [{
      name: t('dashboard.stats.passRate'),
      type: 'line',
      smooth: true,
      data: completedRuns.map((r) =>
        r.total_cases > 0 ? Number(((r.passed_cases / r.total_cases) * 100).toFixed(1)) : 0
      ),
      areaStyle: { opacity: 0.15 },
      itemStyle: { color: '#52c41a' },
    }],
  }), [completedRuns, t]);

  const scoreOption = useMemo(() => ({
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: completedRuns.map((r) => r.name.substring(0, 12)),
      axisLabel: { rotate: 30, fontSize: 10 },
    },
    yAxis: { type: 'value', name: t('dashboard.stats.avgScore'), min: 0, max: 1 },
    series: [{
      name: t('dashboard.stats.avgScore'),
      type: 'bar',
      data: completedRuns.map((r) => r.avg_score ?? 0),
      itemStyle: { color: '#1677ff', borderRadius: [4, 4, 0, 0] },
    }],
  }), [completedRuns, t]);

  const latencyOption = useMemo(() => ({
    tooltip: { trigger: 'axis' },
    legend: { data: [t('dashboard.stats.avgScore'), 'Tokens'] },
    xAxis: {
      type: 'category',
      data: completedRuns.map((r) => r.name.substring(0, 12)),
      axisLabel: { rotate: 30, fontSize: 10 },
    },
    yAxis: [
      { type: 'value', name: t('dashboard.stats.avgScore') },
      { type: 'value', name: 'Tokens' },
    ],
    series: [
      {
        name: t('dashboard.stats.avgScore'),
        type: 'bar',
        data: completedRuns.map((r) => r.avg_latency_ms ?? 0),
        itemStyle: { color: '#fa8c16', borderRadius: [4, 4, 0, 0] },
      },
      {
        name: 'Tokens',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: completedRuns.map((r) => r.avg_tokens ?? 0),
        itemStyle: { color: '#722ed1' },
      },
    ],
  }), [completedRuns, t]);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

  const columns = [
    { title: t('dashboard.runName'), dataIndex: 'name', key: 'name' },
    {
      title: t('dashboard.status'), dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={statusColors[s] || 'default'}>{s}</Tag>,
    },
    { title: t('dashboard.score'), dataIndex: 'avg_score', key: 'avg_score', render: (v: number | null) => v?.toFixed(2) ?? '-' },
    { title: t('dashboard.stats.passRate'), key: 'pass', render: (_: unknown, r: EvalRun) => r.total_cases > 0 ? `${((r.passed_cases / r.total_cases) * 100).toFixed(0)}%` : '-' },
    { title: t('dashboard.stats.avgScore'), key: 'latency', render: (_: unknown, r: EvalRun) => r.avg_latency_ms ? `${(r.avg_latency_ms / 1000).toFixed(1)}s` : '-' },
    { title: t('dashboard.created'), dataIndex: 'created_at', key: 'created_at', render: (v: string) => new Date(v).toLocaleString() },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card><Statistic title={t('dashboard.stats.totalRuns')} value={stats?.total_runs ?? 0} prefix={<ExperimentOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title={t('dashboard.stats.passRate')} value={stats?.avg_pass_rate ?? 0} suffix="%" precision={1} prefix={<CheckCircleOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title={t('dashboard.stats.avgScore')} value={stats?.avg_latency_ms ?? 0} suffix="ms" precision={0} prefix={<ThunderboltOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title={t('dashboard.stats.datasets')} value={stats?.total_cases ?? 0} prefix={<DollarOutlined />} /></Card>
        </Col>
      </Row>

      {completedRuns.length >= 2 && (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card title={t('dashboard.stats.passRate')}>
                <CartesianChart option={passRateOption} style={{ height: 280 }} />
              </Card>
            </Col>
            <Col span={12}>
              <Card title={t('dashboard.stats.avgScore')}>
                <CartesianChart option={scoreOption} style={{ height: 280 }} />
              </Card>
            </Col>
          </Row>
          <Card title={t('dashboard.stats.avgScore')} style={{ marginBottom: 16 }}>
            <CartesianChart option={latencyOption} style={{ height: 300 }} />
          </Card>
        </>
      )}

      <Card title={t('dashboard.recentRuns')}>
        <Table dataSource={runs} columns={columns} rowKey="id" size="middle" />
      </Card>
    </div>
  );
};

export default Dashboard;
