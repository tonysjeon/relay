"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Detail, Run, Step } from "@/lib/types";
import { startPolling } from "@/lib/polling";
import { orderSteps } from "@/lib/steps";
import { Icon } from "@/components/icons";

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value.toLowerCase()}`}>
      <span aria-hidden="true" className="dot" />
      {value.toLowerCase()}
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
function StepInspection({ step, run }: { step: Step; run: Detail }) {
  return (
    <section className="inspection" aria-label="Step details">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Step inspection</p>
          <h2>{step.step_name}</h2>
        </div>
        <Status value={step.status} />
      </div>
      <dl className="facts">
        <div>
          <dt>Attempts</dt>
          <dd>
            {step.attempt_count} / {step.max_attempts}
          </dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{duration(step)}</dd>
        </div>
        <div className="full">
          <dt>Worker</dt>
          <dd className="mono">{step.lease_owner || "No active lease"}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{date(step.started_at)}</dd>
        </div>
        <div>
          <dt>Completed</dt>
          <dd>{date(step.completed_at)}</dd>
        </div>
        {step.next_retry_at && (
          <div className="full">
            <dt>Retry scheduled</dt>
            <dd>{date(step.next_retry_at)}</dd>
          </div>
        )}
        {step.lease_expires_at && (
          <div className="full">
            <dt>Lease expires</dt>
            <dd>{date(step.lease_expires_at)}</dd>
          </div>
        )}
        <div className="full">
          <dt>Depends on</dt>
          <dd>
            {step.depends_on
              .map((id) => run.steps.find((s) => s.id === id)?.step_name || id)
              .join(", ") || "No dependencies"}
          </dd>
        </div>
      </dl>
      <h3>Attempt history</h3>
      {step.attempt_count > (step.attempts?.length ?? 0) && (
        <p className="muted">History is unavailable for earlier attempts.</p>
      )}
      {!step.attempt_count && <p className="muted">This step has not started yet.</p>}
      <ol className="attempt-history" aria-label="Attempt history">
        {[...(step.attempts ?? [])].reverse().map((attempt) => (
          <li key={attempt.id}>
            <div className="attempt-heading">
              <strong>Attempt {attempt.attempt_number}</strong>
              <Status value={attempt.status} />
            </div>
            <dl className="facts">
              <div className="full">
                <dt>Worker</dt>
                <dd className="mono">{attempt.worker_id}</dd>
              </div>
              <div>
                <dt>Started</dt>
                <dd>{date(attempt.started_at)}</dd>
              </div>
              <div>
                <dt>Finished</dt>
                <dd>{date(attempt.completed_at)}</dd>
              </div>
              <div>
                <dt>Duration</dt>
                <dd>{duration(attempt)}</dd>
              </div>
            </dl>
            {attempt.error && (
              <pre className="attempt-error">{attempt.error}</pre>
            )}
          </li>
        ))}
      </ol>
      {step.error && (
        <div className="error-detail">
          <h3>Last error</h3>
          <pre>{step.error}</pre>
        </div>
      )}
      <h3>
        <Icon name="code" /> Output
      </h3>
      {step.status === "COMPLETED" ? (
        <Json value={step.output} />
      ) : (
        <p className="muted">No completed output yet.</p>
      )}
      <h3>
        <Icon name="code" /> Execution input
      </h3>
      {step.input === null ? (
        <p className="muted">Available when this step is claimed.</p>
      ) : (
        <Json value={step.input} />
      )}
      <p className="step-id mono">{step.id}</p>
    </section>
  );
}
export function Dashboard({ runId }: { runId?: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
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
  const step = detail?.steps.find((s) => s.id === selected) || detail?.steps[0];
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
              ? "Inspect dependencies, execution state, and results."
              : "Monitor execution and explore the details of each run."}
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
                ? `Updated ${updated} · Every 2s`
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
                Run history{" "}
                <span className="count">
                  {runs.length}
                  {more ? "+" : ""}
                </span>
              </h2>
              <p className="muted">Sorted by newest first</p>
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
            <table>
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Status</th>
                  <th className="created-column">Created</th>
                  <th>Duration</th>
                  <th>
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <div className="workflow-cell">
                        <span className="workflow-icon">
                          <Icon name="workflow" />
                        </span>
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
                    <td className="mono">{duration(run)}</td>
                    <td>
                      <Link
                        aria-label={`Inspect ${run.workflow_name} ${run.id}`}
                        className="arrow"
                        href={`/workflows/${run.id}`}
                      >
                        <Icon name="chevron-right" />
                      </Link>
                    </td>
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
          <div className="pagination">
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
          </div>
        </section>
      )}
      {runId && detail && (
        <>
          <section className="run-summary">
            <Status value={detail.status} />
            <div>
              <span>Started</span>
              <strong>{date(detail.started_at)}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong className="mono">{duration(detail)}</strong>
            </div>
            <div>
              <span>Steps completed</span>
              <strong>
                {detail.steps.filter((s) => s.status === "COMPLETED").length} /{" "}
                {detail.steps.length}
              </strong>
            </div>
          </section>
          <p className="run-identifier mono">Run {detail.id}</p>
          {detail.status === "CANCELLED" && (
            <p className="notice">
              This run is cancelled. New steps will not start; handlers already
              running may finish.
            </p>
          )}
          <div className="detail-grid">
            <section className="steps-panel">
              <div className="section-heading">
                <h2>Execution steps</h2>
                <span className="muted">Select to inspect</span>
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
                      <Icon name="chevron-right" className="step-arrow" />
                    </div>
                    <Status value={s.status} />
                    <p>
                      {s.depends_on.length
                        ? `After ${s.depends_on.map((id) => detail.steps.find((p) => p.id === id)?.step_name || id).join(", ")}`
                        : "Starts independently"}
                    </p>
                    <span className="muted">
                      Attempt {s.attempt_count} of {s.max_attempts}
                    </span>
                  </button>
                ))}
              </div>
              <details className="workflow-input">
                <summary>Workflow input</summary>
                <Json value={detail.input} />
              </details>
            </section>
            {step && <StepInspection step={step} run={detail} />}
          </div>
        </>
      )}
    </>
  );
}
