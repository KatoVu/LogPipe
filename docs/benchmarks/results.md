# Results

Latest full run of `make eval` (`artifacts/eval/REPORT.md`), offline LLM unless stated. The
nightly gate's committed baseline (`eval/baseline.json`, reduced settings) is at the end.

## Parsing: Loghub-2k

GA = grouping accuracy, PA = parsing accuracy (DeepParse's metrics). *logpipe* uses offline
masks, Drain depth 5 and similarity 0.4; *+tuned* grid-searches depth and similarity per system
on ground truth. Holdout coverage needs no labels: the share of the newest 20 % of lines a
registry mined from the older 80 % resolves without going PENDING.

| system | GA DeepParse | GA logpipe | GA +tuned | PA DeepParse | PA logpipe | PA +tuned | holdout coverage |
|---|---|---|---|---|---|---|---|
| Apache | 1.0 | 1.0 | 1.0 | 0.694 | 1.0 | 0.694 | 1.0 |
| BGL | 0.833 | 0.955 | 0.974 | 0.817 | 0.548 | 0.914 | 0.49 |
| HDFS | 0.998 | 0.158 | 0.998 | 0.223 | 0.389 | 0.223 | 0.995 |
| HPC | 0.882 | 0.888 | 0.906 | 0.882 | 0.896 | 0.882 | 0.63 |
| Hadoop | 0.685 | 0.987 | 0.961 | 0.105 | 0.394 | 0.378 | 1.0 |
| HealthApp | 0.903 | 1.0 | 0.903 | 0.766 | 0.982 | 0.764 | 0.948 |
| Linux | 0.172 | 0.178 | 0.693 | 0.156 | 0.161 | 0.219 | 0.71 |
| Mac | 0.788 | 0.799 | 0.802 | 0.494 | 0.476 | 0.450 | 0.898 |
| OpenSSH | 0.489 | 0.489 | 0.940 | 0.481 | 0.253 | 0.721 | 0.998 |
| OpenStack | 0.481 | 0.256 | 0.502 | 0.244 | 0.224 | 0.244 | 1.0 |
| Proxifier | 0.026 | 0.001 | 0.001 | 0.0 | 0.0 | 0.0 | 0.935 |
| Spark | 0.970 | 1.0 | 1.0 | 0.682 | 0.869 | 0.682 | 1.0 |
| Thunderbird | 0.637 | 0.674 | 0.677 | 0.432 | 0.338 | 0.338 | 0.998 |
| Zookeeper | 0.988 | 0.989 | 0.989 | 0.669 | 0.671 | 0.670 | 0.993 |
| **macro** | **0.703** | **0.669** | **0.810** | **0.474** | **0.514** | **0.513** | **0.900** |

**Reading it.** Default parameters lose to DeepParse on HDFS and OpenStack; tuning recovers both
and wins on Linux and OpenSSH by wide margins. Proxifier fails for every configuration here. The
tuned parameters are per system, which is what `calibrate --tune` does on a deployment's own
labelled sample.

## Alias decisions

28 hand-labelled pairs of templates: same event (should link) or opposite states (must not).

| decider | precision | recall | false merges | missed merges |
|---|---|---|---|---|
| **rules_offline** (shipping default) | **1.0** | 0.6 | **0** | 4 |
| rules + an always-yes LLM | 0.833 | 1.0 | 2 | 0 |
| token similarity only | 0.5 | 1.0 | 10 | 0 |

A missed merge leaves two ids that a person can link later; a false merge erases the
distinction anomaly detection depends on. Hence the asymmetric default.

## IoT fleet, end to end

Synthetic fleet (`logpipe gen-iot`) with a firmware rollout that changes formats mid-test and
injected anomalies of five kinds. 54,618 test events.

| variant | P | R | F1 | FP reasons | PENDING at end | resolve lag p50 | LLM calls |
|---|---|---|---|---|---|---|---|
| **full** | 0.532 | 0.583 | **0.556** | sequence 234, novelty 479 | 0 | 258 s | 13 |
| no fleet suppression | 0.234 | 0.609 | 0.338 | sequence 133, novelty 2597 | 0 | 258 s | 12 |
| mask unknowns (original LogBERT) | 0.212 | 0.349 | 0.264 | sequence 1728 | 0 | 258 s | 13 |
| no enrichment | 0.218 | 0.614 | 0.321 | sequence 234, novelty 2879 | 4975 | — | 0 |

Recall by anomaly kind (full): new event **1.0**, reorder 0.74, missing 0.65, stuck 0.53,
burst 0.26. Parse latency: p50 31.8 µs, p90 40.8, p99 68.1, max 581 µs; 416 lines/s end to end
including LogBERT training cycles and learning.

**Reading it.** Each component pays for itself: without online learning, 4,975 lines stay
PENDING and every firmware-2 event is "novel"; without fleet suppression, the rollout floods
novelty alerts; masking unknowns (the original blueprint) loses most real anomalies. Bursts are
the weak spot, plausibly because a burst of *familiar* events changes rates more than order.

## LogBERT: HDFS and BGL

Thresholds tuned on a validation split. *Oracle* is LogBERT's original protocol (tuned on the
test set), shown for comparison.

