"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Detail, Run, Step, LLMCall } from "@/lib/types";
import { startPolling } from "@/lib/polling";
import { cost, tokenTotal, usageCost } from "@/lib/usage";
import { orderSteps } from "@/lib/steps";
import { Icon } from "@/components/icons";

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value.toLowerCase()}`}>
      <span aria-hidden="true" className="dot" />
      {value === "WAITING_APPROVAL" ? "Needs review" : value.toLowerCase().replaceAll("_", " ")}
    </span>
  );
}
function date(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}
function duration(run: {
  started_at: string | null;
  completed_at: string | null;
}) {
  if (!run.started_at) return "—";
  const seconds = Math.max(
    0,
    ((run.completed_at ? Date.parse(run.completed_at) : Date.now()) -
      Date.parse(run.started_at)) /
      1000,
  );
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
function Json({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2) ?? "null"}</pre>;
}
function Result({ value }: { value: unknown }) {
  return <div className="result">
    {typeof value === "string" ? <p className="result-text">{value}</p>
      : value && typeof value === "object" && !Array.isArray(value) ? <dl className="result-fields">
        {Object.entries(value).map(([key, item]) => <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{typeof item === "string" ? <span className="result-text">{item}</span>
            : item === null ? <span className="muted">null</span>
            : typeof item === "object" ? <Json value={item} /> : String(item)}</dd>
        </div>)}
      </dl> : <Json value={value} />}
    <details className="raw-data"><summary>View JSON</summary><Json value={value} /></details>
  </div>;
}
function ModelCalls({ calls }: { calls: LLMCall[] }) {
  return (
    <section className="model-calls" aria-label="LLM calls">
      {calls.length === 0 && <p className="empty-inline">No model calls recorded.</p>}
      {calls.map((call) => (
        <details className="model-call" key={call.id}>
          <summary>
            <span className="call-model"><strong>{call.model}</strong><span className="muted">{call.provider}</span></span>
            <span className="call-metrics mono">{duration(call)} · {cost(call.estimated_cost_usd)}</span>
            <Status value={call.status} />
          </summary>
          <dl className="facts">
            <div><dt>Input tokens</dt><dd>{call.input_tokens ?? "Not reported"}</dd></div>
            <div><dt>Output tokens</dt><dd>{call.output_tokens ?? "Not reported"}</dd></div>
            <div><dt>Cached input</dt><dd>{call.cached_input_tokens ?? "Not reported"}</dd></div>
            <div><dt>Duration</dt><dd>{duration(call)}</dd></div>
            <div><dt>Started</dt><dd>{date(call.started_at)}</dd></div>
            <div><dt>Finished</dt><dd>{date(call.completed_at)}</dd></div>
            <div><dt>Est. cost</dt><dd>{cost(call.estimated_cost_usd)}</dd></div>
          </dl>
          {call.error && <p className="attempt-error">{call.error}</p>}
          <h3>Response</h3>
          {call.status === "COMPLETED" ? <Result value={call.output} /> : <p className="empty-inline">No completed response recorded.</p>}
          <details className="raw-data"><summary>Request</summary><Json value={call.input} /></details>
        </details>
      ))}
    </section>
  );
}
function ApprovalReview({ step, run, reload }: { step: Step; run: Detail; reload: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function decide(decision: "approved" | "rejected") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/workflows/${run.id}/steps/${step.id}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to save decision.");
      reload();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to save decision.");
    } finally {
      setBusy(false);
    }
  }
  if (step.approval_decision) return (
    <section className="approval-review" aria-label="Review decision">
      <h3>{step.approval_decision === "approved" ? "Approved" : "Rejected"}</h3>
      <p className="muted">{date(step.approval_decided_at ?? null)}</p>
      {step.approval_note && <p className="review-note">{step.approval_note}</p>}
    </section>
  );
  if (step.status !== "WAITING_APPROVAL") return null;
  return (
    <section className="approval-review" aria-label="Human review">
      <h3>Review required</h3>
      <p>Approve to continue dependent steps, or reject to stop this workflow.</p>
      {run.status === "RUNNING" ? <>
        <label htmlFor={`review-note-${step.id}`}>Review note (optional)</label>
        <textarea id={`review-note-${step.id}`} value={note} maxLength={2000} disabled={busy} onChange={(event) => setNote(event.target.value)} />
        <div className="review-actions">
          <button className="primary" disabled={busy} onClick={() => decide("approved")}>Approve output</button>
          <button className="review-reject" disabled={busy} onClick={() => decide("rejected")}>Reject output</button>
        </div>
      </> : <p>This workflow has ended and no longer accepts decisions.</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
function StepInspection({ step, run, reload }: { step: Step; run: Detail; reload: () => void }) {
  const [section, setSection] = useState("Output");
  const attempts = [...(step.attempts ?? [])].reverse();
  const calls = attempts.flatMap(attempt => attempt.llm_calls ?? []);
  const sections = [
    {name: "Output"}, {name: "Model calls", count: calls.length},
    {name: "Attempts", count: attempts.length}, {name: "Input"},
  ];
  return (
    <section className="inspection" aria-label="Step details">
      <div className="section-heading">
        <h2>{step.step_name}</h2>
        <Status value={step.status} />
      </div>
      <dl className="facts inspection-summary">
        <div><dt>Elapsed</dt><dd>{duration(step)}</dd></div>
        <div><dt>Attempts</dt><dd>{step.attempt_count} / {step.max_attempts}</dd></div>
        <div><dt>Dependencies</dt><dd>{step.depends_on.map(id => run.steps.find(s => s.id === id)?.step_name || id).join(", ") || "None"}</dd></div>
      </dl>
      <nav className="inspector-tabs" aria-label="Step sections">
        {sections.map(item => <button key={item.name} aria-pressed={section === item.name} onClick={() => setSection(item.name)}>
          {item.name}{item.count !== undefined && <span className="count">{item.count}</span>}
        </button>)}
      </nav>
      <div className="inspector-body">
        <div hidden={section !== "Output"}>
          {step.error && <div className="error-banner"><strong>Last error</strong><p>{step.error}</p></div>}
          {(step.status === "COMPLETED" || step.approval_requested_at)
            ? <Result value={step.output} />
            : <p className="empty-inline">{step.status === "RUNNING" ? "Output will appear when execution finishes." : "No output recorded yet."}</p>}
          <ApprovalReview step={step} run={run} reload={reload} />
        </div>
        {section === "Model calls" && <>
          {calls.length === 0 && <p className="empty-inline">No model calls recorded.</p>}
          {attempts.filter(attempt => attempt.llm_calls?.length).map(attempt => <div key={attempt.id}>
            {attempts.length > 1 && <h3>Attempt {attempt.attempt_number}</h3>}
            <ModelCalls calls={attempt.llm_calls} />
          </div>)}
        </>}
        {section === "Attempts" && <>
          {step.attempt_count > attempts.length && <p className="empty-inline">History is unavailable for earlier attempts.</p>}
          {!step.attempt_count && <p className="empty-inline">This step has not started.</p>}
          <ol className="attempt-history" aria-label="Attempt history">
            {attempts.map(attempt => <li key={attempt.id}>
              <details>
                <summary className="attempt-heading">
                  <strong>Attempt {attempt.attempt_number}</strong>
                  <span className="mono muted">{duration(attempt)}</span>
                  <Status value={attempt.status} />
                </summary>
                <dl className="facts">
                  <div><dt>Started</dt><dd>{date(attempt.started_at)}</dd></div>
                  <div><dt>Finished</dt><dd>{date(attempt.completed_at)}</dd></div>
                  <div className="full"><dt>Worker</dt><dd className="mono">{attempt.worker_id}</dd></div>
                </dl>
                {attempt.error && <pre className="attempt-error">{attempt.error}</pre>}
                <ModelCalls calls={attempt.llm_calls ?? []} />
              </details>
            </li>)}
          </ol>
          <dl className="facts">
            {step.next_retry_at && <div><dt>Next retry</dt><dd>{date(step.next_retry_at)}</dd></div>}
            {step.lease_expires_at && <div><dt>Lease expires</dt><dd>{date(step.lease_expires_at)}</dd></div>}
            {step.lease_owner && <div className="full"><dt>Active worker</dt><dd className="mono">{step.lease_owner}</dd></div>}
          </dl>
        </>}
        {section === "Input" && (step.input === null
          ? <p className="empty-inline">Input is available when the step starts.</p>
          : <Result value={step.input} />)}
      </div>
      <details className="step-metadata"><summary>Step metadata</summary>
        <dl className="facts">
          <div className="full"><dt>Step ID</dt><dd className="mono">{step.id}</dd></div>
          <div><dt>Started</dt><dd>{date(step.started_at)}</dd></div>
          <div><dt>Completed</dt><dd>{date(step.completed_at)}</dd></div>
        </dl>
      </details>
    </section>
  );
}
export function Dashboard({ runId }: { runId?: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<string | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  const reload = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setLoading(true);
    setError("");
    setRuns([]);
    setTotal(null);
    setDetail(null);
    setMore(false);
    setUpdated(null);
    const query = new URLSearchParams({
      limit: "21",
      offset: String(page * 20),
    });
    if (filter) query.set("status", filter);
    const url = runId
      ? `/api/workflows/${encodeURIComponent(runId)}`
      : `/api/workflows?${query}`;
    const polling = startPolling(
      async (signal) => {
        const response = await fetch(url, { signal, cache: "no-store" });
        const data = await response.json();
        if (!response.ok)
          throw new Error(data.detail || "Unable to load workflows.");
        if (signal.aborted) return;
        if (runId) setDetail({ ...data, steps: orderSteps(data.steps) });
        else {
          const count = response.headers.get("X-Total-Count");
          setTotal(count === null ? null : Number(count));
          setRuns(data.slice(0, 20));
          setMore(data.length > 20);
        }
        setError("");
        setLoading(false);
        setUpdated(new Date().toLocaleTimeString());
      },
      (error) => {
        setError(
          error instanceof Error ? error.message : "Unable to load workflows.",
        );
        setLoading(false);
      },
      () => document.visibilityState === "visible",
    );
    refreshRef.current = polling.refresh;
    const onVisible = () => {
      if (document.visibilityState === "visible") polling.refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      polling.stop();
      document.removeEventListener("visibilitychange", onVisible);
      refreshRef.current = () => {};
    };
  }, [runId, filter, page]);
  const step = detail?.steps.find((s) => s.id === selected) || detail?.steps.find((s) => s.status === "WAITING_APPROVAL") || detail?.steps[0];
  return (
    <>
      <div className={`page-heading ${runId ? "detail-heading" : ""}`}>
        <div>
          {runId ? (
            <Link className="back" href="/">
              <Icon name="arrow-left" /> All workflow runs
            </Link>
          ) : (
            <p className="eyebrow">
              <Icon name="workflow" /> Workflows
            </p>
          )}
          <h1>
            {runId
              ? detail?.workflow_name || "Workflow details"
              : "Workflow runs"}
          </h1>
          <p className="subtitle">
            {runId
              ? `Run ${detail?.id.slice(0, 8) || runId.slice(0, 8)}`
              : "Execution history"}
          </p>
        </div>
        <div className="heading-actions">
          <button className="refresh" onClick={reload} disabled={loading}>
            <Icon name="refresh" /> {loading ? "Refreshing…" : "Refresh"}
          </button>
          <span className="update-line">
            {error
              ? updated
                ? `Reconnecting · Last updated ${updated}`
                : "Data unavailable"
              : updated
                ? `Live · ${updated}`
                : "Connecting…"}
          </span>
        </div>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          <strong>
            {updated ? "Unable to refresh data" : "Unable to load data"}
          </strong>
          <p>{error}</p>
          <button onClick={reload}>Try again</button>
        </div>
      )}
      {!error && loading && !updated && (
        <div className="empty" role="status">
          Loading workflow data…
        </div>
      )}
      {!runId && (
        <section className="runs-panel" aria-label="Workflow runs">
          <div className="toolbar">
            <div>
              <h2>
                Runs{" "}
                <span className="count">
                  {loading ? "…" : `${runs.length ? `${page * 20 + 1}–${page * 20 + runs.length}` : "0"} of ${total ?? "—"}`}
                </span>
              </h2>
              <p className="muted">Newest first</p>
            </div>
            <label className="status-filter">
              <Icon name="filter" />
              <span className="sr-only">Status</span>
              <select
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setPage(0);
                }}
              >
                <option value="">All statuses</option>
                {["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"].map(
                  (s) => (
                    <option key={s} value={s}>
                      {s.toLowerCase()}
                    </option>
                  ),
                )}
              </select>
            </label>
          </div>
          <div className="table-wrap">
            <table className="workflow-table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Status</th>
                  <th className="created-column">Created</th>
                  <th className="numeric">Duration</th>
                  <th className="numeric">Tokens</th>
                  <th className="numeric">Est. cost</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <div className="workflow-cell">
                        <div>
                          <Link
                            className="run-link"
                            title={run.workflow_name}
                            href={`/workflows/${run.id}`}
                          >
                            {run.workflow_name}
                          </Link>
                          <span className="run-id mono" title={run.id}>
                            {run.id.slice(0, 8)}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Status value={run.status} />
                    </td>
                    <td className="time created-column">
                      <time dateTime={run.created_at}>
                        {new Date(run.created_at).toLocaleDateString(
                          undefined,
                          { month: "short", day: "numeric", year: "numeric" },
                        )}
                        <span>
                          {new Date(run.created_at).toLocaleTimeString(
                            undefined,
                            { hour: "2-digit", minute: "2-digit" },
                          )}
                        </span>
                      </time>
                    </td>
                    <td className="mono numeric">{duration(run)}</td>
                    <td className="mono numeric">{tokenTotal(run.usage)}</td>
                    <td className="mono numeric">{usageCost(run.usage)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!loading && !error && runs.length === 0 && (
              <div className="empty">
                <span className="empty-icon">
                  <Icon name="workflow" />
                </span>
                <h2>
                  {filter || page
                    ? "No matching runs"
                    : "Your first run starts here"}
                </h2>
                <p>
                  {filter || page
                    ? "Choose another status or return to the first page."
                    : "Start a workflow with relay.run() to see its progress here."}
                </p>
              </div>
            )}
          </div>
          {(page > 0 || more) && <div className="pagination">
            <span>Page {page + 1}</span>
            <div>
              <button
                disabled={page === 0 || loading}
                onClick={() => setPage((p) => p - 1)}
              >
                <Icon name="arrow-left" /> Previous
              </button>
              <button
                disabled={!more || loading}
                onClick={() => setPage((p) => p + 1)}
              >
                Next <Icon name="arrow-right" />
              </button>
            </div>
          </div>}
        </section>
      )}
      {runId && detail && (
        <>
          {detail.steps.some((step) => step.status === "WAITING_APPROVAL") && detail.status === "RUNNING" && (
            <p className="approval-banner" role="status"><span className="dot" /> Review required — select a step marked Needs review.</p>
          )}
          <section className="run-summary">
            <div><span>Status</span><Status value={detail.status} /></div>
            <div>
              <span>Started</span>
              <strong>{date(detail.started_at)}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong className="mono">{duration(detail)}</strong>
            </div>
            <div>
              <span title="Recorded tokens across every attempt">Tokens</span>
              <strong className="mono">{tokenTotal(detail.usage)}</strong>
            </div>
            <div>
              <span title="Text-token estimate across all attempts. Unknown usage and other provider charges are excluded.">Est. cost</span>
              <strong className="mono">{usageCost(detail.usage)}</strong>
            </div>
            <div>
              <span>Steps completed</span>
              <strong>
                {detail.steps.filter((s) => s.status === "COMPLETED").length} /{" "}
                {detail.steps.length}
              </strong>
            </div>
          </section>
          <div className="run-meta"><span className="mono" title={detail.id}>{detail.id}</span><details><summary>About usage</summary><p>Totals include all recorded attempts. Costs estimate text tokens only; unknown usage and other provider charges are excluded.</p></details></div>
          {detail.status === "CANCELLED" && (
            <p className="notice">
              This run is cancelled. New steps will not start; handlers already
              running may finish.
            </p>
          )}
          <div className="detail-grid">
            <section className="steps-panel">
              <div className="section-heading">
                <h2>Steps</h2>
                <span className="count">{detail.steps.length}</span>
              </div>
              <div className="step-list">
                {detail.steps.map((s) => (
                  <button
                    key={s.id}
                    className={`step-card ${step?.id === s.id ? "selected" : ""}`}
                    onClick={() => setSelected(s.id)}
                    aria-pressed={step?.id === s.id}
                  >
                    <div className="step-title">
                      <span
                        className={`step-symbol ${s.status.toLowerCase()}`}
                        aria-hidden="true"
                      >
                        <Icon
                          name={
                            s.status === "COMPLETED"
                              ? "check"
                              : s.status === "FAILED"
                                ? "close"
                                : "circle"
                          }
                        />
                      </span>
                      <strong>{s.step_name}</strong>
                    </div>
                    <Status value={s.status} />
                    <p>
                      {s.depends_on.length
                        ? `After ${s.depends_on.map((id) => detail.steps.find((p) => p.id === id)?.step_name || id).join(", ")}`
                        : "No dependencies"}
                    </p>
                    <span className="muted">
                      {s.attempt_count} / {s.max_attempts} attempts
                    </span>
                  </button>
                ))}
              </div>
              <details className="workflow-input">
                <summary>Workflow input</summary>
                <Json value={detail.input} />
              </details>
            </section>
            {step && <StepInspection key={step.id} step={step} run={detail} reload={reload} />}
          </div>
        </>
      )}
    </>
  );
}
