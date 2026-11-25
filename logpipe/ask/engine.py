"""From a validated QuerySpec to an answer, in rounds. Each round is a list of reads
(:class:`~logpipe.ask.query.Query`) that are shown for approval before any of them runs; what
the next round needs depends on what the last one found (a root-cause search first looks for
when things broke, then reads around that moment).

The engine itself never calls the LLM except through ``ctx.adapter`` in :mod:`synth` (the final
explanation) and never reads anything that isn't one of the approved queries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..graph.service import health
from ..server.stores import Compiler, StoreReads
from ..settings import AskSettings, RcaSettings, get_settings
from .models import (
    ClarifyResult,
    GeneralResult,
    LinesResult,
    LogLine,
    Result,
    TableResult,
    TemplateCount,
    TemplateSeen,
)
from .query import Filters, LocalTool, LogTool, Query, QueryStatus, Tool
from .spec import QuerySpec, time_range


@dataclass
class Ctx:
    """Everything a plan reads from besides the approved queries: the server's catalog, models
    and settings, frozen at the moment the round is planned or run."""

    store: StoreReads
    templates: dict[int, str]  # id -> text, canonical ids only
    canonical: Callable[[int], int] = lambda t: t
    scorer_for: Callable[[str], Any] = lambda service: None  # service -> LogBERT scorer
    agents: Callable[[], list[dict]] = list
    alerts: Callable[[int], list[dict]] = lambda limit: []
    search_templates: Callable[[str, int], list[tuple[int, float]]] = lambda text, k: []
    neighbours: Callable[[int, int], list[tuple[int, float]]] = lambda tid, k: []  # LogBERT vectors
    shutdown: set[int] = field(default_factory=set)
    # group -> the services its streams cover
    groups: dict[str, list[str]] = field(default_factory=dict)
    # every service the store knows (follow-ups may name any)
    services: set[str] = field(default_factory=set)
    adapter: Any = None
    question: str = ""  # the person's words, for the LLM's final answer
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    window: int = 20  # LogBERT window length
    use_logbert: bool = True
    use_semantic: bool = True
    use_hints: bool = True
    graph: Any = None  # the component graph (logpipe.graph.store.GraphStore), None without one
    calls: Any = None  # calls measured from traces (logpipe.graph.traces.CallLog)
    use_graph: bool = True
    # follow-up kinds the LLM may ask for; None: all of them
    moves: frozenset[str] | None = None
    ask: AskSettings = field(default_factory=lambda: get_settings().ask)
    rca: RcaSettings = field(default_factory=lambda: get_settings().rca)

    def scope(self, spec: QuerySpec) -> dict:
        """The filters a root-cause read keeps to: the services of the groups asked about, else
        the hosts asked about. Services named alone don't narrow it: what broke them may be elsewhere."""
        if spec.groups:
            return {"services": sorted({s for g in spec.groups for s in self.groups.get(g, [])})}
        return {"hosts": list(spec.hosts)} if spec.hosts and not spec.services else {}

    def groups_of(self, services) -> dict[str, list[str]]:
        """The groups the given services belong to, with all their services."""
        named = {s for s in services if s}
        return {g: svcs for g, svcs in self.groups.items() if named & set(svcs)}


@dataclass
class Round:
    n: int
    purpose: str
    queries: list[Query]


@dataclass
class Outcome:
    """What ``advance`` returns: the next round, or the finished result."""

    round: Round | None = None
    result: Result | None = None
    state: dict = field(default_factory=dict)  # JSON, stored with the plan between rounds


def _q(
    *,
    round_no: int,
    query_no: int,
    tool: Tool,
    title: str,
    why: str,
    compiled: str,
    engine: str,
    **args,
) -> Query:
    return Query(
        id=f"r{round_no}q{query_no}",
        tool=tool,
        title=title,
        why=why,
        args=args,
        compiled=compiled,
        engine=engine,
    )


def local(
    *, round_no: int, query_no: int, tool: LocalTool, title: str, why: str, describe: str, **args
) -> Query:
    """A read that touches no log data in the store (agent heartbeats, the template index)."""
    return _q(
        round_no=round_no,
        query_no=query_no,
        tool=tool,
        title=title,
        why=why,
        compiled=describe,
        engine="local",
        **args,
    )


# How each log read turns into the store's query text: (compiler, filters, start, end, args, ctx).
type CompileRead = Callable[[Compiler, list[dict], datetime, datetime, dict, Ctx], str]


def _rows(c: Compiler, f: list[dict], start: datetime, end: datetime, args: dict, ctx: Ctx) -> str:
    return c.rows(
        f,
        start,
        end,
        args.get("limit", 200),
        args.get("newest_first", False),
        args.get("payload", False),
    )


