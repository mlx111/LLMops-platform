import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Table, Button, Modal, Form, Input, Tag, Space, message, Popconfirm, Select, Descriptions, Badge } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { configApi, dashboardApi } from '../api/client';
import type { ProviderInfo, APIKeyInfo } from '../types';

interface HealthStatus {
  status: string;
  database: string;
  redis: string;
  celery_mode: string;
  api_version: string;
}

const Settings: React.FC = () => {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [apiKeys, setApiKeys] = useState<APIKeyInfo[]>([]);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<ProviderInfo | null>(null);
  const [form] = Form.useForm();
  const selectedProviderId = Form.useWatch('provider', form);

  const selectedManifest = useMemo(() => {
    if (editProvider) return editProvider;
    const p = providers.find((pr) => pr.id === selectedProviderId);
    return p ?? null;
  }, [editProvider, selectedProviderId, providers]);

  const requiresApiKey = selectedManifest?.requires_api_key ?? true;
  const requiresBaseUrl = selectedManifest?.requires_base_url ?? false;

  const apiKeyRules = useMemo(
    () => requiresApiKey ? [{ required: true, message: t('settings.apiKeyRequired') }] : [],
    [requiresApiKey, t]
  );

  const load = () => {
    Promise.all([
      configApi.providers(),
      configApi.apiKeys(),
      dashboardApi.health(),
    ]).then(([pRes, kRes, hRes]) => {
      setProviders(pRes.data);
      setApiKeys(kRes.data);
      setHealth(hRes.data);
    });
  };

  useEffect(() => { load(); }, []);

  const openModal = (provider?: ProviderInfo) => {
    form.resetFields();
    if (provider) {
      setEditProvider(provider);
      form.setFieldsValue({
        provider: provider.id,
        base_url: provider.base_url || '',
        api_key: '',
      });
    } else {
      setEditProvider(null);
    }
    setModalOpen(true);
  };

  const saveKey = async () => {
    const vals = await form.validateFields();
    await configApi.setApiKey({
      provider: vals.provider,
      api_key: vals.api_key || '',
      default_model: selectedManifest?.default_model || '',
      base_url: vals.base_url || undefined,
    });
    message.success(t('settings.saveSuccess'));
    setModalOpen(false);
    load();
  };

  const deleteKey = async (provider: string) => {
    await configApi.deleteApiKey(provider);
    message.success(t('settings.deleteSuccess'));
    load();
  };

  const columns = [
    {
      title: t('settings.provider'), dataIndex: 'provider', key: 'provider',
      render: (p: string) => <Tag color="blue">{p}</Tag>,
    },
    {
      title: t('settings.apiKey'), dataIndex: 'api_key_masked', key: 'api_key',
      render: (v: string) => v ? <code>{v}</code> : <Tag>{t('settings.notSet')}</Tag>,
    },
    { title: t('settings.defaultModel'), dataIndex: 'default_model', key: 'model' },
    { title: t('settings.baseUrl'), dataIndex: 'base_url', key: 'url', render: (v: string | null) => v || '-' },
    {
      title: t('settings.action'), key: 'action',
      render: (_: unknown, r: APIKeyInfo) => (
        <Space>
          <Button size="small" type="link" onClick={() => {
            const p = providers.find((pr) => pr.id === r.provider);
            if (p) openModal(p);
          }}>{t('settings.updateKey')}</Button>
          <Popconfirm title={t('settings.deleteConfirm')} onConfirm={() => deleteKey(r.provider)}>
            <Button size="small" type="link" danger>{t('settings.deleteKey')}</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title={t('settings.apiKeyManagement')}
        extra={<Button icon={<PlusOutlined />} onClick={() => openModal()}>{t('settings.addKey')}</Button>}
      >
        <Table dataSource={apiKeys} columns={columns} rowKey="id" size="middle" />

        <Card title={t('settings.supportedProviders')} size="small" style={{ marginTop: 16 }}>
          <Space wrap>
            {providers.map((p) => (
              <Tag
                key={p.id}
                color={p.configured ? 'success' : 'default'}
                style={{ cursor: 'pointer', padding: '4px 8px' }}
                onClick={() => openModal(p)}
              >
                {p.name} {p.configured ? '✓' : '+'}
              </Tag>
            ))}
          </Space>
        </Card>
      </Card>

      {health && (
        <Card title={t('settings.systemHealth')} size="small" style={{ marginTop: 16 }}>
          <Descriptions size="small" column={2}>
            <Descriptions.Item label={t('settings.apiStatus')}>
              <Badge status={health.status === 'ok' ? 'success' : 'error'} text={health.status} />
            </Descriptions.Item>
            <Descriptions.Item label={t('settings.version')}>{health.api_version}</Descriptions.Item>
            <Descriptions.Item label={t('settings.database')}>
              <Badge status={health.database === 'connected' ? 'success' : 'error'} text={health.database} />
            </Descriptions.Item>
            <Descriptions.Item label={t('settings.redis')}>
              <Badge
                status={health.redis === 'available' ? 'success' : 'warning'}
                text={health.redis}
              />
            </Descriptions.Item>
            <Descriptions.Item label={t('settings.celeryMode')} span={2}>
              <Tag color={health.celery_mode === 'celery_ready' ? 'success' : 'warning'}>
                {health.celery_mode === 'celery_ready' ? t('settings.celeryReady') : t('settings.celeryFallback')}
              </Tag>
              {health.redis !== 'available' && (
                <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>
                  {t('settings.redisHint')}
                </span>
              )}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Modal
        title={editProvider ? t('settings.setApiKeyFor', { name: editProvider.name }) : t('settings.setApiKey')}
        open={modalOpen}
        onOk={saveKey}
        onCancel={() => { form.resetFields(); setModalOpen(false); }}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="provider" label={t('settings.provider')} rules={[{ required: true }]}>
            <Select disabled={!!editProvider}>
              {providers.map((p) => (
                <Select.Option key={p.id} value={p.id}>{p.name}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="api_key" label={t('settings.apiKey')} rules={apiKeyRules}>
            <Input.Password placeholder={requiresApiKey ? 'sk-...' : t('settings.notRequired')} />
          </Form.Item>
          {requiresBaseUrl && (
            <Form.Item name="base_url" label={t('settings.baseUrl')}>
              <Input placeholder="https://api.deepseek.com" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
};

export default Settings;
