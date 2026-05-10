import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Card, Table, Tag, Button, Descriptions, Modal, Spin, Space, message } from 'antd';
import { ApartmentOutlined, FileTextOutlined } from '@ant-design/icons';
import { runApi, reportApi } from '../api/client';
import type { EvalRun, EvalResult } from '../types';

const statusColors: Record<string, string> = {
  pending: 'default', running: 'processing', completed: 'success', failed: 'error',
};

const RunResults: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(10);
  const [selectedRun, setSelectedRun] = useState<EvalRun | null>(null);
  const [results, setResults] = useState<EvalResult[]>([]);
  const [resultTotal, setResultTotal] = useState(0);
  const [resultPage, setResultPage] = useState(1);
  const [resultPageSize, setResultPageSize] = useState(10);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadRuns = (page = 1, pageSize = 10) => {
    runApi.list({ skip: (page - 1) * pageSize, limit: pageSize }).then((res) => {
      setRuns(res.data.items);
      setRunTotal(res.data.total);
      setLoading(false);
    });
  };

  useEffect(() => { loadRuns(); }, []);

  const handleRunPageChange = (page: number, pageSize: number) => {
    setRunPage(page);
    setRunPageSize(pageSize);
    loadRuns(page, pageSize);
  };

  const viewDetail = async (run: EvalRun, page = 1, pageSize = 10) => {
    setDetailLoading(true);
    setSelectedRun(run);
    setResultPage(page);
    setResultPageSize(pageSize);
    setDetailOpen(true);
    try {
      const res = await runApi.results(run.id, { skip: (page - 1) * pageSize, limit: pageSize });
      setResults(res.data.items);
      setResultTotal(res.data.total);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleResultPageChange = (page: number, pageSize: number) => {
    if (selectedRun) viewDetail(selectedRun, page, pageSize);
  };

  const runColumns = [
    { title: t('runResults.name'), dataIndex: 'name', key: 'name' },
    { title: t('runResults.status'), dataIndex: 'status', key: 'status', render: (s: string) => <Tag color={statusColors[s]}>{s}</Tag> },
    {
      title: 'Mode', key: 'mode',
      render: (_: unknown, r: EvalRun) => {
        const targetMode = (r.config_json as Record<string, unknown> | null)?.target_mode ?? 'unknown';
        const evalMode = (r.config_json as Record<string, unknown> | null)?.evaluation_mode ?? 'unknown';
        const isDemo = targetMode !== 'live' || evalMode !== 'live';
        return <Tag color={isDemo ? 'orange' : 'blue'}>{isDemo ? 'DEMO' : 'LIVE'}</Tag>;
      },
    },
    { title: t('runResults.passTotal'), key: 'pass', render: (_: unknown, r: EvalRun) => <span>{r.passed_cases}/{r.total_cases}</span> },
    { title: t('runResults.avgScore'), dataIndex: 'avg_score', key: 'score', render: (v: number | null) => v?.toFixed(2) ?? '-' },
    { title: t('runResults.latency'), dataIndex: 'avg_latency_ms', key: 'latency', render: (v: number | null) => v ? `${(v / 1000).toFixed(1)}s` : '-' },
    {
      title: t('runResults.actions'), key: 'action',
      render: (_: unknown, r: EvalRun) => (
        <Space>
          <Button size="small" type="link" onClick={() => viewDetail(r)}>{t('runResults.viewDetail')}</Button>
          <Button
            size="small"
            type="link"
            icon={<ApartmentOutlined />}
            onClick={() => navigate(`/runs/${r.id}/traces`)}
          >
            {t('runResults.viewTraces')}
          </Button>
          {r.status === 'completed' && (
            <Button
              size="small"
              type="link"
              icon={<FileTextOutlined />}
              onClick={async () => {
                try {
                  await reportApi.generate(r.id);
                  message.success(t('reports.generating'));
                  navigate('/reports');
                } catch {
                  // handled by global interceptor
                }
              }}
            >
              {t('runResults.viewReport')}
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const resultColumns = [
    { title: t('runResults.caseId'), dataIndex: 'case_id', key: 'case_id', width: 70 },
    { title: t('runResults.resultStatus'), dataIndex: 'status', key: 'status', width: 70, render: (s: string) => <Tag color={s === 'passed' ? 'success' : s === 'failed' ? 'error' : 'warning'}>{s}</Tag> },
    { title: t('runResults.failureReason'), dataIndex: 'failure_reason', key: 'failure', render: (v: string | null) => v || '-' },
    { title: t('runResults.latency'), dataIndex: 'latency_ms', key: 'latency', width: 80, render: (v: number) => `${v}ms` },
    { title: t('runResults.inTokens'), dataIndex: 'input_tokens', key: 'in_tok', width: 70 },
    { title: t('runResults.outTokens'), dataIndex: 'output_tokens', key: 'out_tok', width: 70 },
    {
      title: t('runResults.scores'), dataIndex: 'scores', key: 'scores',
      render: (scores: Record<string, { score: number; success: boolean }> | null) => {
        if (!scores) return '-';
        return Object.entries(scores).map(([k, v]) => (
          <Tag key={k} color={v.success ? 'green' : 'red'}>{k}: {v.score.toFixed(2)}</Tag>
        ));
      },
    },
  ];

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

  return (
    <div>
      <Card title={t('runResults.title')} extra={<Button onClick={() => loadRuns(runPage, runPageSize)}>{t('runResults.refresh')}</Button>}>
        <Table
          dataSource={runs}
          columns={runColumns}
          rowKey="id"
          size="middle"
          pagination={{
            current: runPage,
            pageSize: runPageSize,
            total: runTotal,
            showSizeChanger: true,
            onChange: handleRunPageChange,
          }}
        />
      </Card>

      <Modal
        title={selectedRun ? t('runResults.runDetail', { name: selectedRun.name }) : ''}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        width={900}
        footer={null}
      >
        {selectedRun && (
          <Spin spinning={detailLoading}>
            <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
              <Descriptions.Item label={t('runResults.status')}><Tag color={statusColors[selectedRun.status]}>{selectedRun.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="Mode">{
                (() => {
                  const tm = (selectedRun.config_json as Record<string, unknown> | null)?.target_mode ?? 'unknown';
                  const em = (selectedRun.config_json as Record<string, unknown> | null)?.evaluation_mode ?? 'unknown';
                  const isDemo = tm !== 'live' || em !== 'live';
                  return <Tag color={isDemo ? 'orange' : 'blue'}>{isDemo ? 'DEMO' : 'LIVE'}</Tag>;
                })()
              }</Descriptions.Item>
              <Descriptions.Item label={t('runResults.passRate')}>{selectedRun.total_cases > 0 ? `${((selectedRun.passed_cases / selectedRun.total_cases) * 100).toFixed(0)}%` : '-'}</Descriptions.Item>
              <Descriptions.Item label={t('runResults.avgScore')}>{selectedRun.avg_score?.toFixed(2) ?? '-'}</Descriptions.Item>
              <Descriptions.Item label={t('runResults.avgLatency')}>{selectedRun.avg_latency_ms ? `${(selectedRun.avg_latency_ms / 1000).toFixed(1)}s` : '-'}</Descriptions.Item>
              <Descriptions.Item label={t('runResults.avgTokens')}>{selectedRun.avg_tokens?.toFixed(0) ?? '-'}</Descriptions.Item>
              <Descriptions.Item label={t('runResults.created')}>{new Date(selectedRun.created_at).toLocaleString()}</Descriptions.Item>
            </Descriptions>
            <Table
              dataSource={results}
              columns={resultColumns}
              rowKey="id"
              size="small"
              pagination={{
                current: resultPage,
                pageSize: resultPageSize,
                total: resultTotal,
                showSizeChanger: true,
                onChange: handleResultPageChange,
                size: 'small',
              }}
            />
          </Spin>
        )}
      </Modal>
    </div>
  );
};

export default RunResults;
