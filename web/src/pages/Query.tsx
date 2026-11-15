// Query: ask in plain English, build a question as a sentence of pills (measure, time, filters,
// split), or write SQL. Saved queries sit in a menu; the schema list shows on the SQL tab.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, useAction, usePoll, type QueryResult } from "../api";
import { AutoChart, RANGES, TimeRange } from "../charts";
import { AskPanel } from "../ask";
import { Card, Empty, PageHead, Pop, Segmented, fmt } from "../ui";

interface Schema {
  engine: string; table: string; payload_access: string; measures: string[]; ops: string[];
  columns: { name: string; builder?: string; about: string }[]; fields: string[];
}
interface Saved { id: string; name: string; sql: string; spec?: Spec | null; saved_at: string }
interface Filter { field: string; op: string; value: string }
interface Spec { minutes: number; filters: Filter[]; measure: { fn: string; field?: string }; group_by: string[]; over_time: boolean; limit: number }

const BASE = ["host", "service", "source", "level", "status", "template_id", "message"];
const MEASURE_LABEL: Record<string, string> = { lines: "The matching lines", count: "Count of lines", count_distinct: "Distinct values of", avg: "Average of", min: "Minimum of", max: "Maximum of", sum: "Sum of", p95: "95th percentile of" };
type Tab = "ask" | "builder" | "sql";
const TABS: [Tab, string][] = [["ask", "Ask"], ["builder", "Builder"], ["sql", "SQL"]];
const EMPTY: Spec = { minutes: 60, filters: [], measure: { fn: "count" }, group_by: [], over_time: false, limit: 100 };

const FIELD_LABEL: Record<string, string> = { template_id: "kind of line", message: "message text" };
const label = (f: string) => FIELD_LABEL[f] ?? f;
const OP_LABEL: Record<string, string> = { "=": "is", "!=": "is not", contains: "contains", "not contains": "doesn't contain", ">": "above", "<": "below", in: "is one of" };
const RANGE_WORDS: Record<string, string> = { "15m": "last 15 minutes", "1h": "last hour", "4h": "last 4 hours", "1d": "last day", "7d": "last 7 days" };
const QUICK = ["level", "service", "host"];
const PRESETS: [string, Partial<Spec>][] = [
  ["Latest errors", { measure: { fn: "lines" }, filters: [{ field: "level", op: "=", value: "error" }], limit: 50 }],
  ["Errors by service", { filters: [{ field: "level", op: "=", value: "error" }], group_by: ["service"] }],
  ["Volume over time", { over_time: true }],
  ["Errors over time", { filters: [{ field: "level", op: "=", value: "error" }], group_by: ["service"], over_time: true }],
  ["Busiest hosts", { group_by: ["host"] }],
  ["Most common lines", { group_by: ["template_id"], limit: 20 }],
];

function FieldSelect({ value, onChange, fields, label: aria, allowNone }: { value: string; onChange: (v: string) => void; fields: string[]; label: string; allowNone?: boolean }) {
  return (
    <select aria-label={aria} value={value} onChange={(e) => onChange(e.target.value)}>
      {allowNone && <option value="">Pick a field…</option>}
      <optgroup label="Log attributes">{BASE.map((f) => <option key={f} value={f}>{label(f)}</option>)}</optgroup>
      {fields.length > 0 && <optgroup label="Extracted fields">{fields.map((f) => <option key={f} value={`@${f}`}>@{f}</option>)}</optgroup>}
    </select>
  );
}

/** The values a field actually has in the range, most common first: what you can pick from. */
function useValues(field: string, minutes: number) {
  return useQuery({
    queryKey: ["values", field, minutes], enabled: !!field && field !== "message", staleTime: 60_000,
    queryFn: () => api<{ result: QueryResult }>("/api/query/build", { method: "POST",
      json: { ...EMPTY, minutes, group_by: [field], limit: 30 } }).then((r) => r.result.rows.map((row) => [String(row[0]), Number(row[1])] as [string, number])),
  });
}