=== "BGL"

    1,146 train / 1,425 normal + 578 abnormal test sequences; vocab 148; 13 epochs in 666 s.
    Unknown templates: 0.6 % of normal test lines, 69 % of abnormal ones.

    | unknowns | novelty | P | R | F1 (val-tuned) | F1 (oracle) |
    |---|---|---|---|---|---|
    | unk | on | 0.935 | 0.971 | **0.953** | 0.954 |
    | unk | off | 0.869 | 0.869 | 0.869 | 0.878 |
    | mask | on | 0.935 | 0.971 | 0.953 | 0.954 |
    | mask | off | 0.691 | 0.282 | 0.401 | 0.401 |
    | drop | off | 0.678 | 0.270 | 0.386 | 0.386 |

=== "HDFS"

    8,000 train / 10,000 normal + 1,001 abnormal test sessions; vocab 20; 30 epochs in 189 s.

    | unknowns | novelty | P | R | F1 (val-tuned) | F1 (oracle) |
    |---|---|---|---|---|---|
    | unk | on | 0.970 | 0.683 | **0.802** | 0.802 |
    | unk | off | 0.963 | 0.514 | 0.670 | 0.670 |
    | mask | off | 0.963 | 0.513 | 0.669 | 0.669 |
    | drop | off | 0.957 | 0.443 | 0.605 | 0.605 |

**Reading it.** On BGL most anomalies contain templates never seen in training, which is
exactly what masking hides (F1 0.40) and the novelty signal catches (0.95).

## RCA on injected faults (synthetic)

`logpipe eval rca`: a web shop's normal traffic for 2 h, then 15 fault modes
(SIGTERM, OOM, segfault, disk full, DB port blocked, pool exhausted, gateway timeout, DNS, TLS
expired, config reload, dependency 503, GC thrash, lock contention, clock skew, API hang) plus
a bad host, a cross-service trace hop and 2 no-fault controls, 19 in all. Each asks an on-call
question.

| variant | evidence recall | answer correct |
|---|---|---|
| naive (last 200 lines) | 0.733 | — |
| **full, rules only** | **1.0** | **0.947** (18/19) |
| full + Gemini (subset re-run) | 1.0 | `trace_hop` and `bad_host` both correct |

The one rules-only miss is `trace_hop` (the cause is a Postgres line linked by request id); the
LLM's typed `trace` move fixes it. **Caveat:** the heuristics were tuned on these scenarios, so
this is a fit, not a held-out score. That's why the RCAEval results below matter.

## RCA on RCAEval (real testbeds)

[Full page →](rcaeval.md). RE3-OB, 30 code-level faults on Online Boutique:

