export type RunStatus =
  "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type StepStatus =
  "PENDING" | "READY" | "RUNNING" | "RETRYING" | "COMPLETED" | "FAILED";
export type Usage = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  missing_token_calls: number;
  estimated_cost_usd: string;
  unpriced_calls: number;
};
export type Run = {
  usage?: Usage;
  id: string;
  workflow_name: string;
  status: RunStatus;
  input: unknown;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};
export type LLMCall = {
  id: string;
  cached_input_tokens: number | null;
  estimated_cost_usd: string | null;
  provider: string;
  model: string;
  input: unknown;
  output: unknown;
  input_tokens: number | null;
  output_tokens: number | null;
  status: "RUNNING" | "COMPLETED" | "FAILED" | "ABANDONED";
  error: string | null;
  started_at: string;
  completed_at: string | null;
};
export type Attempt = {
  id: string;
  attempt_number: number;
  worker_id: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | "ABANDONED";
  started_at: string;
  completed_at: string | null;
  error: string | null;
  llm_calls: LLMCall[];
};
export type Step = {
  id: string;
  step_name: string;
  status: StepStatus;
  input: unknown;
  output: unknown;
  error: string | null;
  attempt_count: number;
  max_attempts: number;
  next_retry_at: string | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  depends_on: string[];
  attempts: Attempt[];
};
export type Detail = Run & { steps: Step[] };
