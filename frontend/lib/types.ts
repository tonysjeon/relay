export type RunStatus =
  "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type StepStatus =
  "PENDING" | "READY" | "RUNNING" | "RETRYING" | "COMPLETED" | "FAILED";
export type Run = {
  id: string;
  workflow_name: string;
  status: RunStatus;
  input: unknown;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
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
};
export type Detail = Run & { steps: Step[] };
