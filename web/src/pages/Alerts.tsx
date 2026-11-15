// Anomaly alerts, grouped into incidents (one service's alerts close together in time). The list
// says what to look at; an incident's page shows the lines the model found unusual and what it
// expected instead, what else broke at the same time, a root-cause investigation, and an activity timeline of
// status changes and comments; the header keeps Resolve and the other actions in view.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { IncidentAsk } from "../ask";
import { api, useAction, usePoll, type Incident, type IncidentDetail, type IncidentStatus } from "../api";
import { Card, Empty, Loading, PageHead, Pop, Switch, TimeAgo, time } from "../ui";

export const STATUS: Record<IncidentStatus, { label: string; badge: string }> = {
  open: { label: "Open", badge: "bad" },
  acknowledged: { label: "Acknowledged", badge: "warn" },
  resolved: { label: "Resolved", badge: "good" },
  false_positive: { label: "False positive", badge: "" },
};

function span(start: string, end: string) {
  const s = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const FILTERS = { active: (i: Incident) => i.status === "open" || i.status === "acknowledged", all: () => true,
  resolved: (i: Incident) => i.status === "resolved" || i.status === "false_positive" };

export default function Alerts() {
  const { data, error } = usePoll<{ incidents: Incident[] }>("/api/incidents", 5000);
  const [filter, setFilter] = useState<keyof typeof FILTERS>("active");
  const go = useNavigate();
  const shown = data?.incidents.filter(FILTERS[filter]) ?? [];
  return (
    <>
      <PageHead title="Anomaly alerts"
        sub={<>Stretches of a service's logs its LogBERT model finds unusual, grouped into incidents. Kinds of lines a model has never seen are not alerts: they are listed under <Link to="/maintenance">Maintenance</Link>.</>} />
      <Card actions={
        <div className="seg" role="group" aria-label="Show">
          {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((f) => (
            <button key={f} type="button" className={filter === f ? "on" : ""} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "active" ? "Needs attention" : f === "all" ? "All" : "Closed"} {data?.incidents.filter(FILTERS[f]).length ?? ""}
            </button>
          ))}
        </div>}>
        {!data ? <Loading error={error} /> : shown.length === 0 ? <Empty title={filter === "active" ? "Nothing needs attention" : "No incidents"}>Nothing unusual so far.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Status</th><th>Service</th><th>When</th><th className="right">Alerts</th><th className="right">Worst</th><th>What happened</th></tr></thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={i.id} className="clickable" tabIndex={0} onClick={() => go(`/alerts/${i.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && go(`/alerts/${i.id}`)}>
                    <td><span className={`badge ${STATUS[i.status].badge}`}>{STATUS[i.status].label}</span></td>
                    <td><strong>{i.service || i.host}</strong> <span className="muted small">on {i.host}</span></td>
                    <td className="small nowrap">{time(i.start)} <span className="muted">· {span(i.start, i.end)}</span></td>
                    <td className="right num">{i.alerts}</td>
                    <td className="right num">{Math.round(i.peak_miss * 100)}%</td>
                    <td className="small ellipsis" style={{ maxWidth: 380 }}>{(typeof i.explanation?.answer === "object" ? i.explanation.answer.summary : i.explanation?.message) ?? <span className="muted">Open it to see the unusual lines</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** Moving an incident to a status, with an optional note that goes on its activity. */
function Confirm({ inc, status, onDone }: { inc: IncidentDetail; status: IncidentStatus; onDone: () => void }) {
  const [text, setText] = useState("");
  const set = useAction(() => api(`/api/incidents/${inc.id}`, { method: "PUT", json: { status, note: text } }));
  const fp = status === "false_positive";
  return (
    <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); set.mutate(undefined, { onSuccess: onDone }); }}>
      <div className="pop-title">{fp ? "Mark as a false positive" : status === "resolved" ? "Resolve this incident" : `Mark as ${STATUS[status].label.toLowerCase()}`}</div>
      {fp && <p className="muted small" style={{ margin: 0 }}>This is how {inc.service || inc.host} normally behaves. Its stretches join the training data, so the next retrain of <Link to="/streams">{inc.stream}</Link> stops flagging them.</p>}
      <textarea autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="Note"
        placeholder={fp ? "Why it's normal (optional)" : status === "resolved" ? "What fixed it? (optional)" : "Note (optional)"} />
      <div className="row">
        <button className="btn small primary" disabled={set.isPending}>{fp ? "Mark as false positive" : status === "resolved" ? "Resolve" : "Confirm"}</button>
        <button type="button" className="btn small ghost" onClick={onDone}>Cancel</button>
      </div>
      {set.error && <div className="error-text small">{set.error.message}</div>}
    </form>
  );
}

function MoreMenu({ inc, close }: { inc: IncidentDetail; close: () => void }) {
  const [to, setTo] = useState<IncidentStatus | null>(null);
  const reopen = useAction(() => api(`/api/incidents/${inc.id}`, { method: "PUT", json: { status: "open", note: "" } }));
  if (to) return <Confirm inc={inc} status={to} onDone={close} />;
  return (
    <ul className="menu-list">
      {inc.status !== "false_positive" && <li><button type="button" onClick={() => setTo("false_positive")}>Not a problem…<span className="muted small">A false positive the model learns from</span></button></li>}
      {inc.status !== "open" && <li><button type="button" onClick={() => reopen.mutate(undefined, { onSuccess: close })}>Reopen</button></li>}
    </ul>
  );
}

const DOTS = <svg className="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></svg>;

/** Title, status and the facts on the left; what you can do about it on the right. Stays in view. */
function IncidentHeader({ inc }: { inc: IncidentDetail }) {
  useEffect(() => { document.title = `${inc.service || inc.host} incident · logpipe`; }, [inc.service, inc.host]);
  const ack = useAction(() => api(`/api/incidents/${inc.id}`, { method: "PUT", json: { status: "acknowledged", note: "" } }));
  const reopen = useAction(() => api(`/api/incidents/${inc.id}`, { method: "PUT", json: { status: "open", note: "" } }));
  const closed = inc.status === "resolved" || inc.status === "false_positive";
  return (
    <header className="inc-head">
      <div className="inc-head-main">
        <Link to="/alerts" className="inc-back small">Anomaly alerts</Link>
        <div className="inc-title">
          <h1>{inc.service || inc.host} <span className="muted">on {inc.host}</span></h1>
          <span className={`badge ${STATUS[inc.status].badge}`}>{STATUS[inc.status].label}</span>
        </div>
        <div className="inc-facts">
          <span>{time(inc.start)}</span><span>lasted {span(inc.start, inc.end)}</span>
          <span><strong className="num">{inc.alerts}</strong> alert{inc.alerts === 1 ? "" : "s"}</span>
          <span>up to <strong className="num">{Math.round(inc.peak_miss * 100)}%</strong> of events mispredicted</span>
        </div>
      </div>
      <div className="inc-actions">
        {inc.status === "open" && <button className="btn" disabled={ack.isPending} onClick={() => ack.mutate()}>Acknowledge</button>}
        {closed ? <button className="btn" disabled={reopen.isPending} onClick={() => reopen.mutate()}>Reopen</button>
          : <Pop className="btn-pop primary" label="Resolve" title="Resolve with an optional note">{(close) => <Confirm inc={inc} status="resolved" onDone={close} />}</Pop>}
        <span className="inc-more"><Pop className="btn-pop icon-only" label={<>{DOTS}<span className="sr-only">More actions</span></>} title="More actions">{(close) => <MoreMenu inc={inc} close={close} />}</Pop></span>
        {(ack.error || reopen.error) && <span className="error-text small">{(ack.error ?? reopen.error)!.message}</span>}
      </div>
    </header>
  );
}

/** Status changes and comments in one timeline, with a box to add a comment. */
function Activity({ inc }: { inc: IncidentDetail }) {
  const [text, setText] = useState("");
  const add = useAction(() => api(`/api/incidents/${inc.id}/comments`, { method: "POST", json: { text } }));
  // incidents from before the timeline kept one note: show it as the first entry
  const items = inc.activity?.length ? inc.activity
    : inc.note ? [{ kind: "comment" as const, text: inc.note, by: inc.by ?? null, at: inc.updated_at ?? inc.end }] : [];
  return (
    <Card title="Activity" className="inc-activity">
      {items.length === 0 ? <p className="muted small" style={{ margin: "0 0 10px" }}>No comments or status changes yet.</p> : (
        <ol className="activity">
          {items.map((a, i) => (
            <li key={i} className={`act-${a.kind}`}>
              <span className="act-dot" aria-hidden="true" />
              <div className="grow">
                <div className="small">
                  <strong>{a.by ? `AI key ${a.by}` : "Admin"}</strong>{" "}
                  {a.kind === "status" && a.status ? <>marked it <span className={`badge ${STATUS[a.status].badge}`}>{STATUS[a.status].label}</span></> : "commented"}
                  <span className="muted"> · <TimeAgo iso={a.at} /></span>
                </div>
                {a.text && <p className="act-text">{a.text}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
      <form className="act-compose" onSubmit={(e) => { e.preventDefault(); if (text.trim()) add.mutate(undefined, { onSuccess: () => setText("") }); }}>
        <textarea id="inc-comment" rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Comment" placeholder="Add a comment: what you found, what you did"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) add.mutate(undefined, { onSuccess: () => setText("") }); }} />
        <div className="row">
          <button className="btn small" disabled={!text.trim() || add.isPending}>Comment</button>
          <span className="muted small">Ctrl/⌘ + Enter</span>
          {add.error && <span className="error-text small">{add.error.message}</span>}
        </div>
      </form>
    </Card>
  );
}

const MARK = { expected: "", unexpected: "unexpected", unseen: "never seen" };

export function IncidentPage() {
  const { id } = useParams();
  const { data: inc, error } = usePoll<IncidentDetail>(`/api/incidents/${id}`, 4000);
  const [onlyUnusual, setOnlyUnusual] = useState(false);
  if (!inc) return <><PageHead title="Incident" /><Loading error={error} /></>;
  const unusual = inc.events.filter((e) => e.status !== "expected").length;
  const events = onlyUnusual ? inc.events.filter((e) => e.status !== "expected") : inc.events;
  return (
    <>
      <IncidentHeader inc={inc} />
      <div className="inc-layout">
      <div className="stack">
        <IncidentAsk incident={inc.id} plan={inc.ask_plan} />
        <Card title={`The lines (${unusual} of ${inc.events.length} unusual)`}
          hint={inc.model_current ? "Each line masked in turn: did the model expect it there, and what it expected instead."
            : "The model was retrained since this incident: marks come from the current model."}
          actions={<Switch checked={onlyUnusual} onChange={setOnlyUnusual} label="Only unusual" />}>
          {events.length === 0 ? <Empty title="No lines">The lines of this stretch are no longer stored.</Empty> : (
            <ol className="incident-lines">
              {events.map((e, i) => (
                <li key={i} className={`ev-${e.status}`}>
                  <span className="mono small muted nowrap">{new Date(e.timestamp).toLocaleTimeString()}</span>
                  <div className="grow">
                    <div className="mono small ev-msg" title={e.template}>{e.message}</div>
                    {e.status !== "expected" && (
                      <div className="small">
                        <span className={`badge ${e.status === "unseen" ? "warn" : "bad"}`}>{MARK[e.status]}</span>
                        {e.status === "unexpected" && e.expected.length > 0 && <span className="muted"> expected here: {e.expected.map((x, j) => (
                          <span key={x.template_id}>{j > 0 && " or "}<span className="mono" style={{ color: "var(--ink)" }} title={x.template}>{x.words}</span></span>))}</span>}
                        {e.status === "unseen" && <span className="muted"> a kind of line this model never learned</span>}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
          <div className="small" style={{ marginTop: 8 }}><Link to={`/logs?host=${encodeURIComponent(inc.host)}&service=${encodeURIComponent(inc.service)}`}>Open this service in the Log Explorer</Link></div>
        </Card>
        <Card title="Elsewhere at the same time" hint="Warnings and errors other services logged from a minute before to a minute after">
          {inc.around.length === 0 ? <Empty title="Quiet">No other service logged warnings or errors then.</Empty> : (
            <ol className="incident-lines">
              {inc.around.map((a, i) => (
                <li key={i}>
                  <span className="mono small muted nowrap">{new Date(a.timestamp).toLocaleTimeString()}</span>
                  <span className={`badge ${a.level === "error" ? "bad" : "warn"}`}>{a.service || a.host}</span>
                  <div className="grow mono small ev-msg">{a.message}</div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
      <Activity inc={inc} />
      </div>
    </>
  );
}