/** Pick a field (quick ones first), then a value from the ones it has; the operator hides behind "is". */
function FilterEdit({ init, fields, ops, minutes, onDone }: { init?: Filter; fields: string[]; ops: string[]; minutes: number; onDone: (f: Filter) => void }) {
  const [f, setF] = useState<Filter>(init ?? { field: "", op: "=", value: "" });
  const values = useValues(f.field, minutes);
  if (!f.field) return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="pop-title">Filter on</div>
      <div className="row" style={{ gap: 6 }}>{QUICK.map((q) => <button key={q} type="button" className="btn small" onClick={() => setF({ ...f, field: q })}>{label(q)}</button>)}</div>
      {fields.length > 0 && <>
        <div className="pop-title">Extracted from your lines</div>
        <div className="row" style={{ gap: 6 }}>{fields.slice(0, 8).map((x) => <button key={x} type="button" className="btn small mono" onClick={() => setF({ ...f, field: `@${x}` })}>@{x}</button>)}</div>
      </>}
      <FieldSelect allowNone value="" onChange={(v) => setF({ ...f, field: v })} fields={fields} label="Other field" />
    </div>
  );
  const done = (value = f.value) => value.trim() && onDone({ ...f, value: value.trim() });
  const shown = (values.data ?? []).filter(([v]) => v && v !== "null" && v.toLowerCase().includes(f.value.toLowerCase()));
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 6 }}>
        <strong>{label(f.field)}</strong>
        <select aria-label="Operator" value={f.op} onChange={(e) => setF({ ...f, op: e.target.value })}>
          {ops.map((o) => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
        </select>
      </div>
      <input autoFocus aria-label="Value" value={f.value} placeholder={f.op === "in" ? "a, b, c" : "Type or pick below"} onChange={(e) => setF({ ...f, value: e.target.value })}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); done(); } }} />
      {values.isLoading ? <div className="muted small">Looking up values…</div> : shown.length > 0 && (
        <ul className="val-list" aria-label={`Values of ${label(f.field)}`}>
          {shown.slice(0, 12).map(([v, n]) => (
            <li key={v}><button type="button" onClick={() => done(v)}><span className="grow ellipsis">{v}</span><span className="muted num small">{fmt(n)}</span></button></li>
          ))}
        </ul>
      )}
      <div className="row"><button type="button" className="btn small primary" disabled={!f.value.trim()} onClick={() => done()}>{init ? "Update" : "Add filter"}</button></div>
    </div>
  );
}

