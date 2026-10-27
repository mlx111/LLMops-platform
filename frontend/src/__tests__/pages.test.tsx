/** Smoke tests for Agentic Ops pages (Insights + Healing) */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Insights from '../pages/Insights';
import Healing from '../pages/Healing';
import type { InsightResult, HealingResult, EvalRun } from '../types';

// 桩掉 api/client，避免真实网络请求
vi.mock('../api/client', () => ({
  insightApi: {
    generate: vi.fn(),
  },
  healingApi: {
    run: vi.fn(),
  },
  runApi: {
    list: vi.fn(),
  },
}));

import { insightApi, healingApi, runApi } from '../api/client';

const renderPage = (ui: React.ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

// 测试环境 i18n fallbackLng=zh，但 LanguageDetector 会命中 navigator → 实际渲染英文。
// 故断言一律用英文文案，与 components.test.tsx 风格一致。
const makeRun = (over: Partial<EvalRun> = {}): EvalRun => ({
  id: 35,
  name: 'e2e-multiturn-target',
  dataset_id: 1,
  prompt_version_id: null,
  model_version_id: null,
  retriever_version_id: null,
  agent_version_id: null,
  status: 'completed',
  total_cases: 5,
  passed_cases: 0,
  failed_cases: 5,
  avg_score: 0,
  avg_latency_ms: 100,
  avg_tokens: 10,
  config_json: null,
  created_at: '2026-09-24T00:00:00',
  finished_at: null,
  ...over,
});

// ── Insights 页 ──

describe('Insights page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders title and generate button before triggering', () => {
    renderPage(<Insights />);
    expect(screen.getByText(/Agentic Insights/)).toBeInTheDocument();
    expect(screen.getByText('Generate Insights')).toBeInTheDocument();
  });

  it('renders empty state hint when no result', () => {
    renderPage(<Insights />);
    expect(screen.getByText(/launch the L2 Insight Agent/)).toBeInTheDocument();
  });

  it('renders insight result after generate', async () => {
    const sample: InsightResult = {
      judge: 'agentic_insight',
      iterations: 4,
      elapsed_ms: 17700,
      failure_distribution: { timeout: 4, quality_issue: 1 },
      top_failure_categories: [['timeout', 4]],
      intent_clusters: ['multiturn', 'planning'],
      insight_summary: 'failures concentrated in timeout.',
      trace: [{ iteration: 1, action: 'list_recent_runs', observation: '{}' }],
    };
    (insightApi.generate as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: sample,
    });

    renderPage(<Insights />);
    fireEvent.click(screen.getByText('Generate Insights'));

    await waitFor(() =>
      expect(screen.getByText('agentic_insight')).toBeInTheDocument()
    );
    // iterations（强文本 4）
    expect(screen.getAllByText('4').length).toBeGreaterThan(0);
    // 意图簇 tag
    expect(screen.getByText('multiturn')).toBeInTheDocument();
    // 总结（markdown 渲染）
    expect(screen.getByText(/concentrated in timeout/)).toBeInTheDocument();
  });

  it('renders fallback judge tag when agent unavailable', async () => {
    const sample: InsightResult = {
      judge: 'agentic_insight_fallback',
      iterations: 0,
      elapsed_ms: 100,
      failure_distribution: null,
      top_failure_categories: null,
      intent_clusters: null,
      insight_summary: 'agent unavailable',
      trace: [],
    };
    (insightApi.generate as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: sample,
    });

    renderPage(<Insights />);
    fireEvent.click(screen.getByText('Generate Insights'));

    await waitFor(() =>
      expect(screen.getByText('agentic_insight_fallback')).toBeInTheDocument()
    );
  });
});

// ── Healing 页 ──

describe('Healing page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders title and run button after runs load', async () => {
    (runApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { items: [], total: 0 },
    });
    renderPage(<Healing />);
    expect(await screen.findByText(/Agentic Self-Healing/)).toBeInTheDocument();
    expect(screen.getByText('Run Self-Heal')).toBeInTheDocument();
  });

  it('renders empty state hint when no result', async () => {
    (runApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { items: [], total: 0 },
    });
    renderPage(<Healing />);
    expect(await screen.findByText(/Select a run/)).toBeInTheDocument();
  });

  it('renders healing result with verdict tag after run', async () => {
    (runApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { items: [makeRun()], total: 1 },
    });
    const sample: HealingResult = {
      judge: 'agentic_self_heal',
      iterations: 5,
      elapsed_ms: 28657,
      proposed_fixes: [
        {
          approval_id: '87f9d0b0f885',
          tool_name: 'update_config',
          question: 'validate backend reachability',
          risk_level: 'high',
          status: 'pending',
        },
      ],
      executed_fixes: [
        { action: 'trigger_regression_run', run_id: 37, status: 'triggered' },
        { action: 'compare_regression', regression_run_id: 37, verdict: 'pending', score_delta: null },
      ],
      regression_run_id: 37,
      regression_verdict: 'pending',
      score_delta: null,
      healing_summary: '5 timeout failures, 1 fix proposed.',
      trace: [{ iteration: 1, action: 'list_run_failures', observation: '{}' }],
    };
    (healingApi.run as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: sample,
    });

    renderPage(<Healing />);

    // 打开 Select 下拉
    const combobox = await screen.findByRole('combobox');
    fireEvent.mouseDown(combobox);
    // 选项出现在 portal 里，点选 run
    const option = await screen.findByText(/e2e-multiturn-target/);
    fireEvent.click(option);
    // 此时 Run Self-Heal 按钮应已启用，点击触发 agent
    await waitFor(() => {
      const btn = screen.getByText('Run Self-Heal').closest('button');
      expect(btn).not.toBeDisabled();
    });
    fireEvent.click(screen.getByText('Run Self-Heal'));

    // judge tag
    await waitFor(() =>
      expect(screen.getByText('agentic_self_heal')).toBeInTheDocument()
    );
    // verdict tag (pending) — appears in verdict card + executed_fixes row
    expect(screen.getAllByText('pending').length).toBeGreaterThan(0);
    // 回归 run id 文本（verdict 卡片 + executed_fixes 两行均含 37）
    expect(screen.getAllByText('37').length).toBeGreaterThan(0);
    // pending hint Alert（含 verdict=pending 字样，语言无关）
    expect(screen.getByText(/verdict=pending/)).toBeInTheDocument();
    // proposed fix 表格中 approval_id
    expect(screen.getByText('87f9d0b0f885')).toBeInTheDocument();
  });
});
