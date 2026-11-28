"""The server's component graph: labels in, edges out, health on read.

* A **label** (control doc ``graph_label``, id ``service:template``) is what the LLM (or the rule
  baseline) said one template of one service shows: the other components it talks to. Templates
  are labelled when they're made: in the draft job (reviewed with the templates, see
  ``server.core._job_draft``) or, for the ones the online learner adds, by ``tick`` as soon as
  the registry version moves. A template aliased to an existing one is never labelled: its
  canonical template already was.
* **Edges** are derived from all labels, the components known now and the person's edits
  (control doc ``graph_edit``: an edge added by hand, or one removed), and written to the store
  whole.
* **Calls** measured from traces (``graph.traces.CallLog``) are links too (control doc
  ``graph_call``, ``by`` traces): where traces cover a pair they win over what the labels said,
  whose templates stay as its evidence.
* **Health** isn't stored: a traced link's calls and failed calls, else its evidence
  templates' lines and errors, over a range, read when someone looks.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from .label import RELATIONS, prompt_for, propose, resolve
from .store import Edge, MemoryGraph
from .traces import CallLog

LABELLED_WITHIN = timedelta(days=1)
ERRORS = [
    {"field": "level", "op": "in", "value": "error,warn"}
]  # templates seen this recently get labelled by tick


def health(lines: int, errors: int) -> str:
    if not lines:
        return "idle"
    share = errors / lines
    return "ok" if share == 0 else "warn" if share < 0.2 else "error"


class GraphService:
    def __init__(self, control, store: MemoryGraph, batch: int = 40):
        self.control, self.store, self.batch = control, store, batch
        self._version: int | None = None
        self._lock = threading.Lock()
        self.calls = CallLog()
        # labelling cost, for the Topology page and the benchmark
        self.stats = {"calls": 0, "templates": 0, "prompt_chars": 0, "seconds": 0.0}

    @staticmethod
    def known(server) -> set[str]:
        """Components an edge may end at: every service a source reports."""
        return {s["service"] for s in server.sources.values() if s.get("service")}

    def propose(self, adapter, service: str, items: list[tuple], known: Iterable[str]) -> dict:
        """Proposed edges for ``items`` ((key, template text, example) each) of one service, one
        LLM call per batch: key -> (edges, by). Nothing is kept: a draft's edges wait for review."""
        out: dict = {}
        known = set(known)
        for i in range(0, len(items), self.batch):
            chunk = items[i : i + self.batch]
            templates = [(t, ex) for _, t, ex in chunk]
            started = time.perf_counter()
            per, by = propose(adapter, service, templates, known)
            if by == "llm":
                self.stats["calls"] += 1
                self.stats["prompt_chars"] += len(prompt_for(service, templates, known))
            self.stats["seconds"] += time.perf_counter() - started
            self.stats["templates"] += len(chunk)
            for (key, _, _), edges in zip(chunk, per):
                out[key] = (edges, by)
        return out

    def save_label(self, service: str, tid: int, edges: list[dict], by: str) -> None:
        self.control.put(
            "graph_label",
            f"{service}:{tid}",
            {
                "id": f"{service}:{tid}",
                "service": service,
                "template_id": tid,
                "edges": edges,
                "by": by,
                "at": datetime.now(UTC).isoformat(),
            },
        )

    def edit(self, src: str, dst: str, relation: str, removed: bool) -> dict:
        if relation not in RELATIONS:
            raise ValueError(f"relation must be one of {', '.join(RELATIONS)}")
        if not src or not dst or src == dst:
            raise ValueError("an edge links two different components")
        key = f"{src}>{dst}>{relation}"
        return self.control.put(
            "graph_edit",
            key,
            {"id": key, "src": src, "dst": dst, "relation": relation, "removed": removed},
        )

    def derive(self, known: set[str], canonical: Callable[[int], int]) -> list[Edge]:
        merged: dict[str, dict] = {}
        for lab in self.control.all("graph_label"):
            src = lab["service"]
            if src not in known:
                continue
            for e in lab["edges"]:
                dst = resolve(e["target"], known)
                if dst is None or dst == src:
                    continue  # the registry check: only components that exist
                key = f"{src}>{dst}>{e['relation']}"
                m = merged.setdefault(
                    key,
                    {
                        "src": src,
                        "dst": dst,
                        "relation": e["relation"],
                        "t": set(),
                        "s": set(),
                        "by": lab["by"],
                    },
                )
                m["t"].add(canonical(int(lab["template_id"])))
                if e.get("status"):
                    m["s"].add(e["status"])
                if lab["by"] == "llm":
                    m["by"] = "llm"
        for c in self.control.all("graph_call"):
            src, dst = c["src"], c["dst"]
            # measured beats inferred: the labels' links between the pair fold into this one
            folded = [k for k, m in merged.items() if m["src"] == src and m["dst"] == dst]
            m = {"src": src, "dst": dst, "relation": "depends_on", "t": set(), "s": set()}
            for k in folded:
                old = merged.pop(k)
                m["t"] |= old["t"]
                m["s"] |= old["s"]
            merged[f"{src}>{dst}>depends_on"] = m | {"by": "traces"}
        for ed in self.control.all("graph_edit"):
            if ed["removed"]:
                merged.pop(ed["id"], None)
            else:
                m = merged.setdefault(
                    ed["id"],
                    {
                        "src": ed["src"],
                        "dst": ed["dst"],
                        "relation": ed["relation"],
                        "t": set(),
                        "s": set(),
                    },
                )
                m["by"] = "manual"
        return [
            Edge(
                m["src"],
                m["dst"],
                m["relation"],
                tuple(sorted(m["t"])),
                m["by"],
                tuple(sorted(m["s"])),
            )
            for m in merged.values()
        ]

    def rebuild(self, server) -> list[Edge]:
        edges = self.derive(self.known(server), server.registry.current.canonical_id)
        self.store.replace(edges)
        return edges

    def label_new(self, server, adapter, end: datetime | None = None, force: bool = False) -> int:
        """Label the (service, template) pairs logged in the day before ``end`` (default: now)
        that have none yet (every one with ``force``: the benchmark compares labellers on the
        same templates)."""
        now = end or datetime.now(UTC)
        snap = server.registry.current
        done = set() if force else {d["id"] for d in self.control.all("graph_label")}
        reads = server.reads
        rows = reads.records(reads.compiler.template_counts([], now - LABELLED_WITHIN, now), 20_000)
        todo: dict[str, dict[int, str]] = defaultdict(dict)
        for r in rows:
            tid, service = int(r.get("template_id") or 0), r.get("service") or ""
            entry = snap.templates.get(tid)
            if not service or entry is None or entry.alias_of is not None:
                continue
            if f"{service}:{tid}" not in done:
                todo[service].setdefault(tid, str(r.get("example") or ""))
        known, n = self.known(server), 0
        for service, found in sorted(todo.items()):
            items = [(t, " ".join(snap.templates[t].tokens), ex) for t, ex in sorted(found.items())]
            for tid, (edges, by) in self.propose(adapter, service, items, known).items():
                self.save_label(service, tid, edges, by)
            n += len(items)
        return n

    def save_calls(self) -> bool:
        """Write the caller -> callee pairs traces showed for the first time; whether any."""
        new = self.calls.take_new()
        for src, dst in sorted(new):
            key = f"{src}>{dst}"
            self.control.put(
                "graph_call",
                key,
                {"id": key, "src": src, "dst": dst, "at": datetime.now(UTC).isoformat()},
            )
        return bool(new)

    def tick(self, server) -> None:
        """Called by the server's housekeeping: label what's new since the registry last moved,
        and add the pairs traces showed."""
        if self.save_calls():
            with self._lock:
                self.rebuild(server)
        version = server.registry.current.version
        if version == self._version or not server.settings.calibrated:
            return
        with self._lock:
            self.label_new(server, server.adapter)
            self.rebuild(server)
            self._version = version

    def view(self, server, start: datetime, end: datetime) -> dict:
        """Nodes and edges with their health over [start, end)."""
        reads = server.reads
        canonical = server.registry.current.canonical_id
        per: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
        svc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        # every line, then the error/warn ones: one template holds both a 200 and a 502
        for i, filters in enumerate(([], ERRORS)):
            for r in reads.records(reads.compiler.template_counts(filters, start, end), 20_000):
                n = int(r.get("n") or 0)
                key = (r.get("service") or "", canonical(int(r.get("template_id") or 0)))
                per[key][i] += n
                svc[key[0]][i] += n
        edges = []
        for e in self.store.edges():
            lines = sum(per[(e.src, t)][0] for t in e.templates)
            errors = sum(per[(e.src, t)][1] for t in e.templates)
            d = e.to_dict() | {"lines": lines, "errors": errors}
            if e.by == "traces":
                c = self.calls.stats(e.src, e.dst, start, end)
                d |= c | {"health": health(c["calls"], c["call_errors"])}
            else:
                d["health"] = health(lines, errors)
            edges.append(d)
        names = self.known(server) | {e["src"] for e in edges} | {e["dst"] for e in edges}
        nodes = [
            {"id": n, "lines": svc[n][0], "errors": svc[n][1], "health": health(*svc[n])}
            for n in sorted(names)
        ]
        return {"nodes": nodes, "edges": edges, "stats": dict(self.stats)}
