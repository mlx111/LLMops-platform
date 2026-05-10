import React from 'react';
import { Tag, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  ThunderboltOutlined, LinkOutlined, SearchOutlined,
  ToolOutlined, RobotOutlined, ClusterOutlined, DeploymentUnitOutlined,
} from '@ant-design/icons';
import type { TraceStep } from '../types';

const stepTypeConfig: Record<string, { color: string; icon: React.ReactNode }> = {
  LLM: { color: '#722ed1', icon: <RobotOutlined /> },
  CHAIN: { color: '#1677ff', icon: <LinkOutlined /> },
  TOOL: { color: '#fa8c16', icon: <ToolOutlined /> },
  RETRIEVER: { color: '#52c41a', icon: <SearchOutlined /> },
  RERANKER: { color: '#eb2f96', icon: <ClusterOutlined /> },
  EMBEDDING: { color: '#13c2c2', icon: <DeploymentUnitOutlined /> },
  AGENT: { color: '#fa541c', icon: <ThunderboltOutlined /> },
};

interface Props {
  steps: TraceStep[];
  totalLatencyMs: number;
  expandedSteps: Set<number>;
  onToggleStep: (stepId: number) => void;
}

const TraceTree: React.FC<Props> = ({ steps, totalLatencyMs, expandedSteps, onToggleStep }) => {
  const { t } = useTranslation();

  if (steps.length === 0) {
    return <div style={{ color: '#999', padding: 16 }}>No steps recorded.</div>;
  }

  return (
    <div style={{ fontFamily: 'monospace', fontSize: 13 }}>
      {steps.map((step) => {
        const config = stepTypeConfig[step.step_type] || stepTypeConfig.CHAIN;
        const indent = step.parent_step_id ? 1 : 0;
        const barPct = totalLatencyMs > 0 ? (step.latency_ms / totalLatencyMs) * 100 : 0;
        const hasDetail = !!(step.input_json || step.output_json);
        const expanded = expandedSteps.has(step.id);

        return (
          <div key={step.id}>
            <div
              onClick={() => hasDetail && onToggleStep(step.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '4px 0',
                borderBottom: '1px solid #f0f0f0',
                cursor: hasDetail ? 'pointer' : 'default',
                background: expanded ? '#e6f4ff' : undefined,
                transition: 'background 0.2s',
              }}
            >
              {indent > 0 && <div style={{ width: indent * 24, flexShrink: 0 }} />}

              <Tooltip title={step.step_type}>
                <span style={{ color: config.color, marginRight: 8, width: 20, textAlign: 'center' }}>
                  {config.icon}
                </span>
              </Tooltip>

              <div style={{ width: 160, flexShrink: 0, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <Tooltip title={step.step_name}>
                  <span>{step.step_name}</span>
                </Tooltip>
              </div>

              <div style={{ flex: 1, margin: '0 12px' }}>
                <div style={{ background: '#f0f0f0', borderRadius: 4, height: 20, position: 'relative' }}>
                  <div style={{
                    width: `${Math.max(barPct, 0.5)}%`,
                    height: '100%',
                    background: step.error_message ? '#ff4d4f' : config.color,
                    borderRadius: 4,
                    opacity: 0.7,
                    transition: 'width 0.3s',
                  }} />
                  <span style={{ position: 'absolute', left: 8, top: 0, lineHeight: '20px', fontSize: 11, color: '#333' }}>
                    {step.latency_ms}ms
                  </span>
                </div>
              </div>

              <div style={{ width: 60, textAlign: 'right', color: '#999', fontSize: 12 }}>
                {step.tokens != null ? `${step.tokens} tok` : '-'}
              </div>

              <div style={{ width: 60, textAlign: 'right' }}>
                {step.error_message ? (
                  <Tooltip title={step.error_message}>
                    <Tag color="error" style={{ margin: 0 }}>{t('traceTree.error')}</Tag>
                  </Tooltip>
                ) : (
                  <Tag color="success" style={{ margin: 0 }}>{t('traceTree.ok')}</Tag>
                )}
              </div>

              <div style={{ width: 24, textAlign: 'center', color: hasDetail ? '#1677ff' : '#ccc', fontSize: 14 }}>
                {hasDetail ? (expanded ? '▴' : '▾') : ' '}
              </div>
            </div>

            {expanded && (
              <div style={{
                marginLeft: indent * 24 + 28,
                padding: '8px 12px',
                background: '#fafafa',
                borderLeft: '3px solid #1677ff',
                borderRadius: '0 4px 4px 0',
                marginBottom: 4,
                fontSize: 12,
              }}>
                {step.input_json && (
                  <div style={{ marginBottom: 8 }}>
                    <strong style={{ color: '#555' }}>Input:</strong>
                    <pre style={{ margin: '4px 0 0', padding: 6, background: '#fff', borderRadius: 3, border: '1px solid #eee', maxHeight: 120, overflow: 'auto' }}>
                      {JSON.stringify(step.input_json, null, 2)}
                    </pre>
                  </div>
                )}
                {step.output_json && (
                  <div>
                    <strong style={{ color: '#555' }}>Output:</strong>
                    <pre style={{ margin: '4px 0 0', padding: 6, background: '#fff', borderRadius: 3, border: '1px solid #eee', maxHeight: 120, overflow: 'auto' }}>
                      {JSON.stringify(step.output_json, null, 2)}
                    </pre>
                  </div>
                )}
                {!step.input_json && !step.output_json && (
                  <span style={{ color: '#999' }}>No detail available</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default TraceTree;
