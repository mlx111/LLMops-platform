import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  message,
} from 'antd';
import { ReloadOutlined, SettingOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { getApiUrl } from '../api/base';
import { configApi, datasetApi, runApi, versionApi } from '../api/client';
import type {
  APIKeyInfo,
  Dataset,
  EvalRun,
  ProviderInfo,
  ProviderModelInfo,
  ProviderModelList,
  Version,
} from '../types';

const statusColors: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
};

const MODEL_CACHE_TTL_MS = 60_000;

type CachedProviderModels = {
  expiresAt: number;
  payload: ProviderModelList;
};

const getRunModeWarnings = (run: EvalRun | null) => {
  if (!run?.config_json) return [];
  const warnings: string[] = [];
  if (run.config_json.target_mode === 'demo' || !run.config_json.target_url) {
    warnings.push('No target URL configured. This run uses simulated target output.');
  }
  if (run.config_json.evaluation_mode === 'demo') {
    warnings.push('No provider API key configured. This run uses demo evaluation instead of a live judge.');
  }
  return warnings;
};

const ExperimentConfig: React.FC = () => {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [apiKeys, setApiKeys] = useState<APIKeyInfo[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runPage, setRunPage] = useState(1);
  const [runTotal, setRunTotal] = useState(0);
  const runPageSize = 5;

  const [selectedProvider, setSelectedProvider] = useState('deepseek');
  const [selectedModel, setSelectedModel] = useState('deepseek-chat');
  const [selectedDataset, setSelectedDataset] = useState<number | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [concurrency, setConcurrency] = useState(5);
  const [runName, setRunName] = useState('');
  const [targetUrl, setTargetUrl] = useState('');
  const [targetType, setTargetType] = useState('rag');
  const [targetAuth, setTargetAuth] = useState('');
  const [targetTimeout, setTargetTimeout] = useState(30);
  const [availableModels, setAvailableModels] = useState<ProviderModelInfo[]>([]);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelWarning, setModelWarning] = useState<string | null>(null);
  const [modelSource, setModelSource] = useState<string>('fallback');
  const modelCacheRef = useRef<Record<string, CachedProviderModels>>({});

  const [pollingRun, setPollingRun] = useState<EvalRun | null>(null);
  const [pollProgress, setPollProgress] = useState({ completed: 0, passed: 0, failed: 0, total: 0 });

  const navigate = useNavigate();

  const resolveDefaultModel = (
    providerId: string,
    providerList: ProviderInfo[],
    keyList: APIKeyInfo[],
  ) => {
    const provider = providerList.find((item) => item.id === providerId) ?? null;
    const key = keyList.find((item) => item.provider === providerId) ?? null;
    return key?.default_model || provider?.default_model || '';
  };

  const handleProviderChange = (
    providerId: string,
    providerList: ProviderInfo[] = providers,
    keyList: APIKeyInfo[] = apiKeys,
  ) => {
    setSelectedProvider(providerId);
    setSelectedModel(resolveDefaultModel(providerId, providerList, keyList));
  };

  const loadProviderModels = useCallback(async (
    providerId: string,
    providerList: ProviderInfo[],
    keyList: APIKeyInfo[],
  ) => {
    const cached = modelCacheRef.current[providerId];
    if (cached && cached.expiresAt > Date.now()) {
      const fallbackModel = resolveDefaultModel(providerId, providerList, keyList);
      const models = cached.payload.models.length > 0
        ? cached.payload.models
        : [{ id: fallbackModel, label: fallbackModel, owned_by: providerId }];

      setAvailableModels(models);
      setModelWarning(cached.payload.warning);
      setModelSource(cached.payload.source);
      setSelectedModel((currentModel) => {
        if (models.some((model) => model.id === currentModel)) {
          return currentModel;
        }
        return models[0]?.id || fallbackModel;
      });
      return;
    }

    setModelLoading(true);
    try {
      const response = await configApi.providerModels(providerId);
      const payload: ProviderModelList = response.data;
      modelCacheRef.current[providerId] = {
        expiresAt: Date.now() + MODEL_CACHE_TTL_MS,
        payload,
      };
      const fallbackModel = resolveDefaultModel(providerId, providerList, keyList);
      const models = payload.models.length > 0
        ? payload.models
        : [{ id: fallbackModel, label: fallbackModel, owned_by: providerId }];

      setAvailableModels(models);
      setModelWarning(payload.warning);
      setModelSource(payload.source);
      setSelectedModel((currentModel) => {
        if (models.some((model) => model.id === currentModel)) {
          return currentModel;
        }
        return models[0]?.id || fallbackModel;
      });
    } catch {
      const fallbackModel = resolveDefaultModel(providerId, providerList, keyList);
      const payload: ProviderModelList = {
        provider: providerId,
        source: 'fallback',
        warning: 'Failed to load provider models; using the default configured model.',
        models: [{ id: fallbackModel, label: fallbackModel, owned_by: providerId }],
      };
      modelCacheRef.current[providerId] = {
        expiresAt: Date.now() + MODEL_CACHE_TTL_MS,
        payload,
      };
      setAvailableModels(payload.models);
      setModelWarning(payload.warning);
      setModelSource(payload.source);
      setSelectedModel(fallbackModel);
    } finally {
      setModelLoading(false);
    }
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [providerRes, keyRes, datasetRes, versionRes, runRes] = await Promise.all([
        configApi.providers(),
        configApi.apiKeys(),
        datasetApi.list(),
        versionApi.list(),
        runApi.list({ skip: 0, limit: 5 }),
      ]);

      setProviders(providerRes.data);
      setApiKeys(keyRes.data);
      setDatasets(datasetRes.data.items);
      setVersions(versionRes.data.items);
      setRuns(runRes.data.items);
      setRunTotal(runRes.data.total);
      await loadProviderModels(selectedProvider, providerRes.data, keyRes.data);
    } finally {
      setLoading(false);
    }
  }, [loadProviderModels, selectedProvider]);

  useEffect(() => {
    queueMicrotask(() => {
      void loadData();
    });
  }, [loadData]);

  const selectedProviderInfo = providers.find((provider) => provider.id === selectedProvider) ?? null;
  const selectedApiKey = apiKeys.find((key) => key.provider === selectedProvider) ?? null;

  const openSettings = () => {
    // Navigate to Settings page; the provider is already visible there
    navigate('/settings');
  };

  const startRun = async () => {
    if (!selectedDataset || !runName.trim()) {
      message.warning(t('experiment.messages.selectDataset'));
      return;
    }
    if (!selectedProviderInfo) {
      message.warning(t('experiment.messages.selectProvider'));
      return;
    }
    if (!selectedProviderInfo.configured) {
      message.warning(t('experiment.messages.configureProvider'));
      return;
    }
    if (!selectedModel.trim()) {
      message.warning(t('experiment.messages.enterModel'));
      return;
    }

    setRunning(true);
    try {
      const res = await runApi.create({
        name: runName.trim(),
        dataset_id: selectedDataset,
        provider: selectedProvider,
        model: selectedModel.trim(),
        model_version_id: selectedVersion ?? undefined,
        concurrency,
        target_url: targetUrl.trim() || undefined,
        target_type: targetType,
        target_headers: targetAuth.trim() ? { Authorization: `Bearer ${targetAuth.trim()}` } : undefined,
        target_timeout: targetTimeout,
      });
      message.success(t('experiment.runStarted'));
      setPollingRun(res.data);
      await loadData();
    } catch {
      // handled by global interceptor
    } finally {
      setRunning(false);
    }
  };

  // ── SSE progress stream ──
  useEffect(() => {
    if (!pollingRun) return;
    const eventSource = new EventSource(getApiUrl(`/runs/${pollingRun.id}/stream`));
    eventSource.onmessage = (event) => {
      const prog = JSON.parse(event.data);
      setPollProgress({
        completed: prog.completed,
        passed: prog.passed,
        failed: prog.failed,
        total: prog.total,
      });
      if (prog.status === 'completed' || prog.status === 'failed' || prog.status === 'error') {
        eventSource.close();
        setPollingRun(null);
        message.success(t('experiment.runCompleted', { passed: prog.passed, failed: prog.failed }));
        loadData();
      }
    };
    eventSource.onerror = () => {
      eventSource.close();
      setPollingRun(null);
    };
    return () => eventSource.close();
  }, [pollingRun, loadData]);

  if (loading) {
    return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;
  }

  const runColumns = [
    { title: t('runResults.name'), dataIndex: 'name', key: 'name' },
    {
      title: t('runResults.provider'),
      key: 'provider',
      render: (_: unknown, run: EvalRun) => (
        <Tag>{String(run.config_json?.provider || 'deepseek')}</Tag>
      ),
    },
    {
      title: t('runResults.model'),
      key: 'model',
      render: (_: unknown, run: EvalRun) => String(run.config_json?.model || '-'),
    },
    {
      title: t('runResults.status'),
      dataIndex: 'status',
      key: 'status',
      render: (status: string, run: EvalRun) => (
        <Space size={[4, 4]} wrap>
          <Tag color={statusColors[status]}>{status}</Tag>
          {(!run.config_json?.target_url || run.config_json?.target_mode === 'demo') && <Tag color="warning">demo target</Tag>}
          {run.config_json?.evaluation_mode === 'demo' && <Tag color="orange">demo eval</Tag>}
        </Space>
      ),
    },
    {
      title: t('runResults.pass'),
      key: 'pass',
      render: (_: unknown, run: EvalRun) => `${run.passed_cases}/${run.total_cases}`,
    },
    {
      title: t('runResults.score'),
      dataIndex: 'avg_score',
      key: 'score',
      render: (value: number | null) => value?.toFixed(2) ?? '-',
    },
    {
      title: t('runResults.created'),
      dataIndex: 'created_at',
      key: 'time',
      render: (value: string) => new Date(value).toLocaleString(),
    },
  ];

  return (
    <div>
      <Card title={t('experiment.providerSelection')} style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          {providers.map((provider) => {
            const isSelected = provider.id === selectedProvider;
            const providerKey = apiKeys.find((key) => key.provider === provider.id) ?? null;
            return (
              <Card
                key={provider.id}
                size="small"
                style={{
                  width: 220,
                  cursor: 'pointer',
                  border: isSelected ? '2px solid #1677ff' : '1px solid #d9d9d9',
                }}
                onClick={() => {
                  handleProviderChange(provider.id);
                  void loadProviderModels(provider.id, providers, apiKeys);
                }}
              >
                <Space direction="vertical" size={6} style={{ display: 'flex' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <strong>{provider.name}</strong>
                    <Tag color={provider.configured ? 'success' : 'default'}>
                      {provider.configured ? t('experiment.configured') : t('experiment.noKey')}
                    </Tag>
                  </div>
                  <div style={{ color: '#666', fontSize: 12 }}>{provider.default_model}</div>
                  <div style={{ minHeight: 22 }}>
                    {providerKey ? <code>{providerKey.api_key_masked}</code> : (
                      provider.requires_api_key
                        ? <span>{t('experiment.apiKeyRequired')}</span>
                        : <Tag color="success">{t('experiment.localModel')}</Tag>
                    )}
                  </div>
                  <Button
                    size="small"
                    icon={<SettingOutlined />}
                    onClick={(event) => {
                      event.stopPropagation();
                      openSettings();
                    }}
                  >
                    {t('experiment.manageKeys')}
                  </Button>
                </Space>
              </Card>
            );
          })}
        </div>

        {selectedProviderInfo && (
          <Alert
            type={selectedProviderInfo.configured ? 'success' : 'warning'}
            showIcon
            message={t('experiment.info.selected', { provider: selectedProviderInfo.name, model: selectedApiKey?.default_model || selectedProviderInfo.default_model })}
            description={
              selectedProviderInfo.configured
                ? t('experiment.info.apiKey', { key: selectedApiKey?.api_key_masked || '', source: 'config' })
                : t('experiment.messages.configureProvider')
            }
          />
        )}
      </Card>

      <Card title={t('experiment.startExperiment')} style={{ marginBottom: 16 }}>
        <Form layout="vertical">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
            <Form.Item label={t('experiment.runName')}>
              <Input
                placeholder={t('experiment.runNamePlaceholder')}
                value={runName}
                onChange={(event) => setRunName(event.target.value)}
              />
            </Form.Item>

            <Form.Item label={t('experiment.dataset')}>
              <Select
                value={selectedDataset}
                onChange={setSelectedDataset}
                placeholder={t('common.select')}
                options={datasets.map((dataset) => ({
                  value: dataset.id,
                  label: `${dataset.name} (${dataset.case_count} ${t('dataset.caseCount')})`,
                }))}
              />
            </Form.Item>

            <Form.Item label={t('experiment.provider')}>
              <Select
                value={selectedProvider}
                onChange={(value) => {
                  handleProviderChange(value);
                  void loadProviderModels(value, providers, apiKeys);
                }}
                options={providers.map((provider) => ({
                  value: provider.id,
                  label: provider.name,
                }))}
              />
            </Form.Item>

            <Form.Item label={t('experiment.model')}>
              <Space.Compact style={{ width: '100%' }}>
                <Select
                  value={selectedModel || undefined}
                  loading={modelLoading}
                  showSearch
                  optionFilterProp="label"
                  onChange={setSelectedModel}
                  options={availableModels.map((model) => ({
                    value: model.id,
                    label: model.label,
                  }))}
                  notFoundContent={modelLoading ? t('experiment.loadingModels') : t('experiment.noModels')}
                  placeholder={t('experiment.selectModel')}
                  style={{ width: '100%' }}
                />
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => loadProviderModels(selectedProvider, providers, apiKeys)}
                  loading={modelLoading}
                >
                  {t('experiment.fetchModels')}
                </Button>
              </Space.Compact>
            </Form.Item>

            <Form.Item label={t('experiment.savedModelVersion')}>
              <Select
                value={selectedVersion}
                onChange={setSelectedVersion}
                placeholder={t('common.optional')}
                allowClear
                options={versions
                  .filter((version) => version.version_type === 'model')
                  .map((version) => ({
                    value: version.id,
                    label: version.name,
                  }))}
              />
            </Form.Item>

            <Form.Item label={t('experiment.concurrency')}>
              <Select
                value={concurrency}
                onChange={setConcurrency}
                options={[1, 3, 5, 10].map((value) => ({ value, label: value }))}
              />
            </Form.Item>
          </div>

          <Card
            title={t('experiment.targetSystem')}
            size="small"
            style={{ marginTop: 16, marginBottom: 16, background: '#fafafa' }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
              <Form.Item label={t('experiment.targetUrl')} style={{ marginBottom: 0 }}>
                <Input
                  placeholder={t('experiment.targetUrlPlaceholder')}
                  value={targetUrl}
                  onChange={(event) => setTargetUrl(event.target.value)}
                  allowClear
                />
              </Form.Item>
              <Form.Item label={t('experiment.systemType')} style={{ marginBottom: 0 }}>
                <Select
                  value={targetType}
                  onChange={setTargetType}
                  options={[
                    { value: 'rag', label: t('experiment.rag') },
                    { value: 'agent', label: t('experiment.agent') },
                    { value: 'chat', label: t('experiment.chat') },
                  ]}
                />
              </Form.Item>
              <Form.Item label={t('experiment.authToken')} style={{ marginBottom: 0 }}>
                <Input.Password
                  placeholder="Bearer token for target system"
                  value={targetAuth}
                  onChange={(event) => setTargetAuth(event.target.value)}
                />
              </Form.Item>
              <Form.Item label={t('experiment.timeout')} style={{ marginBottom: 0 }}>
                <Select
                  value={targetTimeout}
                  onChange={setTargetTimeout}
                  options={[10, 30, 60, 120].map((value) => ({ value, label: `${value}s` }))}
                />
              </Form.Item>
            </div>
          </Card>

          <Space direction="vertical" size={12} style={{ display: 'flex' }}>
            <Alert
              type="info"
              showIcon
              message={t('experiment.info.selected', { provider: selectedProviderInfo?.name || '-', model: selectedModel || '-' })}
              description={t('experiment.info.apiKey', { key: selectedApiKey?.api_key_masked || t('experiment.noKey'), source: modelSource })}
            />
            {!targetUrl.trim() && (
              <Alert
                type="warning"
                showIcon
                message="No target URL configured. This run will use simulated target output."
              />
            )}
            {!selectedProviderInfo?.configured && (
              <Alert
                type="warning"
                showIcon
                message="No provider API key configured. This run will use demo evaluation instead of a live judge."
              />
            )}
            {modelWarning && (
              <Alert
                type={modelSource === 'live' ? 'info' : 'warning'}
                showIcon
                message={modelWarning}
              />
            )}
            <Space>
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                onClick={startRun}
                loading={running}
                disabled={!selectedProviderInfo?.configured}
              >
                {t('experiment.startRun')}
              </Button>
              {selectedProviderInfo && (
                <Button icon={<SettingOutlined />} onClick={openSettings}>
                  {t('experiment.configureApiKey')}
                </Button>
              )}
            </Space>
          </Space>
        </Form>
      </Card>

      {pollingRun && (
        <Card
          size="small"
          title={<span><Spin size="small" /> {t('experiment.runInProgress', { name: pollingRun.name })}</span>}
          style={{ marginBottom: 16 }}
        >
          <Space direction="vertical" size={8} style={{ display: 'flex', marginBottom: 12 }}>
            {getRunModeWarnings(pollingRun).map((warning) => (
              <Alert key={warning} type="warning" showIcon message={warning} />
            ))}
          </Space>
          <Progress
            percent={pollProgress.total > 0
              ? Math.round((pollProgress.completed / pollProgress.total) * 100)
              : 0}
            format={() =>
              `${pollProgress.completed}/${pollProgress.total} ${t('dataset.caseCount')}`
            }
            status="active"
          />
          <div style={{ display: 'flex', gap: 24, marginTop: 8, fontSize: 13 }}>
            <span style={{ color: '#52c41a' }}>{t('experiment.passed')}: {pollProgress.passed}</span>
            <span style={{ color: '#ff4d4f' }}>{t('experiment.failed')}: {pollProgress.failed}</span>
            <span style={{ color: '#999' }}>
              {t('experiment.remaining')}: {pollProgress.total - pollProgress.completed}
            </span>
          </div>
        </Card>
      )}

      <Card title={t('experiment.recentRuns')}>
        <Table
          dataSource={runs}
          columns={runColumns}
          rowKey="id"
          size="middle"
          pagination={{
            current: runPage,
            pageSize: runPageSize,
            total: runTotal,
            showSizeChanger: false,
            onChange: (page) => {
              setRunPage(page);
              runApi.list({ skip: (page - 1) * runPageSize, limit: runPageSize }).then((res) => {
                setRuns(res.data.items);
                setRunTotal(res.data.total);
              });
            },
          }}
        />
      </Card>

    </div>
  );
};

export default ExperimentConfig;
