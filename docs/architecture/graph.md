# Component graph

`logpipe/graph/` keeps a graph of **which service depends on which**, learned from the log
templates themselves. It follows *Log2Graph* (Amanmadov & Abdullayev, *From Logs to Knowledge:
LLM-Powered Dynamic Knowledge Graphs for Real-Time Cloud Observability*, IJACSA 16(10), 2025):
an LLM turns telemetry into `(entity, relation, entity)` triples, a registry of known components
rejects what it made up, a graph database holds the result, and questions run over it.

Two changes make it fit logpipe:

- **Once per template, not once per line.** The paper parses every line with an LLM. Lines of
  one template only differ in their variables, so logpipe asks once per new template, in a batch
  per stream, and a line never reaches the LLM.
- **No failure samples, no retraining.** Unlike LogKG-style methods, nothing is learned from
  labelled incidents. The graph says how things connect; the anomaly signals (LogBERT, volume,
  rarity, new errors) still say what's wrong.

## When it changes

```mermaid
flowchart TD
    L[a line] --> M{matches a template?}
    M -- yes --> X[nothing: no LLM, no graph write]
    M -- no --> D[Drain drafts a new template]
    D --> A{the alias check: same event<br/>as an existing template?}
    A -- yes --> X2[alias it: graph unchanged]
    A -- no --> P["one LLM call per stream batch:<br/>which other component does it name?<br/>relation, status"]
    P --> R{target is a known service?}
    R -- no --> DROP[dropped]
    R -- yes --> E[edge + this template as its evidence]
```

- **First calibration and new sources** (the draft job): each draft carries its proposed links,
  the Review page shows them, and approving the batch publishes templates and links together. The
  first calibration's answers are cached with its drafts (`LOGPIPE_DRAFT_CACHE`, the showcase's
  `showcase-drafts-<llm>.json`), so a cached run asks nothing.
- **Templates the online learner adds** are labelled by the housekeeping loop as soon as the
  registry version moves.
- Without a chat model, a template's links are the known services it names (the **rule
  baseline**, also a benchmark variant).

A label is stored per `(service, template)` (control doc `graph_label`). The edges are derived
from all labels, the services that exist now and the edits made by hand (`graph_edit`), so a
service that appears later still gets the links an earlier label proposed, and an edge removed
by hand stays removed.

## What's stored, what's read

| | where | when |
|---|---|---|
| components and links (+ evidence templates, who learned it) | memory | derived again from control docs when labels, edits or traced links change |
| how a link looks now (lines, error/warn lines) | the log store | when someone looks: its evidence templates' counts |

A template holds both a 200 and a 502, so a link's errors are its templates' error/warn lines
counted apart, not the level of the template. Health: idle (no lines), ok, warn (< 20 % errors),
error.

The graph is tens to hundreds of links and is read only through fixed walks (upstream,
downstream, path in `graph/store.py`), so it lives in memory: no graph database, and a restart
rebuilds it from the control docs. The LLM never writes a query.

## From traces

Where services send OpenTelemetry traces (`POST /v1/traces`, OTLP JSON or protobuf, e.g. from a
Collector's `otlphttp` exporter), links are **measured** instead of inferred: a span whose parent
came from another service is one call, parent's service → its service; a client span to a
database, cache or broker (`db.system`, `messaging.system`) is a call to its `peer.service` or
`server.address`. Spans aren't stored: only per-minute calls, failed calls and mean time per pair
(in memory, two days; the pairs themselves are kept as control docs `graph_call`).

- A traced link (`by: traces`) replaces what the labels said about the same pair; their templates
  stay as its evidence. A link removed by hand stays removed.
- Its health is its failed calls, and Ask's tables and the RCA bundle show its calls against the
  window before.
- **RCA**: a traced link is *broken* when, after the anchor, its calls fell below half of what the
  window before made in the same time, a fifth or more failed, or they took twice as long. Its
  callee's candidates then go just above the caller's best (`LOGPIPE_RCA_CALL_WEIGHT`, 0.1: 10 %
  above; 0 turns it off): the caller's errors are the callee's symptoms. A service that went
  quiet logs nothing alarming, so only the calls show it.

Logs stay the source for what traces can't see: software you can't instrument (nginx, a mail
server, a database's own log) and anything that drops the trace header.

## Where it's used

- **Topology page**: callers on the left, dependencies on the right, links coloured by health.
  Click a component for what breaks if it fails; click a link for its evidence templates; add
  or remove a link by hand.
- **Ask, dependency questions**: "what breaks if postgres fails?", "what does shop-api depend
  on?", "how are nginx and postgres linked?" route to a `graph` intent (a regex fast path, else
  the LLM router) and answer as a table straight from the graph, without reading lines.
- **RCA**: the evidence round reads the graph around the anchor's service, and the ranking uses
  it. A candidate on a service the anchor's service depends on, if it's an error, a warning or
  new there, is multiplied by `1 + graph_weight / hops`: a cause sits below its symptoms. With no
  service at the anchor, the focus is the top error's service. The links show in the report
  (`G1…`), but **they're not in the LLM's prompt**: on RE3-OB with Gemini they cost ~2.2k tokens
  a case and didn't improve the answer, which sometimes followed a link to the wrong service.

| setting | default | |
|---|---|---|
| `LOGPIPE_GRAPH_ENABLED` | true | |
| `LOGPIPE_GRAPH_BATCH` | 40 | templates per labelling call |
| `LOGPIPE_RCA_GRAPH_WEIGHT` | 1.0 | 0 turns the ranking factor off |
| `LOGPIPE_RCA_GRAPH_DEPTH` | 2 | hops in the bundle's slice |
| `LOGPIPE_RCA_CALL_WEIGHT` | 0.1 | a broken traced link's callee goes this much above its caller; 0 turns it off |

## Limits

- A dependency no log line names is invisible: nginx shows its backend only if its log format
  has `$upstream_addr`. Add it by hand on the Topology page.
- Links end at known services only; an external API (a payment gateway) is dropped.
- Services whose logs never name a callee (Online Boutique) get no links from logs; send traces.
- Call counts are in memory: a restart loses the last two days' (the links stay).
- `graph.all()` reads the whole graph when the anchor has no service; fine for tens of services,
  capped at 60 links.

Results: [Benchmarks → component graph](../benchmarks/results.md#component-graph).