def _template_counts(
    c: Compiler, f: list[dict], start: datetime, end: datetime, args: dict, ctx: Ctx
) -> str:
    return c.template_counts(f, start, end)


def _buckets(
    c: Compiler, f: list[dict], start: datetime, end: datetime, args: dict, ctx: Ctx
) -> str:
    return c.buckets(f, start, end, args["step"], args.get("by", "template_id"))


def _aggregate(
    c: Compiler, f: list[dict], start: datetime, end: datetime, args: dict, ctx: Ctx
) -> str:
    # filters come from the spec (it names groups), not from args
    spec = QuerySpec.model_validate(args["spec"])
    assert spec.aggregate is not None
    return c.build(
        {
            **span(start, end),
            "filters": Filters.of(spec, ctx.groups).builder(),
            "group_by": spec.aggregate.group_by,
            "measure": {"fn": spec.aggregate.fn, "field": spec.aggregate.field},
            "over_time": spec.aggregate.over_time,
            "limit": spec.limit,
        }
    )


LOG_READS: dict[LogTool, CompileRead] = {
    "rows": _rows,
    "template_counts": _template_counts,
    "buckets": _buckets,
    "aggregate": _aggregate,
}


def log_read(
    ctx: Ctx, *, round_no: int, query_no: int, tool: LogTool, title: str, why: str, **args
) -> Query:
    compile_read = LOG_READS.get(tool)
    if compile_read is None:
        raise ValueError(f"unknown read {tool}")
    start, end = (datetime.fromisoformat(args[k]) for k in ("start", "end"))
    f = Filters(**args.get("filters", {})).builder()
    c = ctx.store.compiler
    compiled = compile_read(c, f, start, end, args, ctx)
    return _q(
        round_no=round_no,
        query_no=query_no,
        tool=tool,
        title=title,
        why=why,
        compiled=compiled,
        engine=c.engine,
        **args,
    )


def span(start: datetime, end: datetime) -> dict:
    return {"start": start.astimezone(UTC).isoformat(), "end": end.astimezone(UTC).isoformat()}


def execute(q: Query, ctx: Ctx) -> list[dict]:
    """Run one approved read. Log reads run the compiled text exactly as it was shown."""
    if q.status != QueryStatus.APPROVED:
        raise PermissionError(f"query {q.id} is {q.status}, not approved")
    if q.engine != "local":
        return ctx.store.records(
            q.compiled, ctx.ask.row_cap if q.tool == "rows" else ctx.ask.agg_cap
        )
    if q.tool == "agents":
        return [
            {"host": a.get("hostname"), "last_seen": a.get("last_seen"), "online": a.get("online")}
            for a in ctx.agents()
        ]
    if q.tool == "alerts":
        start, end = q.args["start"], q.args["end"]
        return [a for a in ctx.alerts(500) if a["window_end"] >= start and a["window_start"] <= end]
    if q.tool == "graph":
        when = [
            datetime.fromisoformat(q.args[k]) if q.args.get(k) else None
            for k in ("start", "end", "at")
        ]
        return graph_read(ctx, q.args["op"], q.args["nodes"], int(q.args.get("depth", 2)), *when)
    raise ValueError(f"unknown local read {q.tool}")


GRAPH_OPS = {
    "downstream": "what {0} depends on",
    "upstream": "what depends on {0}",
    "around": "the components around {0}",
    "path": "how {0} and {1} are linked",
    "all": "every link",
}


def graph_read(
    ctx: Ctx,
    op: str,
    nodes: list[str],
    depth: int = 2,
    start: datetime | None = None,
    end: datetime | None = None,
    at: datetime | None = None,
) -> list[dict]:
    """Edges of the component graph: around a node (``downstream``, ``upstream``, ``around``, each
    with the hops of its far end from ``nodes[0]``), or the ``path`` between two. A traced edge
    carries its calls over [at, end) against [start, at) (``at`` unset: [start, end) against
    the window before)."""
    edges = _graph_edges(ctx, op, nodes, depth)
    if ctx.calls is not None and start and end:
        for e in edges:
            if e["by"] == "traces":
                e |= ctx.calls.stats(e["src"], e["dst"], start, end, at)
    return edges


