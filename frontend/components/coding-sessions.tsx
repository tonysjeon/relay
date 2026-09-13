"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
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
  return <>
    <div className="page-heading">
      <div>
        {sessionId && <Link className="back" href="/coding-sessions">← Coding sessions</Link>}
        <h1>{sessionId ? "Codex activity" : "Coding sessions"}</h1>
        <p className="subtitle">Observed activity from connected coding tools. Relay does not control these sessions.</p>
      </div>
      <button className="refresh" onClick={() => refresh.current()}>Refresh</button>
    </div>
    {error && <p role="alert" className="error-detail">{error}</p>}
    {!loaded && !error && <p className="muted">Loading activity…</p>}
    {sessionId ? <>
      <p className="mono">{sessionId}</p>
      <p className="muted">Latest received events · Updates every 2s · Tokens and cost are not reported by this hook adapter.</p>
      <p className="muted">Starts with the latest 100 events and retains up to 500 while open. Tool return events do not imply success.</p>
      {loaded && events.length === 0 && <p>No events received for this session.</p>}
      <ol className="coding-timeline">
        {events.map(event => <li key={event.event_id}>
          <div className="section-heading"><strong>{labels[event.event_type] || event.event_type}</strong><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time></div>
          <p className="muted">{event.tool_name || event.model || "Codex"}{event.turn_id && ` · Turn ${event.turn_id}`}</p>
          {event.content != null && <pre>{event.content}</pre>}
          {event.content == null && ["Stop", "UserPromptSubmit"].includes(event.event_type) && <p className="muted">Text was not captured. Content capture is optional.</p>}
        </li>)}
      </ol>
    </> : <>
      <div className="table-wrap"><table>
        <thead><tr><th>Session</th><th>Workspace</th><th>Last event</th><th>Events</th><th>Last received</th></tr></thead>
        <tbody>{sessions.map(session => <tr key={session.session_id}>
          <td><Link className="run-link" href={`/coding-sessions/${encodeURIComponent(session.session_id)}`}>Codex · {session.session_id.slice(0, 8)}</Link><span className="run-id">{session.model || "Model not reported"}</span></td>
          <td className="coding-path" title={session.cwd}>{session.cwd}</td>
          <td>{labels[session.last_event] || session.last_event}</td>
          <td>{session.event_count}</td><td>{new Date(session.last_seen).toLocaleString()}</td>
        </tr>)}</tbody>
      </table></div>
      {loaded && !sessions.length && <div className="approval-review"><h2>No coding sessions yet</h2><p>Install the Relay Codex hooks in your repository, then review and trust them in Codex. Future session and tool events will appear here.</p><p>Setup instructions are in the repository README under “Codex activity”.</p></div>}
      {(page > 0 || more) && <div className="review-actions"><button className="refresh" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="refresh" disabled={!more} onClick={() => setPage(page + 1)}>Next</button></div>}
    </>}
  </>;
}
