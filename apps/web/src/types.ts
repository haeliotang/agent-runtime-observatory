export type RunStatus = "pending" | "running" | "completed" | "failed";

export type Decision = "allow" | "deny" | "needs_review";

export interface ExampleInfo {
  name: string;
  title: string;
  goal: string;
  steps: number;
  policy_id: string;
}

export interface RunSummary {
  id: string;
  created_at: string;
  status: RunStatus;
  example: string | null;
  steps: number;
  denials: number;
  duration_ms: number | null;
}

export interface AgentStep {
  index: number;
  kind: string;
  name: string;
  args: Record<string, unknown>;
  input_digest: string | null;
  output_digest: string | null;
  output_preview: string | null;
  decision_id: string | null;
  started_at: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface PolicyDecision {
  id: string;
  run_id: string;
  step_index: number;
  policy_id: string;
  rule_id: string;
  decision: Decision;
  reason: string;
}

export interface RiskSignal {
  id: string;
  run_id: string;
  step_index: number;
  severity: string;
  category: string;
  message: string;
}

export interface Artifact {
  id: string;
  run_id: string;
  path: string;
  digest: string;
  media_type: string;
  size_bytes: number;
}

export interface AgentRun {
  id: string;
  task_id: string;
  agent: string;
  model: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  steps: AgentStep[];
  policy_decisions: PolicyDecision[];
  risk_signals: RiskSignal[];
  evidence: unknown[];
  artifacts: Artifact[];
}

export interface RunDetailResponse {
  run: AgentRun;
  task: unknown;
  example: string | null;
  trace_path: string | null;
}

export interface CreateRunResponse {
  run_id: string;
  queued: boolean;
  run: AgentRun;
}

export interface Divergence {
  step_index: number;
  field: string;
  recorded: unknown;
  replayed: unknown;
}

export interface ReplayReport {
  ok: boolean;
  run_id: string;
  replayed_at: string;
  steps_compared: number;
  divergences: Divergence[];
}
