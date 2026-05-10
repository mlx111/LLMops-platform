import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Card, Descriptions, Spin, Tag, Table, Typography, Alert } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { traceApi } from '../api/client';
import CartesianChart from '../components/charts/CartesianChart';
import TraceTree from '../components/TraceTree';
import type { Trace, TraceStep } from '../types';

const { Title, Paragraph } = Typography;

const statusColors: Record<string, string> = {
  success: 'success', error: 'error', running: 'processing',
};

const stepColors: Record<string, string> = {
  LLM: '#722ed1', CHAIN: '#1677ff', TOOL: '#fa8c16',
  RETRIEVER: '#52c41a', RERANKER: '#eb2f96', EMBEDDING: '#13c2c2', AGENT: '#fa541c',
};

const TraceDetail: React.FC = () => {
  const { t } = useTranslation();
  const { runId } = useParams<{ runId: string }>();
  const [searchParams] = useSearchParams();
  const traceIdParam = searchParams.get('traceId');

  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<Trace | null>(null);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    traceApi.byRun(Number(runId))
      .then((res) => {
        setTraces(res.data.items);
        if (traceIdParam) {
          const found = res.data.items.find((t: Trace) => t.trace_id === traceIdParam);
          if (found) setSelectedTrace(found);
        }
      })
      .finally(() => setLoading(false));
  }, [runId, traceIdParam]);

  const selectTrace = (trace: Trace) => {
    setSelectedTrace(trace);
    setExpandedSteps(new Set());
  };

  const toggleStep = (stepId: number) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId); else next.add(stepId);
      return next;
    });
  };

  // ── Timeline waterfall chart ──
  const waterfallOption = useMemo(() => {
    if (!selectedTrace || selectedTrace.steps.length === 0) return {};
    const steps = selectedTrace.steps;

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: { name: string; value: number; color: string; seriesName: string }[]) => {
          if (!params || params.length === 0) return '';
          const p = params[0];
          const step = steps.find((s) => s.step_name === p.name);
          return `${p.name}<br/>${p.seriesName}: ${p.value}ms${step ? `<br/>Type: ${step.step_type}` : ''}`;
        },
      },
      grid: { left: 160, right: 40, top: 10, bottom: 24 },
      xAxis: { type: 'value', name: 'ms' },
      yAxis: {
        type: 'category',
        data: steps.map((s) => s.step_name),
        inverse: true,
        axisLabel: { width: 140, overflow: 'truncate' },
      },
      series: [{
        name: 'Duration',
        type: 'bar',
        data: steps.map((s) => {
          const color = stepColors[s.step_type] || '#1677ff';
          return { value: s.latency_ms, itemStyle: { color, borderRadius: [0, 4, 4, 0] } };
        }),
        barMaxWidth: 28,
        label: { show: true, position: 'right', formatter: '{c}ms', fontSize: 11 },
      }],
    };
  }, [selectedTrace]);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

  const traceColumns = [
    { title: t('traceDetail.traceId'), dataIndex: 'trace_id', key: 'trace_id', render: (v: string) => <code>{v}</code> },
    { title: t('traceDetail.case'), dataIndex: 'case_id', key: 'case_id' },
    { title: t('traceDetail.status'), dataIndex: 'status', key: 'status', render: (s: string) => <Tag color={statusColors[s] || 'default'}>{s}</Tag> },
    { title: t('traceDetail.duration'), dataIndex: 'total_latency_ms', key: 'latency', render: (v: number) => `${v}ms` },
    { title: t('traceDetail.totalTokens'), dataIndex: 'total_tokens', key: 'tokens' },
    { title: t('traceDetail.steps'), dataIndex: 'steps', key: 'steps', render: (s: TraceStep[]) => s.length },
    { title: t('traceDetail.model'), dataIndex: 'model', key: 'model', render: (v: string | null) => v || '-' },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Link to="/runs"><ArrowLeftOutlined /> {t('traceDetail.backToRuns')}</Link>
      </div>

      {traces.length === 0 ? (
        <Alert type="info" message={t('traceDetail.noTraces')}
          description={t('traceDetail.noTracesDesc')} />
      ) : (
        <>
          <Card title={t('traceDetail.title', { runId })} style={{ marginBottom: 16 }}>
            <Table dataSource={traces} columns={traceColumns} rowKey="trace_id" size="middle"
              onRow={(trace) => ({
                onClick: () => selectTrace(trace),
                style: { cursor: 'pointer', background: selectedTrace?.trace_id === trace.trace_id ? '#e6f4ff' : undefined },
              })} />
          </Card>

          {selectedTrace && (
            <Card title={t('traceDetail.titleDetail', { traceId: selectedTrace.trace_id })}>
              <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
                <Descriptions.Item label={t('traceDetail.status')}><Tag color={statusColors[selectedTrace.status] || 'default'}>{selectedTrace.status}</Tag></Descriptions.Item>
                <Descriptions.Item label={t('traceDetail.totalLatency')}>{selectedTrace.total_latency_ms}ms</Descriptions.Item>
                <Descriptions.Item label={t('traceDetail.totalTokens')}>{selectedTrace.total_tokens}</Descriptions.Item>
                <Descriptions.Item label={t('traceDetail.model')}>{selectedTrace.model || '-'}</Descriptions.Item>
                <Descriptions.Item label={t('traceDetail.caseId')}>{selectedTrace.case_id || '-'}</Descriptions.Item>
                <Descriptions.Item label={t('traceDetail.created')}>{new Date(selectedTrace.created_at).toLocaleString()}</Descriptions.Item>
              </Descriptions>

              {selectedTrace.error_message && (
                <Alert type="error" message={t('traceDetail.error')} description={selectedTrace.error_message} style={{ marginBottom: 16 }} />
              )}

              {/* Waterfall chart */}
              {selectedTrace.steps.length > 0 && (
                <Card title={t('traceDetail.stepTimeline')} size="small" style={{ marginBottom: 16 }}>
                  <CartesianChart option={waterfallOption} style={{ height: Math.max(200, selectedTrace.steps.length * 36 + 40) }} />
                </Card>
              )}

              <Title level={5}>{t('traceDetail.pipelineSteps')}</Title>
              <TraceTree
                steps={selectedTrace.steps}
                totalLatencyMs={selectedTrace.total_latency_ms}
                expandedSteps={expandedSteps}
                onToggleStep={toggleStep}
              />

              <Title level={5} style={{ marginTop: 16 }}>{t('traceDetail.userInput')}</Title>
              <Paragraph code style={{ whiteSpace: 'pre-wrap' }}>{selectedTrace.user_input}</Paragraph>
            </Card>
          )}
        </>
      )}
    </div>
  );
};

export default TraceDetail;
