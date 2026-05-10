import React, { useEffect, useState } from 'react';
import { Card, Table, Button, Modal, Form, Input, Select, Tag, Space, message, Popconfirm } from 'antd';
import { useTranslation } from 'react-i18next';
import { PlusOutlined, ImportOutlined } from '@ant-design/icons';
import { datasetApi } from '../api/client';
import type { Dataset, EvalCase } from '../types';

const caseTypeColors: Record<string, string> = { qa: 'blue', rag: 'green', tool_calling: 'orange', multi_turn: 'purple' };
const difficultyColors: Record<string, string> = { easy: 'success', medium: 'warning', hard: 'error' };

const DatasetManagement: React.FC = () => {
  const { t } = useTranslation();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetTotal, setDatasetTotal] = useState(0);
  const [dsPage, setDsPage] = useState(1);
  const [dsPageSize, setDsPageSize] = useState(10);
  const [selectedDs, setSelectedDs] = useState<Dataset | null>(null);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [caseTotal, setCaseTotal] = useState(0);
  const [casePage, setCasePage] = useState(1);
  const [casePageSize, setCasePageSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [dsModalOpen, setDsModalOpen] = useState(false);
  const [caseModalOpen, setCaseModalOpen] = useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [dsForm] = Form.useForm();
  const [caseForm] = Form.useForm();
  const [importForm] = Form.useForm();

  const loadDatasets = async (page = 1, pageSize = 10) => {
    setLoading(true);
    try {
      const res = await datasetApi.list({ skip: (page - 1) * pageSize, limit: pageSize });
      setDatasets(res.data.items);
      setDatasetTotal(res.data.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => {
      void loadDatasets();
    });
  }, []);

  const handleDsPageChange = (page: number, pageSize: number) => {
    setDsPage(page);
    setDsPageSize(pageSize);
    loadDatasets(page, pageSize);
  };

  const selectDataset = (ds: Dataset) => {
    setSelectedDs(ds);
    setCasePage(1);
    loadCases(ds.id, 1, casePageSize);
  };

  const loadCases = (datasetId: number, page = 1, pageSize = 10) => {
    datasetApi.listCases(datasetId, { skip: (page - 1) * pageSize, limit: pageSize })
      .then((res) => {
        setCases(res.data.items);
        setCaseTotal(res.data.total);
      });
  };

  const handleCasePageChange = (page: number, pageSize: number) => {
    setCasePage(page);
    setCasePageSize(pageSize);
    if (selectedDs) loadCases(selectedDs.id, page, pageSize);
  };

  const createDataset = async () => {
    const vals = await dsForm.validateFields();
    await datasetApi.create(vals);
    message.success(t('dataset.createSuccess'));
    setDsModalOpen(false);
    dsForm.resetFields();
    loadDatasets(dsPage, dsPageSize);
  };

  const createCase = async () => {
    if (!selectedDs) return;
    const vals = await caseForm.validateFields();
    await datasetApi.createCase(selectedDs.id, vals);
    message.success('Case added');
    setCaseModalOpen(false);
    caseForm.resetFields();
    loadCases(selectedDs.id, casePage, casePageSize);
  };

  const importJson = async () => {
    if (!selectedDs) return;
    const vals = await importForm.validateFields();
    try {
      const parsed = JSON.parse(vals.jsonData);
      const cases = Array.isArray(parsed) ? parsed : [parsed];
      await datasetApi.importCases(selectedDs.id, cases);
      message.success(`Imported ${cases.length} cases`);
      setImportModalOpen(false);
      importForm.resetFields();
      loadCases(selectedDs.id, casePage, casePageSize);
    } catch { message.error('Invalid JSON'); }
  };

  const dsColumns = [
    { title: t('dataset.name'), dataIndex: 'name', key: 'name' },
    { title: t('dataset.caseType'), dataIndex: 'case_type', key: 'case_type', render: (type: string) => <Tag color={caseTypeColors[type]}>{type}</Tag> },
    { title: t('dataset.caseCount'), dataIndex: 'case_count', key: 'case_count' },
    { title: t('common.save'), dataIndex: 'created_at', key: 'created_at', render: (v: string) => new Date(v).toLocaleDateString() },
    {
      title: t('dataset.actions'), key: 'action',
      render: (_: unknown, r: Dataset) => (
        <Space>
          <Button size="small" type="link" onClick={() => selectDataset(r)}>{t('dataset.addCase')}</Button>
          <Popconfirm title={t('dataset.deleteConfirm')} onConfirm={async () => { await datasetApi.delete(r.id); loadDatasets(dsPage, dsPageSize); }}>
            <Button size="small" type="link" danger>{t('common.delete')}</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const caseColumns = [
    { title: t('dataset.input'), dataIndex: 'input', key: 'input', ellipsis: true },
    { title: t('dataset.caseType'), dataIndex: 'case_type', key: 'case_type', render: (type: string) => <Tag color={caseTypeColors[type]}>{type}</Tag> },
    { title: t('dataset.difficulty'), dataIndex: 'difficulty', key: 'difficulty', render: (d: string) => d ? <Tag color={difficultyColors[d]}>{d}</Tag> : '-' },
    { title: t('dataset.tags'), dataIndex: 'tags', key: 'tags', render: (tagList: string[]) => tagList?.map((tag) => <Tag key={tag}>{tag}</Tag>) },
  ];

  return (
    <div>
      <Card
        title={t('dataset.title')}
        extra={<Button icon={<PlusOutlined />} onClick={() => setDsModalOpen(true)}>{t('dataset.create')}</Button>}
        style={{ marginBottom: 16 }}
      >
        <Table
          dataSource={datasets}
          columns={dsColumns}
          rowKey="id"
          size="middle"
          loading={loading}
          pagination={{
            current: dsPage,
            pageSize: dsPageSize,
            total: datasetTotal,
            showSizeChanger: true,
            onChange: handleDsPageChange,
          }}
        />
      </Card>

      {selectedDs && (
        <Card
          title={t('dataset.casesTitle', { name: selectedDs.name })}
          extra={
            <Space>
              <Button icon={<ImportOutlined />} onClick={() => setImportModalOpen(true)}>{t('dataset.importCases')}</Button>
              <Button icon={<PlusOutlined />} type="primary" onClick={() => setCaseModalOpen(true)}>{t('dataset.addCase')}</Button>
            </Space>
          }
        >
          <Table
            dataSource={cases}
            columns={caseColumns}
            rowKey="id"
            size="middle"
            pagination={{
              current: casePage,
              pageSize: casePageSize,
              total: caseTotal,
              showSizeChanger: true,
              onChange: handleCasePageChange,
            }}
          />
        </Card>
      )}

      <Modal title={t('dataset.create')} open={dsModalOpen} onOk={createDataset} onCancel={() => setDsModalOpen(false)}>
        <Form form={dsForm} layout="vertical">
          <Form.Item name="name" label={t('dataset.name')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label={t('dataset.description')}><Input.TextArea /></Form.Item>
          <Form.Item name="case_type" label={t('dataset.caseType')} initialValue="qa">
            <Select options={['qa', 'rag', 'tool_calling', 'multi_turn'].map((type) => ({ value: type, label: type }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={t('dataset.addCase')} open={caseModalOpen} onOk={createCase} onCancel={() => setCaseModalOpen(false)} width={600}>
        <Form form={caseForm} layout="vertical">
          <Form.Item name="case_type" label={t('dataset.caseType')} initialValue="qa">
            <Select options={['qa', 'rag', 'tool_calling', 'multi_turn'].map((type) => ({ value: type, label: type }))} />
          </Form.Item>
          <Form.Item name="input" label={t('dataset.input')} rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="reference_answer" label={t('dataset.referenceAnswer')}><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="tags" label={t('dataset.tags')}><Select mode="tags" /></Form.Item>
          <Form.Item name="difficulty" label={t('dataset.difficulty')}>
            <Select options={['easy', 'medium', 'hard'].map((d) => ({ value: d, label: d }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={t('dataset.importCases')} open={importModalOpen} onOk={importJson} onCancel={() => setImportModalOpen(false)} width={600}>
        <Form form={importForm} layout="vertical">
          <Form.Item name="jsonData" label="JSON Data" rules={[{ required: true }]}>
            <Input.TextArea rows={10} placeholder='[{"case_type":"qa","input":"...","reference_answer":"..."}]' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default DatasetManagement;
