"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { startPolling } from "@/lib/polling";

type CodingSession = {session_id: string; cwd: string; model: string | null; event_count: number; last_event: string; last_seen: string};
type CodingEvent = {sequence: number; event_id: string; event_type: string; occurred_at: string; received_at: string; cwd: string; model: string | null; turn_id: string | null; tool_name: string | null; content: string | null};
const labels: Record<string, string> = {
  SessionStart: "Session started", SessionEnd: "Session ended", UserPromptSubmit: "Prompt submitted",
  PreToolUse: "Tool started", PostToolUse: "Tool returned", Stop: "Turn stopped", Interrupt: "Turn interrupted",
};
export function CodingSessions({sessionId}: {sessionId?: string}) {
  const [sessions, setSessions] = useState<CodingSession[]>([]);
  const [events, setEvents] = useState<CodingEvent[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [page, setPage] = useState(0);
  const [more, setMore] = useState(false);
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    let cursor: number | undefined;
    setSessions([]); setEvents([]); setLoaded(false); setError("");
    const polling = startPolling(async (signal) => {
      const url = sessionId
        ? `/api/coding-sessions/${encodeURIComponent(sessionId)}/events?limit=100${cursor === undefined ? "" : "&after=" + cursor}`
        : `/api/coding-sessions?limit=21&offset=${page * 20}`;
      const response = await fetch(url, {signal, cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to load activity.");
      if (signal.aborted) return;
      if (sessionId) {
        const incoming = data as CodingEvent[];
        if (incoming.length) cursor = incoming[incoming.length - 1].sequence;
        setEvents(previous => [...previous, ...incoming].slice(-500));
      } else {
        setSessions(data.slice(0, 20)); setMore(data.length > 20);
      }
      setError(""); setLoaded(true);
    }, error => {setError(error instanceof Error ? error.message : "Unable to load activity.");},
    () => document.visibilityState === "visible");
    refresh.current = polling.refresh;
    const visible = () => {if (document.visibilityState === "visible") polling.refresh();};
    document.addEventListener("visibilitychange", visible);
    return () => {polling.stop(); refresh.current = () => {}; document.removeEventListener("visibilitychange", visible);};
  }, [sessionId, page]);
  const latest = events[events.length - 1];
  return <>
    <div className="page-heading">
      <div>
        {sessionId && <Link className="back" href="/coding-sessions"><Icon name="arrow-left" /> Coding sessions</Link>}
        <h1>{sessionId ? "Codex session" : "Coding sessions"}</h1>
        <p className="subtitle">{sessionId ? sessionId : "Activity from connected coding tools"}</p>
      </div>
      <div className="heading-actions">
        <button className="refresh" onClick={() => refresh.current()}><Icon name="refresh" /> Refresh</button>
        <span className="update-line">{error ? "Disconnected" : loaded ? "Live · Updates every 2s" : "Connecting…"}</span>
      </div>
    </div>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {!loaded && !error && <p className="empty-inline">Loading activity…</p>}
    {sessionId ? <>
      <div className="session-summary">
        <span title={latest?.cwd}>Workspace <strong>{latest?.cwd.split("/").filter(Boolean).pop() || "—"}</strong></span>
        <span>Model <strong>{latest?.model || "Not reported"}</strong></span>
        <span title="Codex hooks do not report reliable token usage or cost.">Usage <strong>Not reported</strong></span>
      </div>
      <section className="session-events" aria-label="Session events">
        <div className="toolbar"><h2>Events <span className="count">{events.length}</span></h2><span className="muted">Receipt order</span></div>
        {loaded && events.length === 0 && <p className="empty">No events received for this session.</p>}
        <ol className="coding-timeline">
          {events.map(event => <li key={event.event_id}>
            <details>
              <summary className="event-row">
                <time dateTime={event.occurred_at} title={new Date(event.occurred_at).toLocaleString()}>{new Date(event.occurred_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false})}</time>
                <strong>{labels[event.event_type] || event.event_type}</strong>
                <span className="event-preview">{event.content || event.tool_name || event.model || "Codex"}</span>
              </summary>
              <div className="event-body">
                {event.content != null && <p className="result-text">{event.content}</p>}
                {event.content == null && ["Stop", "UserPromptSubmit"].includes(event.event_type) && <p className="empty-inline">Content capture is off for this event.</p>}
                <dl>
                  <div><dt>Observed</dt><dd>{new Date(event.occurred_at).toLocaleString()}</dd></div>
                  <div><dt>Received</dt><dd>{new Date(event.received_at).toLocaleString()}</dd></div>
                  {event.tool_name && <div><dt>Tool</dt><dd>{event.tool_name}</dd></div>}
                  {event.turn_id && <div><dt>Turn</dt><dd className="mono">{event.turn_id}</dd></div>}
                </dl>
              </div>
            </details>
          </li>)}
        </ol>
        <p className="table-note">Latest 100 on load; up to 500 retained while open. A tool return does not imply success.</p>
      </section>
    </> : <section className="runs-panel" aria-label="Coding sessions">
      <div className="toolbar"><h2>Sessions <span className="count">{sessions.length}{more ? "+" : ""}</span></h2><span className="muted">Newest activity first</span></div>
      <div className="table-wrap"><table>
        <thead><tr><th>Session</th><th>Workspace</th><th>Last event</th><th className="numeric">Events</th><th>Last received</th></tr></thead>
        <tbody>{sessions.map(session => <tr key={session.session_id}>
          <td><Link className="run-link" href={`/coding-sessions/${encodeURIComponent(session.session_id)}`}>Codex <span className="muted mono">· {session.session_id.slice(0, 8)}</span></Link><span className="run-id">{session.model || "Model not reported"}</span></td>
          <td className="coding-path" title={session.cwd}>{session.cwd.split("/").filter(Boolean).pop() || session.cwd}</td>
          <td>{labels[session.last_event] || session.last_event}</td>
          <td className="numeric mono">{session.event_count}</td>
          <td className="time">{new Date(session.last_seen).toLocaleDateString([], {month: "short", day: "numeric"})}<span>{new Date(session.last_seen).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</span></td>
        </tr>)}</tbody>
      </table></div>
      {loaded && !sessions.length && <div className="empty"><h2>No coding sessions</h2><p>Install and trust the Relay hooks in Codex to start capturing activity.</p><p>Setup: README → Codex activity</p></div>}
      {(page > 0 || more) && <div className="pagination"><span>Page {page + 1}</span><div><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button disabled={!more} onClick={() => setPage(page + 1)}>Next</button></div></div>}
    </section>}
  </>;
}
