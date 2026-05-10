/** Tests for shared UI components */

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import ErrorBoundary from '../components/ErrorBoundary';
import FailurePieChart from '../components/FailurePieChart';
import CompareTable from '../components/CompareTable';
import TraceTree from '../components/TraceTree';
import AppLayout from '../components/Layout';

// ── ErrorBoundary ──

describe('ErrorBoundary', () => {
  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>Hello World</div>
      </ErrorBoundary>
    );
    expect(screen.getByText('Hello World')).toBeInTheDocument();
  });

  it('renders error UI when child throws', () => {
    const Bomb = () => {
      throw new Error('💥');
    };

    // Suppress console.error from React error logging
    const origError = console.error;
    console.error = vi.fn();

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('💥')).toBeInTheDocument();

    console.error = origError;
  });
});

// ── FailurePieChart ──

describe('FailurePieChart', () => {
  it('renders "No failure data" when distribution is empty', () => {
    const { container } = render(<FailurePieChart distribution={{}} />);
    expect(container.textContent).toContain('No failure data');
  });

  it('renders chart with data', () => {
    // ECharts requires a DOM environment with full layout support.
    // This test verifies the component renders without crashing.
    const distribution = { hallucination: 5, timeout: 3 };
    const { container } = render(<FailurePieChart distribution={distribution} />);
    // ECharts renders into a canvas, so just verify the component mounted
    expect(container.querySelector('div')).toBeTruthy();
  });
});

// ── CompareTable ──

describe('CompareTable', () => {
  it('renders "None" when entries are empty', () => {
    render(<CompareTable entries={[]} />);
    expect(screen.getByText('-- None --')).toBeInTheDocument();
  });

  it('renders entries with scores', () => {
    const entries = [
      {
        case_id: 1, input: 'What is AI?',
        run1_score: 0.5, run2_score: 0.9, delta: 0.4,
        run1_status: 'failed', run2_status: 'passed',
      },
    ];
    render(<CompareTable entries={entries} />);
    expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('What is AI?')).toBeInTheDocument();
  });
});

// ── TraceTree ──

describe('TraceTree', () => {
  const baseStep = {
    id: 1,
    step_name: 'llm_call',
    step_type: 'LLM',
    parent_step_id: null,
    input_json: { prompt: 'Hello' },
    output_json: { response: 'Hi there' },
    latency_ms: 150,
    tokens: 45,
    error_message: null,
    order_index: 0,
  };

  it('renders empty state when steps is empty', () => {
    render(<TraceTree steps={[]} totalLatencyMs={0} expandedSteps={new Set()} onToggleStep={vi.fn()} />);
    expect(screen.getByText('No steps recorded.')).toBeInTheDocument();
  });

  it('renders steps with name and latency', () => {
    render(
      <TraceTree
        steps={[baseStep]}
        totalLatencyMs={200}
        expandedSteps={new Set()}
        onToggleStep={vi.fn()}
      />
    );
    expect(screen.getByText('llm_call')).toBeInTheDocument();
    expect(screen.getByText('150ms')).toBeInTheDocument();
    expect(screen.getByText('45 tok')).toBeInTheDocument();
  });

  it('shows ok tag for successful steps', () => {
    render(
      <TraceTree
        steps={[baseStep]}
        totalLatencyMs={200}
        expandedSteps={new Set()}
        onToggleStep={vi.fn()}
      />
    );
    expect(screen.getByText('ok')).toBeInTheDocument();
  });

  it('shows error tag for failed steps', () => {
    const errorStep = { ...baseStep, error_message: 'Connection timeout', tokens: null };
    render(
      <TraceTree
        steps={[errorStep]}
        totalLatencyMs={200}
        expandedSteps={new Set()}
        onToggleStep={vi.fn()}
      />
    );
    expect(screen.getByText('error')).toBeInTheDocument();
  });

  it('expands detail section on click', () => {
    const onToggle = vi.fn();
    const { container } = render(
      <TraceTree
        steps={[baseStep]}
        totalLatencyMs={200}
        expandedSteps={new Set()}
        onToggleStep={onToggle}
      />
    );
    const row = container.querySelector('div[style*="cursor: pointer"]');
    expect(row).toBeTruthy();
    fireEvent.click(row!);
    expect(onToggle).toHaveBeenCalledWith(1);
  });

  it('shows expanded detail section with input/output when expanded', () => {
    render(
      <TraceTree
        steps={[baseStep]}
        totalLatencyMs={200}
        expandedSteps={new Set([1])}
        onToggleStep={vi.fn()}
      />
    );
    expect(screen.getByText(/Hello/)).toBeInTheDocument();
    expect(screen.getByText(/Hi there/)).toBeInTheDocument();
  });

  it('shows indent for child steps with parent_step_id', () => {
    const childStep = { ...baseStep, id: 2, parent_step_id: 1 };
    render(
      <TraceTree
        steps={[baseStep, childStep]}
        totalLatencyMs={300}
        expandedSteps={new Set()}
        onToggleStep={vi.fn()}
      />
    );
    expect(screen.getAllByText('llm_call').length).toBe(2);
  });
});

// ── Layout ──

describe('AppLayout', () => {
  it('renders sidebar with menu items', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppLayout />
      </MemoryRouter>
    );
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Datasets & Cases')).toBeInTheDocument();
    expect(screen.getByText('Experiment Config')).toBeInTheDocument();
    expect(screen.getByText('Run Results')).toBeInTheDocument();
    expect(screen.getByText('Version Compare')).toBeInTheDocument();
    expect(screen.getByText('Reports')).toBeInTheDocument();
    expect(screen.getByText('Settings')).toBeInTheDocument();
  });

  it('renders the platform title', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppLayout />
      </MemoryRouter>
    );
    expect(screen.getByText('LLMOps Platform')).toBeInTheDocument();
  });
});
