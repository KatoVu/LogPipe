// Query: ask in plain English, build a question in three plain steps (what to show, which lines,
// one row per what), or write SQL. Saved queries sit in a menu; the schema list shows on the SQL tab.
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, useAction, usePoll, type QueryResult } from "../api";
import { AutoChart, RANGES, TimeRange } from "../charts";
import { AskPanel } from "../ask";
import { Card, Empty, PageHead, Pop, fmt } from "../ui";

interface Schema {
  engine: string; table: string; payload_access: string; measures: string[]; ops: string[];
  columns: { name: string; builder?: string; about: string }[]; fields: string[];
}
interface Saved { id: string; name: string; sql: string; spec?: Spec | null; saved_at: string }
interface Filter { field: string; op: string; value: string }
interface Spec { minutes: number; filters: Filter[]; measure: { fn: string; field?: string }; group_by: string[]; over_time: boolean; limit: number }

const BASE = ["host", "service", "source", "level", "status", "template_id", "message"];
type Tab = "ask" | "builder" | "sql";
const TABS: [Tab, string][] = [["ask", "Ask"], ["builder", "Builder"], ["sql", "SQL"]];
const EMPTY: Spec = { minutes: 60, filters: [], measure: { fn: "count" }, group_by: [], over_time: false, limit: 100 };
const START: Spec = { ...EMPTY, measure: { fn: "lines" } };  // what a new visitor wants first: the lines

const FIELD_LABEL: Record<string, string> = { template_id: "kind of line", message: "message text", lines: "lines" };
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

type Mode = "lines" | "count" | "trend" | "number";
const ICON: Record<Mode, string> = {
  lines: "M4 6h16M4 12h16M4 18h10",
  count: "M9 4L7 20M17 4l-2 16M4 9h16M3 15h16",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  number: "M18 5H6l6 7-6 7h12",
};
const MODES: [Mode, string, string][] = [
  ["lines", "Log lines", "The lines themselves"],
  ["count", "How many", "Count the matching lines"],
  ["trend", "Over time", "How that count changes"],
  ["number", "A number", "Average, max… of a field"],
];
const STATS: [string, string][] = [["avg", "Average"], ["max", "Maximum"], ["min", "Minimum"], ["sum", "Sum"], ["p95", "95th percentile"], ["count_distinct", "Different values of"]];
const SPLITS = ["service", "host", "level", "template_id"];

// a field that is probably a number (duration_ms, bytes, …) beats the alphabetically first one
const numericGuess = (fields: string[]) => {
  const f = fields.find((x) => /ms|duration|latency|took|time|bytes|size|count|number|status|code/i.test(x)) ?? fields[0];
  return f ? `@${f}` : "template_id";
};
const modeOf = (s: Spec): Mode => s.measure.fn === "lines" ? "lines" : s.measure.fn === "count" ? (s.over_time ? "trend" : "count") : "number";

function Glyph({ d }: { d: string }) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
}

