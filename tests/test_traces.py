"""Links measured from traces: span joins, OTLP intake, precedence over labels, the RCA lift."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from logpipe.ask.models import Candidate
from logpipe.ask.rca import GraphRow, lift_broken_calls
from logpipe.graph.service import GraphService
from logpipe.graph.store import MemoryGraph
from logpipe.graph.traces import CallLog, broken, ingest
from logpipe.server.control import ControlStore
from tests.test_server import env  # noqa: F401

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_spans_join_into_calls_whichever_arrives_first():
    log = CallLog()
    log.span("a", "", "frontend", False, 5, T0)
    log.span("b", "a", "checkout", False, 3, T0)  # parent already seen
    log.span("d", "c", "email", True, 1, T0)  # parent not yet seen
    log.span("c", "b", "checkout", False, 2, T0)  # same service: no call, resolves d
    assert sorted(log.pairs()) == [("checkout", "email"), ("frontend", "checkout")]
    s = log.stats("checkout", "email", T0, T0 + timedelta(minutes=1))
    assert (s["calls"], s["call_errors"]) == (1, 1)


def test_broken_compares_with_the_window_before():
    log = CallLog()
    for m in range(10):
        for _ in range(30 if m < 5 else 2):
            log.call("checkout", "email", False, 1, T0 + timedelta(minutes=m))
    s = log.stats("checkout", "email", T0 + timedelta(minutes=5), T0 + timedelta(minutes=10))
    assert (s["base_calls"], s["calls"]) == (150, 10)
    assert broken(s) == "calls fell 150 -> 10"
    assert broken({"calls": 20, "call_errors": 1, "ms": 1, "base_calls": 20, "base_ms": 1}) is None


def test_otlp_request_gives_service_calls_and_backend_calls():
    def span(sid, parent, kind=2, attrs=(), code=0):
        return {
            "spanId": sid,
            "parentSpanId": parent,
            "kind": kind,
            "startTimeUnixNano": str(int(T0.timestamp() * 1e9)),
            "endTimeUnixNano": str(int(T0.timestamp() * 1e9) + 2_000_000),
            "status": {"code": code},
            "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs],
        }

    def res(service, spans):
        return {
            "resource": {
                "attributes": [{"key": "service.name", "value": {"stringValue": service}}]
            },
            "scopeSpans": [{"spans": spans}],
        }

    log = CallLog()
    n = ingest(
        log,
        {
            "resourceSpans": [
                res(
                    "cart",
                    [
                        span("c1", "f1", code=2),
                        span(
                            "c2",
                            "c1",
                            3,
                            [("db.system", "redis"), ("server.address", "valkey-cart:6379")],
                        ),
                    ],
                ),
                res("frontend", [span("f1", "")]),
            ]
        },
    )
    assert n == 3
    assert sorted(log.pairs()) == [("cart", "valkey-cart"), ("frontend", "cart")]
    s = log.stats("frontend", "cart", T0, T0 + timedelta(minutes=1))
    assert (s["calls"], s["call_errors"], s["ms"]) == (1, 1, 2.0)


def test_traced_pairs_win_over_labels_but_not_over_a_removal():
    control = ControlStore()
    g = GraphService(control, MemoryGraph())
    control.put(
        "graph_label",
        "api:1",
        {
            "id": "api:1",
            "service": "api",
            "template_id": 1,
            "by": "rules",
            "edges": [{"target": "postgres", "relation": "writes_to"}],
        },
    )
    g.calls.call("api", "postgres", False, 1, T0)
    g.calls.call("web", "api", False, 1, T0)
    assert g.save_calls() and not g.save_calls()
    edges = {(e.src, e.dst): e for e in g.derive({"api", "postgres", "web"}, lambda t: t)}
    assert (edges[("api", "postgres")].by, edges[("api", "postgres")].templates) == ("traces", (1,))
    assert len(edges) == 2  # the labelled writes_to folded into the traced link
    g.edit("web", "api", "depends_on", removed=True)
    assert [(e.src, e.dst) for e in g.derive({"api", "postgres", "web"}, lambda t: t)] == [
        ("api", "postgres")
    ]


def test_broken_calls_put_the_callee_above_its_caller(env):  # noqa: F811
    server, _, _ = env

    def cand(service, score):
        return Candidate(
            template_id=1,
            service=service,
            count=1,
            first=T0,
            last=T0,
            example="",
            template="",
            new=False,
            baseline_median=0,
            anomaly=1,
            rarity=1,
            proximity=1,
            score=score,
            why="",
        )

    cands = [cand("checkout", 0.44), cand("frontend", 0.13), cand("email", 0.05)]
    edges = [
        GraphRow(src="checkout", dst="email", by="traces", calls=4, base_calls=161, hops=1),
        GraphRow(src="frontend", dst="checkout", by="traces", calls=140, base_calls=161, hops=1),
    ]
    ctx = server.ask.ctx(question="why?")
    lift_broken_calls(cands, edges, ctx)
    assert [c.service for c in cands] == ["email", "checkout", "frontend"]
    assert "calls fell 161 -> 4" in cands[0].why
    # read as the whole graph (hops 0 everywhere): still callers first, so the deepest wins
    chain = [cand("frontend", 0.5), cand("checkout", 0.2), cand("currency", 0.1)]
    whole = [
        GraphRow(src="checkout", dst="currency", by="traces", calls=100, base_calls=380, hops=0),
        GraphRow(src="frontend", dst="checkout", by="traces", calls=100, call_errors=100, hops=0),
    ]
    lift_broken_calls(chain, whole, ctx)
    assert [c.service for c in chain] == ["currency", "checkout", "frontend"]
    # checkout itself fails: what it stopped calling altogether was never reached
    gave_up = [
        cand("frontend", 0.5),
        cand("checkout", 0.2),
        cand("currency", 0.1),
        cand("email", 0.1),
    ]
    whole.append(GraphRow(src="checkout", dst="email", by="traces", calls=0, base_calls=112))
    lift_broken_calls(gave_up, whole, ctx)
    assert [c.service for c in gave_up][:3] == ["currency", "checkout", "frontend"]
    assert "checkout -> email" not in gave_up[3].why
    ctx.rca = ctx.rca.model_copy(update={"call_weight": 0})
    again = [cand("checkout", 0.44), cand("email", 0.05)]
    lift_broken_calls(again, edges, ctx)
    assert again[0].service == "checkout"


def test_otlp_traces_endpoint_feeds_the_graph(env):  # noqa: F811
    server, client, headers = env
    t = str(int(T0.timestamp() * 1e9))
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": s}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "spanId": sid,
                                "parentSpanId": p,
                                "startTimeUnixNano": t,
                                "endTimeUnixNano": t,
                            }
                        ]
                    }
                ],
            }
            for s, sid, p in (("web", "1", ""), ("api", "2", "1"))
        ]
    }
    r = client.post("/v1/traces", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert (
        client.post(
            "/v1/traces", content=b"nope", headers=headers | {"content-type": "application/json"}
        ).status_code
        == 400
    )
    server.graph.tick(server)
    assert [(e.src, e.dst, e.by) for e in server.graph.store.edges()] == [("web", "api", "traces")]
    # components only traces show can be asked about by name
    assert {"web", "api"} <= server.ask.catalog().services


def test_stats_split_at_the_anchor_scale_the_part_before():
    log = CallLog()
    for m in range(30):  # 10 a minute for 20 minutes, then 1 a minute
        for _ in range(10 if m < 20 else 1):
            log.call("checkout", "email", False, 1, T0 + timedelta(minutes=m))
    s = log.stats("checkout", "email", T0, T0 + timedelta(minutes=30), T0 + timedelta(minutes=20))
    assert (s["calls"], s["base_calls"]) == (10, 100)  # 200 over 20 min, scaled to 10 min
    assert broken(s) == "calls fell 100 -> 10"