function Builder({ spec, setSpec, schema, onRun, running }: { spec: Spec; setSpec: (s: Spec) => void; schema?: Schema; onRun: () => void; running: boolean }) {
  const fields = schema?.fields ?? [];
  const ops = schema?.ops ?? ["="];
  const range = RANGES.find(([, m]) => m === spec.minutes)?.[0] ?? "1h";
  const measures = (schema?.measures ?? ["count"]).filter((m) => m !== "p95" || schema?.engine === "clickhouse");
  const lines = spec.measure.fn === "lines";
  const plain = lines || spec.measure.fn === "count";  // measures without a field
  const what = lines ? "the matching lines" : spec.measure.fn === "count" ? "the number of lines" : `${MEASURE_LABEL[spec.measure.fn].toLowerCase()} ${spec.measure.field ?? ""}`;
  // the search box is the first "message contains" filter; the other filters are pills
  const si = spec.filters.findIndex((f) => f.field === "message" && f.op === "contains");
  const searched = si >= 0 ? spec.filters[si].value : "";
  const [text, setText] = useState(searched);
  // a preset, Clear or a loaded query changes it from outside; typing (trailing space and all) doesn't
  useEffect(() => { if (searched !== text.trim()) setText(searched); }, [searched]);  // eslint-disable-line react-hooks/exhaustive-deps
  const search = (v: string) => {
    setText(v);
    const rest = spec.filters.filter((_, j) => j !== si);
    setSpec({ ...spec, filters: v.trim() ? [{ field: "message", op: "contains", value: v.trim() }, ...rest] : rest });
  };
  const pills = spec.filters.map((f, i) => [f, i] as const).filter(([, i]) => i !== si);
  const preset = (p: Partial<Spec>) => setSpec({ ...EMPTY, minutes: spec.minutes, ...p });
  const setFilter = (i: number, nf: Filter) => setSpec({ ...spec, filters: spec.filters.map((x, j) => (j === i ? nf : x)) });
  return (
    <form className="builder" onSubmit={(e) => { e.preventDefault(); onRun(); }}>
      <div className="presets" role="group" aria-label="Start from">
        <span className="muted small">Start from</span>
        {PRESETS.map(([name, p]) => <button key={name} type="button" className="ask-example" onClick={() => preset(p)}>{name}</button>)}
      </div>
      <input type="search" className="builder-search" value={text} onChange={(e) => search(e.target.value)}
        placeholder="Search the message text, e.g. timeout or order 1042" aria-label="Search the message text" />
      <div className="sentence">
        <span>Show</span>
        <Pop label={what} title="What to measure">{(close) => (
          <div className="stack" style={{ gap: 6 }}>
            <div className="pop-title">Measure</div>
            <ul className="val-list">{measures.map((m) => (
              <li key={m}><button type="button" aria-pressed={spec.measure.fn === m} onClick={() => {
                const noField = m === "count" || m === "lines";
                setSpec({ ...spec, measure: { fn: m, field: noField ? undefined : spec.measure.field ?? (fields[0] ? `@${fields[0]}` : "host") } });
                if (noField) close();
              }}>{MEASURE_LABEL[m]}{m === "count" || m === "lines" ? "" : "…"}</button></li>))}</ul>
            {!plain && <FieldSelect value={spec.measure.field ?? ""} onChange={(v) => setSpec({ ...spec, measure: { ...spec.measure, field: v } })} fields={fields} label="Measure field" />}
          </div>)}
        </Pop>
        <span>from the</span>
        <Pop label={RANGE_WORDS[range]} title="Time range">{(close) => (
          <TimeRange value={range} onChange={(r) => { setSpec({ ...spec, minutes: RANGES.find(([k]) => k === r)![1] }); close(); }} />)}
        </Pop>
        {pills.length > 0 && <span>where</span>}
        {pills.map(([f, i], n) => (
          <span key={i} className="chip-group">
            {n > 0 && <span className="muted">and</span>}
            <Pop className="on" label={<>{label(f.field)} <span className="pill-op">{OP_LABEL[f.op] ?? f.op}</span> {f.value}</>} title="Edit filter">{(close) => (
              <FilterEdit init={f} fields={fields} ops={ops} minutes={spec.minutes} onDone={(nf) => { setFilter(i, nf); close(); }} />)}
            </Pop>
            <button type="button" className="pill-x" aria-label={`Remove filter ${label(f.field)}`} onClick={() => setSpec({ ...spec, filters: spec.filters.filter((_, j) => j !== i) })}>×</button>
          </span>
        ))}
        <Pop className="add" label={pills.length ? "+ and" : "+ filter"} title="Narrow it down">{(close) => (
          <FilterEdit fields={fields} ops={ops} minutes={spec.minutes} onDone={(nf) => { setSpec({ ...spec, filters: [...spec.filters, nf] }); close(); }} />)}
        </Pop>
        {!lines && spec.group_by.length > 0 && <span>per</span>}
        {!lines && spec.group_by.map((g, i) => (
          <span key={g} className="chip-group">
            {i > 0 && <span className="muted">and</span>}
            <span className="pill on static">{label(g)}</span>
            <button type="button" className="pill-x" aria-label={`Stop splitting by ${label(g)}`} onClick={() => setSpec({ ...spec, group_by: spec.group_by.filter((x) => x !== g) })}>×</button>
          </span>
        ))}
        {!lines && spec.group_by.length < 2 && (
          <Pop className="add" label={spec.group_by.length ? "+ and" : "+ split by"} title="Break it down">{(close) => {
            const pick = (v: string) => { if (v && !spec.group_by.includes(v)) setSpec({ ...spec, group_by: [...spec.group_by, v] }); close(); };
            return (
              <div className="stack" style={{ gap: 8 }}>
                <div className="pop-title">One row (or line) per</div>
                <div className="row" style={{ gap: 6 }}>{["service", "host", "level"].filter((q) => !spec.group_by.includes(q)).map((q) => <button key={q} type="button" className="btn small" onClick={() => pick(q)}>{label(q)}</button>)}</div>
                <FieldSelect allowNone value="" onChange={pick} fields={fields} label="Other field" />
              </div>);
          }}</Pop>
        )}
        {!lines && <Segmented label="Shape" value={spec.over_time ? "time" : "total"} onChange={(v) => setSpec({ ...spec, over_time: v === "time" })}
          options={[["total", "as a total"], ["time", "over time"]]} />}
      </div>
      <div className="row">
        <button className="btn primary" disabled={running}>{running ? <><span className="spin" aria-hidden="true" />Running…</> : "Run"}</button>
        <span className="muted small">{lines ? "Newest first. Click a service, host, level or field to narrow it down." : "Updates as you change it. Click a result row to drill in."}</span>
        {spec.filters.length + spec.group_by.length > 0 && <button type="button" className="btn small ghost" onClick={() => setSpec({ ...EMPTY, minutes: spec.minutes })}>Clear</button>}
      </div>
    </form>
  );
}