| setup | AC@1 | AC@3 | Avg@5 |
|---|---|---|---|
| rules only | 0.40 | 0.40 | 0.56 |
| rules + Gemini (web) | 0.70 | 0.77 | 0.81 |
| Claude Code over MCP, with `ask` | 0.87 | 0.87 | 0.88 |
| Claude Code over MCP, raw tools | 0.90 | 0.90 | 0.90 |
| rules + graph from traces (26 Nov) | 0.77 | 0.80 | 0.85 |
| rules + graph from traces + Gemini (26 Nov) | 0.93 | 1.00 | 0.99 |

The first four rows were run on 12 Nov, before `settle()` and later RCA changes; compare the
26 Nov rows with the [component graph](#component-graph) runs of the same day, not with them.

## Component graph

`logpipe eval graph`, run 2026-11-25 ([how it works](../architecture/graph.md)). The shop with
`--topology` traffic (nginx logs its upstream, the API its connection pool), three faults
injected, 16 templates. Truth: nginx → shop-api → postgres.

**Links learned**

| labeller | precision | recall | LLM calls | prompt tokens | labelling time |
|---|---|---|---|---|---|
| rules (a template names a known service) | 1.0 | 1.0 | 0 | 0 | < 1 ms |
| Gemini (web chat, one call per stream) | 1.0 | 1.0 | 3 | ~1.9k | 54 s |

**Dependency questions**: "what breaks if X fails?" and "what does X depend on?" for each of
the 3 services (6 questions), set F1 against the true answer:

| | F1 |
|---|---|
| Ask without the graph | 0.33 (it can't answer; right only where the answer is "nothing") |
| Gemini alone, reading the last 200 lines | 0.44 |
| **Ask with the graph** (no LLM at question time) | **1.0** |

**RCA, synthetic** (`logpipe eval rca --topology`, 19 faults, rules only): graph on, off, or in
the bundle without the ranking factor all score evidence recall 1.0 and answer 0.947 (18/19), so
no regression, and no room: the miss (`trace_hop`) is an info-level Postgres line. With the
graph it moves from unranked to second lead, behind the shop-api error it explains.

**Cost**

| | |
|---|---|
| graph query (upstream + downstream, depth 6) | p50 0.003 ms in memory (Neo4j, since dropped, was 3–4 ms) |
| RCA prompt, graph slice | ~120 tokens (2 links), ~480 (15 links, Online Boutique) |
| per line at ingest | nothing: labels are per template |

**RCA on RE3-OB** (`logpipe eval rcaeval`, 30 cases, rules only):

| graph | links learned (of 11) | AC@1 | AC@3 | Avg@5 |
|---|---|---|---|---|
| off | — | 0.40 | 0.40 | 0.56 |
| learned from the logs (rules) | 0 | 0.40 | 0.40 | 0.56 |
| learned from the logs (Gemini, 1 case: 6 calls, ~2.2k tokens, 70 s) | 0 | — | — | — |
| true links entered by hand (`--true-links`), factor on | 11 | 0.40 | 0.40 | 0.56 |
| true links, factor off (in the bundle only) | 11 | 0.40 | 0.40 | 0.56 |

Online Boutique's services never name the one they call ("payment went through", "request
started"), so neither labeller finds a link; Gemini proposes none rather than guessing. Its
topology is in the traces, not the logs. With the true links entered by hand, the factor
reorders lower ranks in 15 cases but never changes the top answer: the root cause is
`emailservice` in 15 cases, and its lines during these faults are its usual info lines, not an
error, a warning or a new template, so there's nothing on it to lift.

**Links from traces** (`--traces`: the case's `traces.csv` fed through the same code as
`POST /v1/traces`). Each span whose parent belongs to another service is one call. RCA reads the
calls on each link in the window against the 10 minutes before. A link whose calls fell by
half, ≥ 20 % failed or doubled in time is broken, and its callee goes above its caller
(`LOGPIPE_RCA_CALL_WEIGHT`, 0.1).

A failing caller (≥ 20 % of the calls *to* it fail) that stops calling something altogether
never reached it, so that link lifts nothing.

| graph (rules only, 30 cases) | AC@1 | AC@3 | AC@5 | Avg@5 |
|---|---|---|---|---|
| off | 0.40 | 0.40 | 1.00 | 0.56 |
| from traces, factor off (in the bundle only) | 0.40 | 0.40 | 1.00 | 0.56 |
| **from traces, factor on** | **0.77** | **0.80** | 1.00 | **0.85** |

| fault | right first, factor off → on |
|---|---|
| emailservice F1, F2, F5 | 0/9 → 9/9: checkout's calls to email fall from ~160 to ~4 |
| emailservice F3, F4 | 0/6 → 0/6: calls keep flowing at the usual rate and time, nothing to see |
| currencyservice F1 | 0/3 → 2/3: frontend's calls to checkout all fail; checkout still calls currency (385 → 100) but never reaches payment and email (→ 0) |
| adservice, cartservice | 12/12 → 12/12 |

**The same with Gemini** (web chat, `--llm llm:web:gemini --label-with-rules`, 30 cases; per
case, from the recorded prompts: tokens ≈ characters / 4):

| graph | AC@1 | AC@3 | AC@5 | Avg@5 | tokens in / out | LLM time |
|---|---|---|---|---|---|---|
| off | 0.90 | 0.93 | 1.00 | 0.95 | 13.1k / 4.3k | 69 s |
| from traces, also in the prompt (first try) | 0.80 | 0.97 | 1.00 | 0.93 | 15.6k / 4.3k | 82 s |
| **from traces, ranking only** (now) | **0.93** | **1.00** | 1.00 | **0.99** | 13.0k / 4.6k | 38 s |

In the prompt, the graph slice cost ~2.2k tokens a case and Gemini sometimes followed a link to
the wrong service (`redis` above `cartservice` in 0007, from a link learned from the logs). Now
the graph only reorders the evidence, as without an LLM, and the prompt is the no-graph one's
size. The 3-case gain over no graph is within run-to-run noise: web Gemini picks its own model,
and an earlier no-graph run (12 Nov, older code, above) scored 0.70.

**Dependency questions on Online Boutique** (`--ob-questions re3-ob-0001`; 20 questions on
the 10 services the traces and the task share; truth is the 11 known links):

| | links found (precision / recall) | question F1 |
|---|---|---|
| Ask without the graph | — | 0.40 |
| Ask with the graph from logs | 0 links | 0.40 |
| **Ask with the graph from traces** | 1.0 / 0.64 | **0.83** |
| Gemini alone, last 200 lines | — | 0.86 |

The traces miss cart, shipping and ad, which send no spans of their own, so questions about
them come back short. Gemini alone likely knows Online Boutique: it's a public demo, so 0.86
is an upper bound, not a fair baseline.

**Caveats.** The shop is small and names its backends plainly, so the rule baseline is already
perfect there. On RE3-OB nothing is learnable from logs, but traces give the links and the
calls on them. Dependency questions and RCA improve; with Gemini the gain is small and within noise. The
call factor's thresholds and the failing-caller rule were set while looking at these 30 cases,
so treat 0.77 as an optimistic fit: RE3-TT and RE2 are the held-out check. The LLM-only baseline's prompt carries
wall-clock timestamps, so it can't be replayed; it was recorded once.

## Nightly gate baseline

`eval/baseline.json`: reduced settings (`--no-tune`; IoT with 3 epochs, 3 devices, 2 h) so CI
fits in minutes. Not comparable with the full-run numbers above.

| check | baseline | tolerance |
|---|---|---|
| parsing mean GA (15 systems incl. IoT) | 0.8037 | −0.02 |
| alias false merges (offline) | 0 | hard limit |
| IoT F1 | 0.5147 | −0.05 |
