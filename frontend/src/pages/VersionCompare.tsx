import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Card, Col, Row, Select, Spin, Table } from 'antd';
import { SwapOutlined, RiseOutlined, FallOutlined, CaretUpOutlined, CaretDownOutlined, MinusOutlined } from '@ant-design/icons';
import { runApi } from '../api/client';
import CompareTable from '../components/CompareTable';
import RadarChart from '../components/charts/RadarChart';
import type { EvalRun, RunCompareOut } from '../types';

const VersionCompare: React.FC = () => {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [run1Id, setRun1Id] = useState<number | null>(null);
  const [run2Id, setRun2Id] = useState<number | null>(null);
  const [compareData, setCompareData] = useState<RunCompareOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    runApi.list({ limit: 100 })
      .then((res) => {
        const completed = res.data.items.filter(
          (r: EvalRun) => r.status === 'completed'
        );
        setRuns(completed);
        if (completed.length >= 2) {
          setRun1Id(completed[1].id);
          setRun2Id(completed[0].id);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (run1Id && run2Id) {
      setComparing(true);
      runApi.compare(run1Id, run2Id)
        .then((res) => setCompareData(res.data))
        .finally(() => setComparing(false));
    }
  }, [run1Id, run2Id]);

  const radarOption = useMemo(() => {
    if (!compareData) return {};
    const metrics = Object.keys(compareData.metric_diffs).filter(
      (k) => !['avg_latency_ms', 'avg_tokens'].includes(k)
    );

    const run1Values = metrics.map((name) => {
      const d = compareData.metric_diffs[name] ?? 0;
      const r1Score = (compareData.run1.avg_score ?? 0);
      if (name === 'avg_score') return r1Score;
      return Math.max(0, r1Score - d);
    });
    const run2Values = metrics.map((name) => {
      if (name === 'avg_score') return compareData.run2.avg_score ?? 0;
      return run1Values[metrics.indexOf(name)] + (compareData.metric_diffs[name] ?? 0);
    });

    return {
      tooltip: {},
      legend: { data: [compareData.run1.name, compareData.run2.name] },
      radar: {
        indicator: metrics.map((name) => ({
          name: name.replace(/_/g, ' '),
          max: 1,
        })),
      },
      series: [{
        type: 'radar',
        data: [
          { value: run1Values, name: compareData.run1.name },
          { value: run2Values, name: compareData.run2.name },
        ],
      }],
    };
  }, [compareData]);

  const metricDiffColumns = [
    { title: t('versionCompare.metric'), dataIndex: 'metric', key: 'metric', render: (v: string) => v.replace(/_/g, ' ') },
    { title: t('versionCompare.run1'), dataIndex: 'run1', key: 'run1' },
    { title: t('versionCompare.run2'), dataIndex: 'run2', key: 'run2' },
    {
      title: t('versionCompare.delta'), dataIndex: 'diff', key: 'diff',
      render: (v: number) => {
        const color = v > 0 ? '#52c41a' : v < 0 ? '#ff4d4f' : '#999';
        const icon = v > 0 ? <CaretUpOutlined /> : v < 0 ? <CaretDownOutlined /> : <MinusOutlined />;
        return <span style={{ color, fontWeight: 600 }}>{icon} {v > 0 ? '+' : ''}{v.toFixed(4)}</span>;
      },
    },
  ];

  const diffTableData = compareData
    ? Object.entries(compareData.metric_diffs).map(([metric, diff]) => {
        let run1Val = 0;
        let run2Val = 0;
        if (metric === 'avg_score') {
          run1Val = compareData.run1.avg_score ?? 0;
          run2Val = compareData.run2.avg_score ?? 0;
        } else if (metric === 'avg_latency_ms') {
          run1Val = compareData.run1.avg_latency_ms ?? 0;
          run2Val = compareData.run2.avg_latency_ms ?? 0;
        } else if (metric === 'avg_tokens') {
          run1Val = compareData.run1.avg_tokens ?? 0;
          run2Val = compareData.run2.avg_tokens ?? 0;
        } else {
          run2Val = diff;
        }
        return { metric, run1: run1Val, run2: run2Val, diff };
      })
    : [];

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

  return (
    <div>
      <Card title={<><SwapOutlined /> {t('versionCompare.title')}</>} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={10}>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>{t('versionCompare.baseline')}</div>
            <Select
              showSearch
              value={run1Id}
              onChange={setRun1Id}
              style={{ width: '100%' }}
              placeholder={t('versionCompare.selectBaseline')}
              optionFilterProp="label"
              options={runs.map((r) => ({
                value: r.id,
                label: `${r.name} (score: ${(r.avg_score ?? 0).toFixed(2)}, ${r.created_at.slice(0, 10)})`,
              }))}
            />
          </Col>
          <Col span={4} style={{ textAlign: 'center' }}>
            <SwapOutlined style={{ fontSize: 20, color: '#999', marginTop: 24 }} />
          </Col>
          <Col span={10}>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>{t('versionCompare.candidate')}</div>
            <Select
              showSearch
              value={run2Id}
              onChange={setRun2Id}
              style={{ width: '100%' }}
              placeholder={t('versionCompare.selectCandidate')}
              optionFilterProp="label"
              options={runs.map((r) => ({
                value: r.id,
                label: `${r.name} (score: ${(r.avg_score ?? 0).toFixed(2)}, ${r.created_at.slice(0, 10)})`,
              }))}
            />
          </Col>
        </Row>
      </Card>

      {comparing && <Spin style={{ display: 'block', margin: '40px auto' }} />}

      {compareData && !comparing && (
        <>
          <Alert
            type={compareData.improved_cases.length >= compareData.regressed_cases.length ? 'success' : 'warning'}
            message={t('versionCompare.comparisonSummary')}
            description={compareData.summary}
            style={{ marginBottom: 16 }}
          />

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card title={<><RiseOutlined /> {t('versionCompare.improvedCases', { count: compareData.improved_cases.length })}</>}>
                <CompareTable entries={compareData.improved_cases} />
              </Card>
            </Col>
            <Col span={12}>
              <Card title={<><FallOutlined /> {t('versionCompare.regressedCases', { count: compareData.regressed_cases.length })}</>}>
                <CompareTable entries={compareData.regressed_cases} />
              </Card>
            </Col>
          </Row>

          <Card title={t('versionCompare.metricRadar')} style={{ marginBottom: 16 }}>
            <RadarChart option={radarOption} style={{ height: 400 }} />
          </Card>

          <Card title={t('versionCompare.metricDiffs')}>
            <Table
              dataSource={diffTableData}
              columns={metricDiffColumns}
              rowKey="metric"
              size="middle"
              pagination={false}
            />
          </Card>
        </>
      )}
    </div>
  );
};

export default VersionCompare;