def _graph_edges(ctx: Ctx, op: str, nodes: list[str], depth: int) -> list[dict]:
    g = ctx.graph
    if g is None or op not in GRAPH_OPS:
        return []
    if op == "path":
        return [e.to_dict() | {"hops": i + 1} for i, e in enumerate(g.path(nodes[0], nodes[1]))]
    if op == "all":
        # shortcut: the whole graph, fine for the tens of services it holds; cap it when it grows
        return [e.to_dict() | {"hops": 0} for e in g.edges()][:60]
    node = nodes[0]
    hops = {node: 0}
    if op in ("downstream", "around"):
        hops |= g.downstream(node, depth)
    if op in ("upstream", "around"):
        hops |= {n: h for n, h in g.upstream(node, depth).items() if n not in hops}
    down = set(g.downstream(node, depth)) | {node} if op != "upstream" else set()
    up = set(g.upstream(node, depth)) | {node} if op != "downstream" else set()
    out = []
    for e in g.edges():
        if (e.src in down and e.dst in down) or (e.src in up and e.dst in up):
            far = max(hops.get(e.src, 0), hops.get(e.dst, 0))
            out.append(e.to_dict() | {"hops": far})
    return sorted(out, key=lambda e: (e["hops"], e["id"]))


def graph_round(spec: QuerySpec, ctx: Ctx, start: datetime, end: datetime) -> Round:
    """A dependency question: the graph read, and the evidence templates' lines in the range."""
    op = {"impact": "upstream", "depends": "downstream", "path": "path"}[spec.graph_op or "impact"]
    nodes = spec.services[:2]
    return Round(
        1,
        "dependencies",
        [
            local(
                round_no=1,
                query_no=1,
                tool="graph",
                title="Component graph: " + GRAPH_OPS[op].format(*nodes, ""),
                why="the links the templates show, learned when each template was made",
                describe=f"graph.{op}({', '.join(map(repr, nodes))}, depth=6)",
                op=op,
                nodes=nodes,
                depth=6,
                **span(start, end),
            ),
            log_read(
                ctx,
                round_no=1,
                query_no=2,
                tool="template_counts",
                title="Lines per template in the range",
                why="how busy and how healthy each link is now (its templates' lines and errors)",
                filters={},
                **span(start, end),
            ),
            log_read(
                ctx,
                round_no=1,
                query_no=3,
                tool="template_counts",
                title="Error and warning lines per template in the range",
                why="one template can hold both a 200 and a 502: its error/warn lines counted apart",
                filters={"levels": ["error", "warn"]},
                **span(start, end),
            ),
        ],
    )


def graph_table(done: Round, outputs: dict, ctx: Ctx) -> TableResult:
    edges = outputs.get(done.queries[0].id) or []
    per: dict[tuple, list[int]] = {}
    for i in (0, 1):
        for r in outputs.get(done.queries[i + 1].id) or []:
            c = TemplateCount.model_validate(r)
            per.setdefault((c.service, ctx.canonical(c.template_id)), [0, 0])[i] += c.n
    rows = []
    for e in edges:
        lines = sum(per.get((e["src"], t), [0, 0])[0] for t in e["templates"])
        errors = sum(per.get((e["src"], t), [0, 0])[1] for t in e["templates"])
        calls, failed = e.get("calls"), e.get("call_errors")
        ok = health(calls, failed) if calls is not None else health(lines, errors)
        rows.append(
            [
                e["src"],
                e["relation"],
                e["dst"],
                e["hops"],
                lines,
                errors,
                calls,
                failed,
                ok,
                e["by"],
            ]
        )
    return TableResult(
        columns=[
            "from",
            "relation",
            "to",
            "hops",
            "lines",
            "errors",
            "calls",
            "failed calls",
            "health",
            "learned by",
        ],
        rows=rows,
        compiled=done.queries[0].compiled,
    )


def describe_templates(found: list[tuple[int, float]], templates: dict[int, str]) -> str:
    return "; ".join(f"#{t} {templates.get(t, '?')[:60]} ({s:.2f})" for t, s in found)


def lookup_templates(spec: QuerySpec, ctx: Ctx) -> tuple[list[int], str]:
    """Templates an event/semantic question is about, from the template catalog (which holds
    template text, not log lines): given ids, else the closest matches to its description."""
    if spec.template_ids:
        return [ctx.canonical(t) for t in spec.template_ids], "templates named in the question"
    text = spec.semantic_query or spec.anchor_event or " ".join(spec.keywords)
    found = [(t, s) for t, s in ctx.search_templates(text, 5) if s > 0] if ctx.use_semantic else []
    return [
        t for t, _ in found
    ], f"closest templates to {text!r}: {describe_templates(found, ctx.templates) or 'none'}"


