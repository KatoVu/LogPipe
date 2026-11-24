import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useAction, usePoll } from "../api";
import { TimeRange, useRange } from "../charts";
import { GraphView, HEALTH_COLOR, reach, type GraphData, type GraphEdge, type Health } from "../graph";
import { Card, Drawer, Empty, fmt, Loading, PageHead } from "../ui";

const RELATIONS = ["depends_on", "sends_to", "writes_to", "reads_from"];
const LEARNED = { llm: "the LLM, from the template", rules: "a rule: the template names it", manual: "added by hand" };

export default function Topology() {
  const [range, minutes, setRange] = useRange("1h");
  const { data, error } = usePoll<GraphData>(`/api/graph?minutes=${minutes}`, 5000);
  const [selected, setSelected] = useState<string | null>(null);
  const [edge, setEdge] = useState<GraphEdge | null>(null);

  if (!data) return <><PageHead title="Topology" /><Loading error={error} /></>;
  const up = selected ? [...reach(selected, data.edges, false)].sort() : [];
  const down = selected ? [...reach(selected, data.edges, true)].sort() : [];

  return (
    <>
      <PageHead title="Topology"
        sub="Which service depends on which, learned from the log templates when each was made. A link's colour is how its own lines look in the range."
        actions={<TimeRange value={range} onChange={setRange} />} />
      <div className="dash">
        <Card title="Components" className="w-8"
          hint={<span className="row graph-legend">{(Object.keys(HEALTH_COLOR) as Health[]).map((h) => (
            <span key={h} className="row"><i style={{ background: HEALTH_COLOR[h] }} />{h}</span>))}
            <span className="row"><i className="dashed" />by hand</span></span>}>
          {data.edges.length === 0 ? (
            <Empty title="No links yet">
              Links appear when a template names another service (a connection to <span className="mono">postgres:5432</span>,
              an upstream address). New templates are labelled as they're learned; you can also add one below.
            </Empty>
          ) : (
            <div className="graph-wrap">
              <GraphView data={data} selected={selected} onNode={(n) => setSelected(n === selected ? null : n)} onEdge={setEdge} />
            </div>
          )}
        </Card>
        <Card title={selected ? selected : "Pick a component"} className="w-4">
          {!selected ? <p className="muted small">Click a component to see what it needs and what breaks if it fails.</p> : (
            <>
              <h4 className="graph-h">Breaks if {selected} fails</h4>
              {up.length ? <ul className="graph-list">{up.map((n) => <li key={n} className="graph-up">{n}</li>)}</ul> : <p className="muted small">Nothing depends on it.</p>}
              <h4 className="graph-h">{selected} depends on</h4>
              {down.length ? <ul className="graph-list">{down.map((n) => <li key={n} className="graph-down">{n}</li>)}</ul> : <p className="muted small">Nothing it logs about.</p>}
              <Link className="btn small" to={`/logs?range=${range}&service=${encodeURIComponent(selected)}`}>Open its logs</Link>
            </>
          )}
        </Card>
        <Card title="Links" className="w-8">
          <table>
            <thead><tr><th>From</th><th>Relation</th><th>To</th><th>Seen as</th><th className="num">Lines</th><th className="num">Error/warn</th><th>Learned by</th></tr></thead>
            <tbody>
              {data.edges.map((e) => (
                <tr key={e.id} className="clickable" onClick={() => setEdge(e)}>
                  <td>{e.src}</td><td className="muted">{e.relation.replace("_", " ")}</td><td>{e.dst}</td>
                  <td>{e.status.map((s) => <span key={s} className="badge">{s}</span>)}</td>
                  <td className="num">{fmt(e.lines)}</td>
                  <td className="num" style={{ color: e.errors ? HEALTH_COLOR[e.health ?? "idle"] : undefined }}>{fmt(e.errors)}</td>
                  <td className="muted">{e.by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Add a link" className="w-4" hint="for a dependency no log line names">
          <AddEdge names={data.nodes.map((n) => n.id)} />
          {data.stats && data.stats.templates > 0 && (
            <p className="muted small" style={{ marginTop: 12 }}>
              Since the server started: {fmt(data.stats.templates)} templates labelled in {fmt(data.stats.calls)} LLM calls
              (~{fmt(Math.round(data.stats.prompt_chars / 4))} prompt tokens, {data.stats.seconds.toFixed(1)} s).
            </p>
          )}
        </Card>
      </div>
      {edge && <EdgeDrawer edge={edge} range={range} onClose={() => setEdge(null)} />}
    </>
  );
}

function AddEdge({ names }: { names: string[] }) {
  const [src, setSrc] = useState(""), [dst, setDst] = useState(""), [relation, setRelation] = useState("depends_on");
  const add = useAction(() => api("/api/graph/edges", { method: "POST", json: { src, dst, relation } }));
  return (
    <form className="graph-add" onSubmit={(ev) => { ev.preventDefault(); add.mutate(); }}>
      <label className="field">From<select value={src} onChange={(e) => setSrc(e.target.value)} required>
        <option value="">Pick a service</option>{names.map((n) => <option key={n}>{n}</option>)}</select></label>
      <label className="field">Relation<select value={relation} onChange={(e) => setRelation(e.target.value)}>
        {RELATIONS.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</select></label>
      <label className="field">To<select value={dst} onChange={(e) => setDst(e.target.value)} required>
        <option value="">Pick a service</option>{names.filter((n) => n !== src).map((n) => <option key={n}>{n}</option>)}</select></label>
      <button className="btn primary" disabled={!src || !dst || add.isPending}>Add link</button>
      {add.error && <p className="error-text">{add.error.message}</p>}
    </form>
  );
}

function EdgeDrawer({ edge, range, onClose }: { edge: GraphEdge; range: string; onClose: () => void }) {
  const remove = useAction(() => api(`/api/graph/edges/${encodeURIComponent(edge.id)}`, { method: "DELETE" }));
  return (
    <Drawer onClose={onClose}>
      <h3>{edge.src} <span className="muted">{edge.relation.replace("_", " ")}</span> {edge.dst}</h3>
      <p className="muted">
        Learned by {LEARNED[edge.by]}. In the range: {fmt(edge.lines)} lines, {fmt(edge.errors)} error/warn
        {edge.status.length > 0 && <> · seen as {edge.status.join(", ")}</>}.
      </p>
      <h4 className="graph-h">Evidence templates</h4>
      {edge.evidence?.length ? (
        <ul className="graph-list">
          {edge.evidence.map((t) => (
            <li key={t.id} className="mono">
              <Link to={`/logs?range=${range}&template_id=${t.id}&service=${encodeURIComponent(edge.src)}`}><span className="muted">#{t.id}</span> {t.text}</Link>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">None: this link was added by hand.</p>}
      <div className="row" style={{ marginTop: 16 }}>
        <button className="btn danger" disabled={remove.isPending} onClick={() => remove.mutate(undefined, { onSuccess: onClose })}>
          Remove this link
        </button>
        <span className="muted small">It stays removed even if a template shows it again.</span>
      </div>
    </Drawer>
  );
}