type Drill = (add: Filter[], lines: boolean) => void;

/** The field a cell value can filter on, or "": group-by columns, and a line's host/service/level. */
function drillFields(cols: string[], ran?: Spec): string[] {
  if (!ran) return cols.map(() => "");
  if (ran.measure.fn === "lines") return cols.map((c) => (["host", "service", "level"].includes(c) ? c : ""));
  const off = ran.over_time ? 1 : 0;
  return cols.map((_, j) => ran.group_by.slice(0, 2)[j - off] ?? "");
}

function payloadOf(v: unknown): Record<string, unknown> {
  if (typeof v === "string") { try { const o = JSON.parse(v); return o && typeof o === "object" ? o : {}; } catch { return {}; } }
  return v && typeof v === "object" ? v as Record<string, unknown> : {};
}

function LinesView({ r, onDrill }: { r: QueryResult; onDrill?: Drill }) {
  const at = (c: string) => r.columns.indexOf(c);
  const chip = (field: string, value: unknown, text = String(value)) => value === null || value === undefined || value === "" ? null : onDrill
    ? <button type="button" className="chip-btn" title={`Only ${label(field)} ${String(value)}`} onClick={() => onDrill([{ field, op: "=", value: String(value) }], true)}>{text}</button>
    : <span className="chip-btn">{text}</span>;
  return (
    <ul className="line-list">
      {r.rows.map((row, i) => {
        const level = String(row[at("level")] ?? "info");
        const fields = Object.entries(payloadOf(row[at("payload")])).filter(([k]) => k !== "timestamp" && k !== "device_id");
        return (
          <li key={i} className="line-item">
            <div className="line-meta">
              <span className={`lvl lvl-${level}`}>{onDrill ? <button type="button" onClick={() => onDrill([{ field: "level", op: "=", value: level }], true)} title={`Only ${level}`}>{level}</button> : level}</span>
              <span className="muted small num">{new Date(String(row[at("timestamp")])).toLocaleString()}</span>
              {chip("service", row[at("service")])}
              {chip("host", row[at("host")])}
            </div>
            <div className="mono small line-msg">{String(row[at("message")] ?? "")}</div>
            {fields.length > 0 && <div className="row line-fields">{fields.map(([k, v]) => <span key={k}>{chip(`@${k}`, v, `@${k} = ${String(v)}`)}</span>)}</div>}
          </li>
        );
      })}
    </ul>
  );
}

