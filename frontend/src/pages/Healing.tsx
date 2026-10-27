import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  Select,
  Spin,
  Table,
  Tag,
  Typography,
  Space,
  Empty,
  Timeline,
  Collapse,
  Alert,
  message,
} from 'antd';
import {
  ToolOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { healingApi, runApi } from '../api/client';
import type { EvalRun, HealingResult, ProposedFix, ExecutedFix } from '../types';

const { Text, Paragraph } = Typography;

// judge → Tag 颜色（绿=成功，橙=兜底，红=超时）
const judgeColor = (judge: string): string => {
  if (judge === 'agentic_self_heal') return 'green';
  if (judge.includes('fallback')) return 'orange';
  if (judge.includes('timeout')) return 'red';
  return 'blue';
};

// regression_verdict → Tag 颜色（绿=改进，红=回退，灰=持平/待定）
const verdictColor = (verdict: string): string => {
  if (verdict === 'improved') return 'green';
  if (verdict === 'declined') return 'red';
  if (verdict === 'unchanged') return 'default';
  return 'orange'; // pending
};

const Healing: React.FC = () => {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<HealingResult | null>(null);

  useEffect(() => {
    runApi
      .list({ limit: 100 })
      .then((res) => {
        // 优先展示有失败 case 的 run
        const sorted = (res.data.items as EvalRun[])
          .slice()
          .sort((a, b) => (b.failed_cases ?? 0) - (a.failed_cases ?? 0));
        setRuns(sorted);
      })
      .finally(() => setLoadingRuns(false));
  }, []);

  const run = async () => {
    if (!selectedRunId) return;
    setLoading(true);
    try {
      const res = await healingApi.run(selectedRunId);
      setResult(res.data);
      message.success(t('healing.done'));
    } catch {
      // 全局拦截器已提示
    } finally {
      setLoading(false);
    }
  };

  const proposedColumns = [
    {
      title: t('healing.approvalId'),
      dataIndex: 'approval_id',
      key: 'approval_id',
      width: 180,
      render: (v?: string) => v ?? '-',
    },
    {
      title: t('healing.toolName'),
      dataIndex: 'tool_name',
      key: 'tool_name',
      render: (v?: string) => (v ? <Tag>{v}</Tag> : '-'),
    },
    {
      title: t('healing.question'),
      dataIndex: 'question',
      key: 'question',
      ellipsis: true,
    },
    {
      title: t('healing.risk'),
      dataIndex: 'risk_level',
      key: 'risk_level',
      width: 90,
      render: (v?: string) =>
        v ? <Tag color={v === 'high' ? 'red' : 'green'}>{v}</Tag> : '-',
    },
    {
      title: t('healing.status'),
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v?: string) => (v ? <Tag color="orange">{v}</Tag> : '-'),
    },
  ];

  const executedColumns = [
    {
      title: t('healing.action'),
      dataIndex: 'action',
      key: 'action',
      render: (v: string) => <Tag color="geekblue">{v}</Tag>,
    },
    {
      title: t('healing.runId'),
      key: 'run_id',
      render: (_: unknown, r: ExecutedFix) =>
        r.run_id ?? r.regression_run_id ?? '-',
    },
    {
      title: t('healing.status'),
      key: 'status',
      render: (_: unknown, r: ExecutedFix) => {
        if (r.verdict) {
          return <Tag color={verdictColor(r.verdict)}>{r.verdict}</Tag>;
        }
        return r.status ? <Tag>{r.status}</Tag> : '-';
      },
    },
    {
      title: t('healing.scoreDelta'),
      key: 'score_delta',
      render: (_: unknown, r: ExecutedFix) => {
        if (r.score_delta === null || r.score_delta === undefined) return '-';
        const v = Number(r.score_delta);
        const color = v > 0 ? 'green' : v < 0 ? 'red' : 'default';
        return <Tag color={color}>{v > 0 ? '+' : ''}{v.toFixed(4)}</Tag>;
      },
    },
  ];

  if (loadingRuns) {
    return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;
  }

  return (
    <div>
      <Card
        title={<><ToolOutlined /> {t('healing.title')}</>}
        style={{ marginBottom: 16 }}
      >
        <div style={{ marginBottom: 8, color: '#888' }}>{t('healing.desc')}</div>
        <Space wrap>
          <Text style={{ fontWeight: 500 }}>{t('healing.selectRun')}</Text>
          <Select
            showSearch
            value={selectedRunId}
            onChange={setSelectedRunId}
            style={{ width: 480 }}
            placeholder={t('healing.selectRunPlaceholder')}
            optionFilterProp="label"
            options={runs.map((r) => ({
              value: r.id,
              label: `[#${r.id}] ${r.name} (failed: ${r.failed_cases ?? 0}, score: ${(r.avg_score ?? 0).toFixed(2)}, ${r.created_at.slice(0, 10)})`,
            }))}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={run}
            loading={loading}
            disabled={!selectedRunId}
          >
            {t('healing.run')}
          </Button>
        </Space>
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          <ClockCircleOutlined /> {t('healing.timeoutHint')}
        </div>
      </Card>

      {!result && !loading && (
        <Empty description={t('healing.noData')} style={{ marginTop: 60 }} />
      )}

      {result && (
        <>
          <Card style={{ marginBottom: 16 }}>
            <Space wrap size="middle">
              <span>
                <Text type="secondary">{t('healing.judge')}: </Text>
                <Tag color={judgeColor(result.judge)}>{result.judge}</Tag>
              </span>
              <span>
                <Text type="secondary">{t('healing.iterations')}: </Text>
                <Text strong>{result.iterations}</Text>
              </span>
              <span>
                <Text type="secondary">{t('healing.elapsed')}: </Text>
                <Text strong>{(result.elapsed_ms / 1000).toFixed(1)}s</Text>
              </span>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={run}
                loading={loading}
              >
                {t('healing.rerun')}
              </Button>
            </Space>
          </Card>

          {/* L4 半闭环 verdict 横幅 */}
          <Card style={{ marginBottom: 16 }}>
            <Space wrap size="large" align="center">
              <Text style={{ fontWeight: 600 }}>{t('healing.verdict')}: </Text>
              <Tag
                color={verdictColor(result.regression_verdict)}
                style={{ fontSize: 14, padding: '2px 12px' }}
              >
                {result.regression_verdict}
              </Tag>
              <span>
                <Text type="secondary">{t('healing.scoreDelta')}: </Text>
                <Text strong>
                  {result.score_delta === null || result.score_delta === undefined
                    ? '-'
                    : `${result.score_delta > 0 ? '+' : ''}${Number(result.score_delta).toFixed(4)}`}
                </Text>
              </span>
              <span>
                <Text type="secondary">{t('healing.regressionRunId')}: </Text>
                <Text strong>
                  {result.regression_run_id ?? '-'}
                </Text>
              </span>
            </Space>
            {result.regression_verdict === 'pending' && (
              <Alert
                style={{ marginTop: 12 }}
                type="info"
                showIcon
                message={t('healing.pendingHint')}
              />
            )}
          </Card>

          {result.proposed_fixes.length > 0 && (
            <Card
              title={`${t('healing.proposedFixes')} (${result.proposed_fixes.length})`}
              style={{ marginBottom: 16 }}
            >
              <Table
                dataSource={result.proposed_fixes}
                columns={proposedColumns}
                rowKey={(r: ProposedFix, i?: number) => r.approval_id ?? `p-${i}`}
                size="small"
                pagination={false}
              />
            </Card>
          )}

          {result.executed_fixes.length > 0 && (
            <Card
              title={`${t('healing.executedFixes')} (${result.executed_fixes.length})`}
              style={{ marginBottom: 16 }}
            >
              <Table
                dataSource={result.executed_fixes}
                columns={executedColumns}
                rowKey={(r: ExecutedFix, i?: number) => `${r.action}-${i}`}
                size="small"
                pagination={false}
              />
            </Card>
          )}

          <Card title={t('healing.summary')} style={{ marginBottom: 16 }}>
            <div
              style={{
                background: '#fafafa',
                padding: 16,
                borderRadius: 8,
                border: '1px solid #f0f0f0',
                fontSize: 14,
                lineHeight: 1.8,
              }}
            >
              <ReactMarkdown>{result.healing_summary || ''}</ReactMarkdown>
            </div>
          </Card>

          {result.trace && result.trace.length > 0 && (
            <Card title={t('healing.trace')} style={{ marginBottom: 16 }}>
              <Timeline
                items={result.trace.map((step, i) => ({
                  color: step.action ? 'blue' : 'gray',
                  children: (
                    <div>
                      <Text strong>
                        {t('healing.iter')} {Number(step.iteration ?? i + 1)}
                      </Text>
                      {step.action ? (
                        <div>
                          <Tag color="geekblue">{String(step.action)}</Tag>
                          <Paragraph
                            style={{
                              margin: '4px 0',
                              fontSize: 12,
                              color: '#666',
                              whiteSpace: 'pre-wrap',
                            }}
                          >
                            {typeof step.observation === 'string'
                              ? step.observation.slice(0, 300)
                              : JSON.stringify(step.observation ?? step.thought_raw ?? '', null, 0).slice(0, 300)}
                          </Paragraph>
                        </div>
                      ) : (
                        <Paragraph
                          style={{ margin: '4px 0', fontSize: 12, color: '#999' }}
                        >
                          {String(step.thought_raw ?? step.thought ?? '').slice(0, 200)}
                        </Paragraph>
                      )}
                    </div>
                  ),
                }))}
              />
              <Collapse
                ghost
                items={[
                  {
                    key: 'raw',
                    label: t('healing.rawTrace'),
                    children: (
                      <pre style={{ fontSize: 11, overflow: 'auto', maxHeight: 300 }}>
                        {JSON.stringify(result.trace, null, 2)}
                      </pre>
                    ),
                  },
                ]}
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
};

export default Healing;
