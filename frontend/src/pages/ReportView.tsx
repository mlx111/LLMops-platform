import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Select, Spin, Table, message } from 'antd';
import { FileTextOutlined, BarChartOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { reportApi, runApi } from '../api/client';
import FailurePieChart from '../components/FailurePieChart';
import type { EvalRun, Report } from '../types';

const ReportView: React.FC = () => {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [reportTotal, setReportTotal] = useState(0);
  const [reportPage, setReportPage] = useState(1);
  const [reportPageSize, setReportPageSize] = useState(10);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = (page = 1, pageSize = 10) => {
    Promise.all([
      runApi.list({ limit: 100 }),
      reportApi.list({ skip: (page - 1) * pageSize, limit: pageSize }),
    ]).then(([runRes, reportRes]) => {
      const completed = runRes.data.items.filter(
        (r: EvalRun) => r.status === 'completed'
      );
      setRuns(completed);
      setReports(reportRes.data.items);
      setReportTotal(reportRes.data.total);
    }).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleReportPageChange = (page: number, pageSize: number) => {
    setReportPage(page);
    setReportPageSize(pageSize);
    load(page, pageSize);
  };

  const generate = async () => {
    if (!selectedRunId) return;
    setGenerating(true);
    try {
      const res = await reportApi.generate(selectedRunId);
      message.success(t('reports.generating'));
      setSelectedReport(res.data);
      load();
    } catch {
      // handled by global interceptor
    } finally {
      setGenerating(false);
    }
  };

  const viewReport = (report: Report) => {
    setSelectedReport(report);
    setSelectedRunId(report.run_id);
  };

  const reportColumns = [
    { title: t('reports.runId'), dataIndex: 'run_id', key: 'run_id', width: 80 },
    { title: t('reports.created'), dataIndex: 'created_at', key: 'created_at', render: (v: string) => new Date(v).toLocaleString() },
    {
      title: t('reports.passRate'), key: 'pass_rate',
      render: (_: unknown, r: Report) => {
        const s = r.summary_json as Record<string, unknown> | null;
        return s?.pass_rate ? `${s.pass_rate}%` : '-';
      },
    },
    {
      title: t('reports.actions'), key: 'action',
      render: (_: unknown, r: Report) => (
        <Button size="small" type="link" onClick={() => viewReport(r)}>{t('reports.view')}</Button>
      ),
    },
  ];

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

  const failureDist = selectedReport?.summary_json
    ? (selectedReport.summary_json as Record<string, unknown>)?.failure_distribution as Record<string, number>
    : null;

  return (
    <div>
      <Card title={<><BarChartOutlined /> {t('reports.title')}</>} style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
          <span style={{ fontWeight: 500 }}>{t('reports.generateFor')}</span>
          <Select
            showSearch
            value={selectedRunId}
            onChange={setSelectedRunId}
            style={{ width: 400 }}
            placeholder={t('reports.selectRun')}
            optionFilterProp="label"
            options={runs.map((r) => ({
              value: r.id,
              label: `[#${r.id}] ${r.name} (score: ${(r.avg_score ?? 0).toFixed(2)}, ${r.created_at.slice(0, 10)})`,
            }))}
          />
          <Button
            type="primary"
            icon={<FileTextOutlined />}
            onClick={generate}
            loading={generating}
            disabled={!selectedRunId}
          >
            {t('reports.generateReport')}
          </Button>
        </div>

        <Table
          dataSource={reports}
          columns={reportColumns}
          rowKey="id"
          size="middle"
          pagination={{
            current: reportPage,
            pageSize: reportPageSize,
            total: reportTotal,
            showSizeChanger: true,
            onChange: handleReportPageChange,
          }}
        />
      </Card>

      {selectedReport && (
        <>
          {failureDist && (
            <Card title={t('failurePieChart.title')} style={{ marginBottom: 16 }}>
              <FailurePieChart distribution={failureDist} />
            </Card>
          )}

          <Card title={t('reports.reportForRun', { name: `#${selectedReport.run_id}` })}>
            <div style={{
              background: '#fff', padding: 24, borderRadius: 8,
              border: '1px solid #f0f0f0', maxHeight: 600, overflow: 'auto',
              fontSize: 14, lineHeight: 1.8,
            }}>
              <ReactMarkdown>{selectedReport.report_markdown}</ReactMarkdown>
            </div>
          </Card>
        </>
      )}
    </div>
  );
};

export default ReportView;