export function ResultView({ r, ran, onDrill }: { r: QueryResult; ran?: Spec; onDrill?: Drill }) {
  const cols = r.columns;
  if (ran?.measure.fn === "lines") return (
    <Card title={`${r.rows.length} line${r.rows.length === 1 ? "" : "s"}`} hint="newest first">
      {r.rows.length === 0 ? <Empty title="No lines">Widen the time range, change the search or remove a filter.</Empty> : <LinesView r={r} onDrill={onDrill} />}
    </Card>
  );
  const by = drillFields(cols, ran);
  const canDrill = !!onDrill && by.some(Boolean);
  const pick = (row: unknown[]) => by.flatMap((f, j) => (f && row[j] !== null ? [{ field: f, op: "=", value: String(row[j]) }] : []));
  const chart = <AutoChart columns={cols} rows={r.rows} />;
  return (
    <Card title={`${r.rows.length} row${r.rows.length === 1 ? "" : "s"}`} hint={`${r.engine} · ${r.table}`}>
      {r.rows.length === 0 ? <Empty title="No rows">Widen the time range or remove a filter.</Empty> : (
        <div className="stack">
          {chart}
          <div className="table-wrap" style={{ maxHeight: 420, overflow: "auto" }}>
            <table>
              <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}{canDrill && <th><span className="sr-only">Drill in</span></th>}</tr></thead>
              <tbody>
                {r.rows.map((row, i) => (
                  <tr key={i} className={canDrill ? "drill-row" : ""}>{row.map((v, j) => (
                    <td key={j} className={`mono small ${typeof v === "number" ? "right num" : ""}`}>{typeof v === "object" && v !== null ? JSON.stringify(v) : v === null ? <span className="muted">null</span> : String(v)}</td>
                  ))}
                  {canDrill && <td className="drill-actions">{pick(row).length > 0 && <>
                    <button type="button" className="btn small ghost" onClick={() => onDrill!(pick(row), false)}>Only this</button>
                    <button type="button" className="btn small" onClick={() => onDrill!(pick(row), true)}>See lines</button>
                  </>}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function Query() {
  const [params, setParams] = useSearchParams();
  const schema = usePoll<Schema>("/api/query/schema", 60000).data;
  // MongoDB runs the builder's pipelines but has no SQL, so the SQL tab (and SQL-only saved queries) hide
  const noSql = schema?.engine === "mongodb";
  const tabs = noSql ? TABS.filter(([t]) => t !== "sql") : TABS;
  const saved = (usePoll<{ queries: Saved[] }>("/api/queries", 30000).data?.queries ?? []).filter((q) => !noSql || q.spec);
  const initial = params.get("sql") ? "sql" : params.get("plan") ? "ask" : params.get("tab");
  const [picked, setTabState] = useState<Tab>(TABS.some(([t]) => t === initial) ? initial as Tab : "ask");
  const tab: Tab = noSql && picked === "sql" ? "builder" : picked;
  const setTab = (t: Tab) => { setTabState(t); setParams((p) => { p.set("tab", t); return p; }, { replace: true }); };
  const [spec, setSpec] = useState<Spec>(EMPTY);
  const [sql, setSql] = useState(params.get("sql") ?? "");
  const [name, setName] = useState("");
  const [naming, setNaming] = useState(false);
  const build = useAction((s: Spec) => api<{ sql: string; result: QueryResult }>("/api/query/build", { method: "POST", json: s }));
  const run = useAction((q: string) => api<QueryResult>("/api/query", { method: "POST", json: { sql: q } }));
  const save = useAction(() => api("/api/queries", { method: "POST", json: { name, sql: tab === "builder" ? build.data?.sql : sql, spec: tab === "builder" ? spec : null } }));
  const del = useAction((id: string) => api(`/api/queries/${id}`, { method: "DELETE" }));
  const result = tab === "builder" ? build.data?.result : tab === "sql" ? run.data : undefined;
  const error = tab === "builder" ? build.error : run.error;
  const currentSql = tab === "builder" ? build.data?.sql : sql;
  const key = JSON.stringify(spec);
  // a result row or a line's chip becomes a filter (replacing one on the same field); "See lines" lists them
  const drill: Drill = (add, showLines) => setSpec((s) => ({
    ...s,
    filters: [...s.filters.filter((f) => !add.some((a) => a.field === f.field)), ...add],
    group_by: s.group_by.filter((g) => !add.some((a) => a.field === g)),
    ...(showLines ? { measure: { fn: "lines" }, over_time: false } : {}),
  }));
  // the builder runs itself a moment after each change, so a new user sees an answer at once
  useEffect(() => {
    if (tab !== "builder" || spec.filters.some((f) => !f.value)) return;
    const t = setTimeout(() => build.mutate(spec), 350);
    return () => clearTimeout(t);
  }, [key, tab]);  // eslint-disable-line react-hooks/exhaustive-deps

  const insert = (text: string) => { setTab("sql"); setSql((s) => (s ? `${s}${s.endsWith(" ") || s.endsWith("\n") ? "" : " "}${text}` : `SELECT ${text}\nFROM ${schema?.table ?? "iot_logs"}`)); };
  const load = (q: Saved) => {
    if (q.spec) { setTab("builder"); setSpec(q.spec); } else { setTab("sql"); setSql(q.sql); run.mutate(q.sql); }
  };

  return (
    <>
      <PageHead title="Query" sub={`Ask a question in plain words${noSql ? " or" : ","} build one from a sentence${noSql ? "" : ", or write SQL"}. Everything is read-only.`} />
      <div className={tab === "sql" ? "query-layout" : ""}>
        {tab === "sql" && (
          <aside className="query-side">
            <Card title={<>Table <code>{schema?.table ?? "…"}</code></>} hint="Click to insert">
              <ul className="side-list schema">
                {schema?.columns.map((c) => (
                  <li key={c.name}><button type="button" title={c.about} onClick={() => insert(c.name === "level (computed)" ? "level" : c.name)}>
                    <code>{c.name}</code><span className="muted small">{c.about}</span></button></li>
                ))}
              </ul>
              {schema && schema.fields.length > 0 && (
                <>
                  <h3 style={{ marginTop: 10 }}>Extracted fields</h3>
                  <div className="row" style={{ gap: 4 }}>
                    {schema.fields.map((f) => <button key={f} type="button" className="chip-btn" title={schema.payload_access.replace("name", f)}
                      onClick={() => insert(schema.payload_access.replace("name", f))}>@{f}</button>)}
                  </div>
                </>
              )}
            </Card>
          </aside>
        )}

        <div className="stack">
          <Card>
            <div className="query-tabs">
              <div className="tabs" role="tablist">
                {tabs.map(([t, text]) => (
                  <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{text}</button>
                ))}
              </div>
              {tab !== "ask" && (
                <Pop className="ghost" label={<>Saved{saved.length ? ` (${saved.length})` : ""} ▾</>} title="Saved queries">{(close) => saved.length === 0
                  ? <div className="muted small" style={{ maxWidth: 220 }}>Run a query, then save it to find it here.</div>
                  : <ul className="val-list">{saved.map((q) => (
                    <li key={q.id} className="row" style={{ gap: 0, flexWrap: "nowrap" }}>
                      <button type="button" title={q.sql} onClick={() => { load(q); close(); }}><span className="grow ellipsis">{q.name}</span>
                        <span className="muted small">{q.spec ? "builder" : "SQL"}</span></button>
                      <button type="button" className="pill-x" aria-label={`Delete ${q.name}`} onClick={() => del.mutate(q.id)}>×</button>
                    </li>))}</ul>}
                </Pop>
              )}
            </div>
            {tab === "builder" && <Builder spec={spec} setSpec={setSpec} schema={schema} running={build.isPending} onRun={() => build.mutate(spec)} />}
            {tab === "ask" && <AskPanel initialPlan={params.get("plan") ?? undefined} />}
            {tab === "sql" && (
              <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); run.mutate(sql); }}>
                <textarea rows={8} className="mono" value={sql} spellCheck={false} aria-label="SQL" placeholder={`SELECT service, count() FROM ${schema?.table ?? "iot_logs"} GROUP BY service`}
                  onChange={(e) => setSql(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run.mutate(sql); }} />
                <div className="row">
                  <button className="btn primary" disabled={!sql.trim() || run.isPending}>{run.isPending ? "Running…" : "Run"}</button>
                  <span className="muted small">Ctrl/⌘ + Enter · at most 500 rows</span>
                </div>
              </form>
            )}
            {currentSql && tab === "builder" && (
              <details className="gen-sql"><summary className="small">{noSql ? "View query" : "View SQL"}</summary>
                <pre className="code-block mono small">{currentSql}</pre>
                {!noSql && <button type="button" className="btn small" onClick={() => { setSql(currentSql); setTab("sql"); }}>Edit as SQL</button>}
              </details>
            )}
            {error && tab !== "ask" && <div className="callout bad" style={{ marginTop: 12 }}>{error.message}</div>}
            {result && currentSql && (
              <div className="row" style={{ marginTop: 12 }}>
                {naming ? (
                  <form className="row" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined, { onSuccess: () => { setNaming(false); setName(""); } }); }}>
                    <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Query name" />
                    <button className="btn small primary" disabled={!name.trim() || save.isPending}>Save</button>
                    <button type="button" className="btn small ghost" onClick={() => setNaming(false)}>Cancel</button>
                  </form>
                ) : <button className="btn small" onClick={() => setNaming(true)}>Save query</button>}
                {save.error && <span className="error-text">{save.error.message}</span>}
              </div>
            )}
          </Card>
          {result && <ResultView r={result} ran={tab === "builder" ? build.variables : undefined} onDrill={tab === "builder" ? drill : undefined} />}
        </div>
      </div>
    </>
  );
}
