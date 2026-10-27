// ---------- Dataset ----------
export interface Dataset {
  id: number;
  name: string;
  description: string | null;
  case_type: string;
  created_at: string;
  updated_at: string;
  case_count: number;
}

export interface EvalCase {
  id: number;
  dataset_id: number;
  case_type: string;
  input: string;
  reference_answer: string | null;
  expected_tool: string | null;
  expected_args: Record<string, unknown> | null;
  reference_context_ids: string[] | null;
  turns: TurnInput[] | null;
  tags: string[] | null;
  difficulty: string | null;
  extra_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface TurnInput {
  user: string;
  reference: string | null;
}

// ---------- Version ----------
export interface Version {
  id: number;
  version_type: string;
  name: string;
  config_json: Record<string, unknown>;
  description: string | null;
  created_at: string;
}

// ---------- Run ----------
export interface EvalRun {
  id: number;
  name: string;
  dataset_id: number;
  prompt_version_id: number | null;
  model_version_id: number | null;
  retriever_version_id: number | null;
  agent_version_id: number | null;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  avg_score: number | null;
  avg_latency_ms: number | null;
  avg_tokens: number | null;
  config_json: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
}

export interface EvalResult {
  id: number;
  run_id: number;
  case_id: number;
  status: string;
  actual_output: string | null;
  actual_tool: string | null;
  actual_args: Record<string, unknown> | null;
  scores: Record<string, ScoreDetail> | null;
  failure_reason: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface ScoreDetail {
  score: number;
  reason: string;
  success: boolean;
}

// ---------- Config ----------
export interface ProviderInfo {
  id: string;
  name: string;
  default_model: string;
  base_url: string | null;
  env_key: string | null;
  configured: boolean;
  requires_api_key: boolean;
  requires_model: boolean;
  requires_base_url: boolean;
  supports_model_fetch: boolean;
}

export interface APIKeyInfo {
  id: number;
  provider: string;
  api_key_masked: string;
  base_url: string | null;
  default_model: string;
  created_at: string;
  updated_at: string;
}

export interface ProviderModelInfo {
  id: string;
  label: string;
  owned_by: string | null;
}

export interface ProviderModelList {
  provider: string;
  source: string;
  warning: string | null;
  models: ProviderModelInfo[];
}

// ---------- Dashboard ----------
export interface DashboardStats {
  total_runs: number;
  avg_pass_rate: number;
  avg_latency_ms: number;
  avg_tokens: number;
  total_cases: number;
}

// 按天聚合的评测趋势（GET /dashboard/trends）
export interface TrendBucket {
  date: string;
  runs: number;
  avg_score: number | null;
  pass_rate: number | null;
  avg_latency_ms: number | null;
  avg_tokens: number | null;
}

// ---------- Report ----------
export interface Report {
  id: number;
  run_id: number;
  report_markdown: string;
  summary_json: Record<string, unknown> | null;
  created_at: string;
}

// ---------- Compare ----------
export interface RunCompareOut {
  run1: EvalRun;
  run2: EvalRun;
  metric_diffs: Record<string, number>;
  improved_cases: CompareCaseEntry[];
  regressed_cases: CompareCaseEntry[];
  summary: string;
}

export interface CompareCaseEntry {
  case_id: number;
  input?: string;
  run1_score: number;
  run2_score: number;
  delta: number;
  run1_status: string;
  run2_status: string;
}

// ---------- Trace ----------
export interface TraceStep {
  id: number;
  step_name: string;
  step_type: string;
  parent_step_id: number | null;
  step_key: string | null;
  parent_key: string | null;
  input_json: Record<string, unknown> | null;
  output_json: Record<string, unknown> | null;
  latency_ms: number;
  tokens: number | null;
  error_message: string | null;
  order_index: number;
}

export interface Trace {
  id: number;
  trace_id: string;
  run_id: number | null;
  case_id: number | null;
  user_input: string;
  session_id: string | null;
  prompt_version: string | null;
  model: string | null;
  retriever_version: string | null;
  status: string;
  total_latency_ms: number;
  total_tokens: number;
  error_message: string | null;
  created_at: string;
  steps: TraceStep[];
}

// ---------- API Response ----------
export interface ListResponse<T> {
  items: T[];
  total: number;
}

// ---------- Agentic Ops: Insight (L2) ----------
export interface InsightResult {
  judge: string;
  iterations: number;
  elapsed_ms: number;
  failure_distribution: Record<string, number> | null;
  top_failure_categories: [string, number][] | null;
  intent_clusters: string[] | null;
  insight_summary: string;
  trace?: Array<Record<string, unknown>>;
}

// ---------- Agentic Ops: Self-Healing (L3 + L4 半闭环) ----------
export interface ProposedFix {
  approval_id?: string;
  tool_name?: string;
  question?: string;
  risk_level?: string;
  status?: string;
}

export interface ExecutedFix {
  action: string;
  run_id?: number;
  regression_run_id?: number;
  status?: string;
  verdict?: string;
  score_delta?: number | null;
  completed_cases?: number;
  total_cases?: number;
  [key: string]: unknown;
}

export interface HealingResult {
  judge: string;
  iterations: number;
  elapsed_ms: number;
  proposed_fixes: ProposedFix[];
  executed_fixes: ExecutedFix[];
  regression_run_id: number | null;
  regression_verdict: 'improved' | 'declined' | 'unchanged' | 'pending' | string;
  score_delta: number | null;
  healing_summary: string;
  trace?: Array<Record<string, unknown>>;
}