/** Three plain questions: what to show, which lines, and (for numbers) how to break it down. */
function Builder({ spec, setSpec, schema }: { spec: Spec; setSpec: (s: Spec) => void; schema?: Schema }) {
  const fields = schema?.fields ?? [];
  const ops = schema?.ops ?? ["="];
  const mode = modeOf(spec);
  const range = RANGES.find(([, m]) => m === spec.minutes)?.[0] ?? "1h";
  const stats = STATS.filter(([fn]) => (schema?.measures ?? []).includes(fn) && (fn !== "p95" || schema?.engine === "clickhouse"));
  const setMode = (m: Mode) => setSpec({
    ...spec,
    over_time: m === "trend",
    measure: m === "lines" ? { fn: "lines" } : m === "number"
      ? { fn: modeOf(spec) === "number" ? spec.measure.fn : "avg", field: spec.measure.field ?? numericGuess(fields) }
      : { fn: "count" },
  });
  // the search box is the first "message contains" filter; the other filters are chips
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
  const chips = spec.filters.map((f, i) => [f, i] as const).filter(([, i]) => i !== si);
  const setFilter = (i: number, nf: Filter) => setSpec({ ...spec, filters: spec.filters.map((x, j) => (j === i ? nf : x)) });
  const toggleSplit = (g: string) => setSpec({ ...spec, group_by: spec.group_by.includes(g) ? spec.group_by.filter((x) => x !== g) : [...spec.group_by, g].slice(-2) });
  const extraSplits = spec.group_by.filter((g) => !SPLITS.includes(g));
  const changed = spec.filters.length > 0 || spec.group_by.length > 0 || spec.minutes !== START.minutes || mode !== "lines";

  return (
    <div className="qb">
      <div className="qb-try">
        <span className="muted small">Try</span>
        {PRESETS.map(([name, p]) => <button key={name} type="button" className="link-btn small" onClick={() => setSpec({ ...EMPTY, minutes: spec.minutes, ...p })}>{name}</button>)}
        {changed && <button type="button" className="link-btn small qb-reset" onClick={() => setSpec({ ...START })}>Start over</button>}
      </div>

      <section className="qb-step" aria-labelledby="qb-show">
        <h3 id="qb-show" className="qb-label">Show</h3>
        <div className="stack" style={{ gap: 10 }}>
          <div className="qb-modes" role="radiogroup" aria-labelledby="qb-show">
            {MODES.map(([m, title, hint]) => (
              <button key={m} type="button" role="radio" aria-checked={mode === m} className="qb-mode" onClick={() => setMode(m)}>
                <Glyph d={ICON[m]} />
                <span><strong>{title}</strong><span className="small">{hint}</span></span>
              </button>
            ))}
          </div>
          {mode === "number" && (
            <div className="row qb-number">
              <select aria-label="Which number" value={spec.measure.fn} onChange={(e) => setSpec({ ...spec, measure: { ...spec.measure, fn: e.target.value } })}>
                {stats.map(([fn, name]) => <option key={fn} value={fn}>{name}</option>)}
              </select>
              <span className="muted">of</span>
              <FieldSelect value={spec.measure.field ?? ""} onChange={(v) => setSpec({ ...spec, measure: { ...spec.measure, field: v } })} fields={fields} label="Field" />
              <label className="row qb-check"><input type="checkbox" checked={spec.over_time} onChange={(e) => setSpec({ ...spec, over_time: e.target.checked })} /> over time</label>
            </div>
          )}
        </div>
      </section>

      <section className="qb-step" aria-labelledby="qb-which">
        <h3 id="qb-which" className="qb-label">Which lines</h3>
        <div className="stack" style={{ gap: 10 }}>
          <div className="row qb-which">
            <Pop className="qb-time" label={<>{RANGE_WORDS[range][0].toUpperCase() + RANGE_WORDS[range].slice(1)}</>} title="Time range">{(close) => (
              <TimeRange value={range} onChange={(r) => { setSpec({ ...spec, minutes: RANGES.find(([k]) => k === r)![1] }); close(); }} />)}
            </Pop>
            <input type="search" className="qb-search" value={text} onChange={(e) => search(e.target.value)}
              placeholder="Containing text, e.g. timeout or order 1042" aria-label="Lines containing text" />
          </div>
          <div className="row qb-chips">
            {chips.map(([f, i]) => (
              <span key={i} className="chip-group">
                <Pop className="on" label={<>{label(f.field)} <span className="pill-op">{OP_LABEL[f.op] ?? f.op}</span> {f.value}</>} title="Change this filter">{(close) => (
                  <FilterEdit init={f} fields={fields} ops={ops} minutes={spec.minutes} onDone={(nf) => { setFilter(i, nf); close(); }} />)}
                </Pop>
                <button type="button" className="pill-x" aria-label={`Remove filter ${label(f.field)}`} onClick={() => setSpec({ ...spec, filters: spec.filters.filter((_, j) => j !== i) })}>×</button>
              </span>
            ))}
            <Pop className="add" label="+ Add filter" title="Only lines where…">{(close) => (
              <FilterEdit fields={fields} ops={ops} minutes={spec.minutes} onDone={(nf) => { setSpec({ ...spec, filters: [...spec.filters, nf] }); close(); }} />)}
            </Pop>
            {chips.length === 0 && <span className="muted small">All levels, services and hosts</span>}
          </div>
        </div>
      </section>

      {mode !== "lines" && (
        <section className="qb-step" aria-labelledby="qb-split">
          <h3 id="qb-split" className="qb-label">{mode === "trend" || spec.over_time ? "One line per" : "One row per"}</h3>
          <div className="row qb-chips">
            {[...SPLITS, ...extraSplits].map((g) => (
              <button key={g} type="button" className="qb-toggle" aria-pressed={spec.group_by.includes(g)} onClick={() => toggleSplit(g)}>{label(g)}</button>
            ))}
            <FieldSelect allowNone value="" onChange={(v) => v && toggleSplit(v)} fields={fields} label="Another field" />
            <span className="muted small">{spec.group_by.length ? "Up to two" : "None picked: one total"}</span>
          </div>
        </section>
      )}
    </div>
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

/** What a result is called: "51 lines", "4 services", "12 points". */
function titleOf(r: QueryResult, ran?: Spec) {
  const n = r.rows.length;
  if (ran?.measure.fn === "lines") return `${fmt(n)} line${n === 1 ? "" : "s"}`;
  if (ran?.over_time) return `${fmt(n)} point${n === 1 ? "" : "s"} in time`;
  const g = ran?.group_by[0];
  if (g) return `${fmt(n)} ${label(g)}${n === 1 ? "" : g === "template_id" ? "s" : g.endsWith("s") ? "es" : "s"}`;
  return `${fmt(n)} row${n === 1 ? "" : "s"}`;
}

export function ResultView({ r, ran, onDrill, busy, actions }: { r?: QueryResult; ran?: Spec; onDrill?: Drill; busy?: boolean; actions?: ReactNode }) {
  if (!r) return <Card title="Working…" actions={actions}><div className="muted small">Running the first query.</div></Card>;
  const cols = r.columns;
  const hint = busy ? "Updating…" : ran ? (ran.measure.fn === "lines" ? "newest first · click a service, host, level or field to narrow it down" : "") : `${r.engine} · ${r.table}`;
  if (ran?.measure.fn === "lines") return (
    <Card title={titleOf(r, ran)} hint={hint} actions={actions} className={busy ? "is-busy" : ""}>
      {r.rows.length === 0 ? <Empty title="No lines match">Widen the time range, change the text or remove a filter.</Empty> : <LinesView r={r} onDrill={onDrill} />}
    </Card>
  );
  // one number and nothing to break it down by: say it, don't tabulate it
  if (ran && r.rows.length === 1 && cols.length === 1) {
    const v = r.rows[0][0];
    const what = ran.measure.fn === "count" ? "matching lines" : `${STATS.find(([fn]) => fn === ran.measure.fn)?.[1].toLowerCase() ?? ran.measure.fn} of ${ran.measure.field}`;
    return (
      <Card title="Answer" hint={hint} actions={actions} className={busy ? "is-busy" : ""}>
        <p className="qb-answer"><strong className="num">{typeof v === "number" ? fmt(Math.round(v * 100) / 100) : v === null ? "no value" : String(v)}</strong> {what}
          <span className="muted"> · {RANGE_WORDS[RANGES.find(([, m]) => m === ran.minutes)?.[0] ?? "1h"]}</span></p>
        {ran.measure.fn === "count" && onDrill && <button type="button" className="btn small" onClick={() => onDrill([], true)}>See these lines</button>}
      </Card>
    );
  }
  const by = drillFields(cols, ran);
  const canDrill = !!onDrill && by.some(Boolean);
  const pick = (row: unknown[]) => by.flatMap((f, j) => (f && row[j] !== null ? [{ field: f, op: "=", value: String(row[j]) }] : []));
  const chart = <AutoChart columns={cols} rows={r.rows} />;
  const numeric = cols.map((_, j) => r.rows.some((row) => typeof row[j] === "number"));
  const head = (c: string, j: number) => !ran ? c : j === cols.length - 1 && ran.measure.fn !== "count"
    ? `${STATS.find(([fn]) => fn === ran.measure.fn)?.[1] ?? ran.measure.fn} of ${ran.measure.field}` : label(c);
  // builder results read rounded; raw SQL results stay exact
  const cell = (v: unknown) => typeof v === "number" ? (ran ? (Math.round(v * 100) / 100).toLocaleString() : String(v)) : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  return (
    <Card title={ran ? titleOf(r, ran) : `${r.rows.length} row${r.rows.length === 1 ? "" : "s"}`} hint={hint || (canDrill ? "hover a row to narrow it down or see its lines" : "")} actions={actions} className={busy ? "is-busy" : ""}>
      {r.rows.length === 0 ? <Empty title="Nothing matches">Widen the time range or remove a filter.</Empty> : (
        <div className="stack">
          {chart}
          <div className="table-wrap" style={{ maxHeight: 420, overflow: "auto" }}>
            <table>
              <thead><tr>{cols.map((c, j) => <th key={c} className={numeric[j] ? "right" : ""}>{head(c, j)}</th>)}{canDrill && <th><span className="sr-only">Narrow down</span></th>}</tr></thead>
              <tbody>
                {r.rows.map((row, i) => (
                  <tr key={i} className={canDrill ? "drill-row" : ""}>{row.map((v, j) => (
                    <td key={j} className={`mono small ${numeric[j] ? "right num" : ""}`}>{v === null ? <span className="muted">none</span> : cell(v)}</td>
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
  const [spec, setSpec] = useState<Spec>(START);
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
      <PageHead title="Query" sub={`Ask a question in plain words${noSql ? " or" : ","} build one step by step${noSql ? "" : ", or write SQL"}. Everything is read-only.`} />
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
            {tab === "builder" && <Builder spec={spec} setSpec={setSpec} schema={schema} />}
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
            {error && tab !== "ask" && <div className="callout bad" style={{ marginTop: 12 }}>{error.message}</div>}
            {result && currentSql && tab === "sql" && (
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
          {tab === "builder" && (result || build.isPending) && (
            <ResultView r={result} ran={build.variables} onDrill={drill} busy={build.isPending} actions={<>
              {currentSql && <Pop className="ghost" label={noSql ? "View query" : "View SQL"} title="The query this runs">{() => (
                <div className="stack qb-code" style={{ gap: 8 }}>
                  <pre className="code-block mono small">{currentSql}</pre>
                  {!noSql && <button type="button" className="btn small" onClick={() => { setSql(currentSql); setTab("sql"); }}>Edit as SQL</button>}
                </div>)}
              </Pop>}
              {currentSql && <Pop className="ghost" label="Save" title="Save this query">{(close) => (
                <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); save.mutate(undefined, { onSuccess: () => { setName(""); close(); } }); }}>
                  <div className="pop-title">Name it to find it under Saved</div>
                  <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Checkout errors" aria-label="Query name" />
                  {save.error && <span className="error-text small">{save.error.message}</span>}
                  <div className="row"><button className="btn small primary" disabled={!name.trim() || save.isPending}>Save</button></div>
                </form>)}
              </Pop>}
            </>} />
          )}
          {tab === "sql" && result && <ResultView r={result} />}
        </div>
      </div>
    </>
  );
}