def first_round(spec: QuerySpec, ctx: Ctx) -> Outcome:
    if spec.intent == "general":
        return Outcome(result=GeneralResult(answer=spec.answer or "(no answer)"))

    if spec.intent == "rca":
        from .rca import rca_first_round

        return rca_first_round(spec, ctx)

    start, end = time_range(spec, ctx.now)
    if spec.intent == "graph":
        if ctx.graph is None or not ctx.use_graph:
            return Outcome(
                result=ClarifyResult(message="There's no component graph yet to answer that from.")
            )
        return Outcome(graph_round(spec, ctx, start, end))
    if spec.intent == "summary":
        from .rca import summary_round

        return summary_round(spec, ctx, start, end)

    if spec.aggregate:
        return Outcome(
            Round(
                1,
                "count",
                [
                    log_read(
                        ctx,
                        round_no=1,
                        query_no=1,
                        tool="aggregate",
                        title="Count",
                        why=f"{spec.aggregate.fn} per {', '.join(spec.aggregate.group_by) or 'range'}",
                        spec=spec.model_dump(mode="json"),
                        **span(start, end),
                    )
                ],
            )
        )
    f = Filters.of(spec, ctx.groups)
    note = ""
    if spec.intent in ("event", "semantic"):
        tids, note = lookup_templates(spec, ctx)
        if not tids and not spec.keywords:
            return Outcome(
                result=ClarifyResult(
                    message="No template looks like that event. Name it with words the lines "
                    "contain (in quotes), or pick a template on the Templates page.",
                    note=note,
                )
            )
        if tids:
            f.template_ids = sorted(set(f.template_ids) | set(tids))
            f.keywords = []  # the templates are the match; the words described them
    queries = [
        log_read(
            ctx,
            round_no=1,
            query_no=1,
            tool="rows",
            title="Matching lines",
            why="the newest lines that match: " + f.describe(),
            filters=asdict(f),
            limit=spec.limit,
            newest_first=True,
            **span(start, end),
        )
    ]
    if spec.intent in ("event", "semantic"):
        queries.append(
            log_read(
                ctx,
                round_no=1,
                query_no=2,
                tool="template_counts",
                title="When each template occurred",
                why="first and last time per template, to say when the event happened",
                filters=asdict(f),
                **span(start, end),
            )
        )
    return Outcome(Round(1, "search", queries), state={"note": note})


def advance(
    spec: QuerySpec, state: dict, done: Round, outputs: dict[str, list[dict]], ctx: Ctx
) -> Outcome:
    """The next round, or the result, from what the approved reads of ``done`` returned."""
    if spec.intent in ("rca", "summary"):
        from .rca import rca_advance

        return rca_advance(spec, state, done, outputs, ctx)
    q = done.queries[0]
    if q.id not in outputs:
        return Outcome(
            result=ClarifyResult(
                message="The search was not approved, so there is nothing to show."
            )
        )
    rows = outputs[q.id]
    if spec.intent == "graph":
        return Outcome(result=graph_table(done, outputs, ctx))
    if q.tool == "aggregate":
        cols = list(rows[0]) if rows else []
        return Outcome(
            result=TableResult(
                columns=cols, rows=[[r[c] for c in cols] for r in rows], compiled=q.compiled
            )
        )
    lines = [LogLine.model_validate(r) for r in rows]
    for line in lines:
        line.template_id = ctx.canonical(int(line.template_id or 0))
    seen = []
    if len(done.queries) > 1 and done.queries[1].id in outputs:
        for c in (TemplateCount.model_validate(r) for r in outputs[done.queries[1].id]):
            tid = ctx.canonical(c.template_id)
            seen.append(
                TemplateSeen(
                    template_id=tid,
                    service=c.service,
                    count=c.n,
                    first=c.first,
                    last=c.last,
                    text=ctx.templates.get(tid, ""),
                )
            )
    return Outcome(result=LinesResult(lines=lines, templates=seen, note=state.get("note", "")))


def run_unattended(spec: QuerySpec, ctx: Ctx) -> dict:
    """Every round approved as planned: for the eval and tests only (the server never does this
    unless approval is switched off). The result, as JSON, carries the rounds that ran."""
    out, state, rounds = first_round(spec, ctx), {}, []
    while out.result is None and len(rounds) < ctx.ask.max_rounds:
        state |= out.state
        r = out.round
        assert r is not None
        outputs = {}
        for q in r.queries:
            q.status = QueryStatus.APPROVED
            outputs[q.id] = execute(q, ctx)
            q.status = QueryStatus.DONE
        rounds.append(r)
        out = advance(spec, state, r, outputs, ctx)
    result = out.result or ClarifyResult(message="too many rounds")
    return result.model_dump(mode="json") | {
        "rounds": [
            {"n": r.n, "purpose": r.purpose, "queries": [asdict(q) for q in r.queries]}
            for r in rounds
        ]
    }
