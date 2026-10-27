import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  InputNumber,
  Tag,
  Typography,
  Space,
  Empty,
  Timeline,
  Collapse,
  message,
} from 'antd';
import {
  BulbOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { insightApi } from '../api/client';
import FailurePieChart from '../components/FailurePieChart';
import type { InsightResult } from '../types';

const { Text, Paragraph } = Typography;

// judge → Tag 颜色映射（绿=成功，橙=兜底，红=超时）
const judgeColor = (judge: string): string => {
  if (judge === 'agentic_insight') return 'green';
  if (judge.includes('fallback')) return 'orange';
  if (judge.includes('timeout')) return 'red';
  return 'blue';
};

const Insights: React.FC = () => {
  const { t } = useTranslation();
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<InsightResult | null>(null);

  const generate = async () => {
    setLoading(true);
    try {
      const res = await insightApi.generate(days);
      setResult(res.data);
      message.success(t('insights.generated'));
    } catch {
      // 全局拦截器已提示
    } finally {
      setLoading(false);
    }
  };

  const failureDist = result?.failure_distribution ?? {};

  return (
    <div>
      <Card
        title={<><BulbOutlined /> {t('insights.title')}</>}
        style={{ marginBottom: 16 }}
      >
        <div style={{ marginBottom: 8, color: '#888' }}>
          {t('insights.desc')}
        </div>
        <Space wrap>
          <Text style={{ fontWeight: 500 }}>{t('insights.days')}</Text>
          <InputNumber
            min={1}
            max={90}
            value={days}
            onChange={(v) => setDays(Number(v) || 7)}
            style={{ width: 110 }}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={generate}
            loading={loading}
          >
            {t('insights.generate')}
          </Button>
        </Space>
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          <ClockCircleOutlined /> {t('insights.timeoutHint')}
        </div>
      </Card>

      {!result && !loading && (
        <Empty description={t('insights.noData')} style={{ marginTop: 60 }} />
      )}

      {result && (
        <>
          <Card style={{ marginBottom: 16 }}>
            <Space wrap size="middle">
              <span>
                <Text type="secondary">{t('insights.judge')}: </Text>
                <Tag color={judgeColor(result.judge)}>{result.judge}</Tag>
              </span>
              <span>
                <Text type="secondary">{t('insights.iterations')}: </Text>
                <Text strong>{result.iterations}</Text>
              </span>
              <span>
                <Text type="secondary">{t('insights.elapsed')}: </Text>
                <Text strong>{(result.elapsed_ms / 1000).toFixed(1)}s</Text>
              </span>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={generate}
                loading={loading}
              >
                {t('insights.regenerate')}
              </Button>
            </Space>
          </Card>

          {Object.keys(failureDist).length > 0 && (
            <Card
              title={t('insights.failureDist')}
              style={{ marginBottom: 16 }}
            >
              <FailurePieChart distribution={failureDist} />
            </Card>
          )}

          {result.intent_clusters && result.intent_clusters.length > 0 && (
            <Card
              title={t('insights.intentClusters')}
              style={{ marginBottom: 16 }}
            >
              <Space wrap>
                {result.intent_clusters.map((c, i) => (
                  <Tag key={i} color="blue">{c}</Tag>
                ))}
              </Space>
            </Card>
          )}

          <Card title={t('insights.summary')} style={{ marginBottom: 16 }}>
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
              <ReactMarkdown>{result.insight_summary || ''}</ReactMarkdown>
            </div>
          </Card>

          {result.trace && result.trace.length > 0 && (
            <Card title={t('insights.trace')} style={{ marginBottom: 16 }}>
              <Timeline
                items={result.trace.map((step, i) => ({
                  color: step.action ? 'blue' : 'gray',
                  children: (
                    <div>
                      <Text strong>
                        {t('insights.iter')} {Number(step.iteration ?? i + 1)}
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
                    label: t('insights.rawTrace'),
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

export default Insights;
