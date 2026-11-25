"""Calls between services, measured from traces (OpenTelemetry spans, or an RCAEval
``traces.csv``).

A span whose parent was made by another service is one call, parent's service -> span's
service. A client span to something that sends no spans of its own (a database, a cache, a
broker: ``db.system`` or ``messaging.system`` set) is a call to ``peer.service``, else its
``server.address`` host, else the system's name. Spans aren't kept: only per-minute counts of
calls, errors and time per (caller, callee), which is all the graph needs.
"""

from __future__ import annotations

import csv
import sys
import threading
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.protobuf.json_format import MessageToDict

KEEP = timedelta(days=2)
SPANS = 200_000  # span id -> service, for children that arrive after their parent
PENDING = 50_000  # children whose parent hasn't arrived yet
ERROR_CODES = {2, "2", "STATUS_CODE_ERROR"}
BACKEND_KINDS = {3, 4, "3", "4", "SPAN_KIND_CLIENT", "SPAN_KIND_PRODUCER"}


def _minute(t: datetime) -> int:
    return int(t.timestamp() // 60)


class CallLog:
    """Per-minute calls, errors and time between services. In memory: shortcut: the counts of
    the last ``KEEP`` are lost on restart (the links themselves are kept as control docs)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._service: OrderedDict[str, str] = OrderedDict()
        self._pending: OrderedDict[str, list[tuple]] = OrderedDict()
        self._minutes: dict[tuple[str, str], dict[int, list]] = defaultdict(dict)
        self._seen = (sys.maxsize, -1)  # first and last minute with any call: what was measured
        self.new: set[tuple[str, str]] = set()  # pairs not yet written as links

    def span(
        self, sid: str, parent: str, service: str, error: bool, ms: float, at: datetime
    ) -> None:
        with self._lock:
            if sid:
                self._service[sid] = service
                if len(self._service) > SPANS:
                    self._service.popitem(last=False)
                for child in self._pending.pop(sid, ()):
                    self._call(service, *child)
            if not parent:
                return
            caller = self._service.get(parent)
            if caller is not None:
                self._call(caller, service, error, ms, at)
            else:
                self._pending.setdefault(parent, []).append((service, error, ms, at))
                if len(self._pending) > PENDING:
                    self._pending.popitem(last=False)

    def call(self, src: str, dst: str, error: bool, ms: float, at: datetime) -> None:
        with self._lock:
            self._call(src, dst, error, ms, at)

    def _call(self, src: str, dst: str, error: bool, ms: float, at: datetime) -> None:
        if not src or not dst or src == dst:
            return
        per = self._minutes[(src, dst)]
        if not per:
            self.new.add((src, dst))
        minute = _minute(at)
        self._seen = (min(self._seen[0], minute), max(self._seen[1], minute))
        m = per.setdefault(minute, [0, 0, 0.0])
        m[0] += 1
        m[1] += bool(error)
        m[2] += ms
        if len(per) > KEEP.total_seconds() // 60:
            del per[min(per)]

    def pairs(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._minutes)

    def take_new(self) -> set[tuple[str, str]]:
        with self._lock:
            new, self.new = self.new, set()
            return new

    def stats(
        self, src: str, dst: str, start: datetime, end: datetime, at: datetime | None = None
    ) -> dict:
        """Calls, errors and mean ms over [at, end), against [start, at) (``at`` unset: over
        [start, end) against the window of the same length before), in whole minutes, and only
        the minutes traces cover. ``base_calls`` is scaled to the first window's length, so the
        two compare directly."""
        with self._lock:
            first, last = self._seen
        if at:  # whole minutes on each side: the one holding ``at`` is neither
            lo, b_hi = -(-int(at.timestamp()) // 60), _minute(at)
            b_lo = _minute(start)
        else:
            lo = b_hi = _minute(start)
            b_lo = 2 * lo - _minute(end)
        hi = min(_minute(end), last + 1)
        b_lo = max(b_lo, first)
        width, b_width = max(hi - lo, 1), max(b_hi - b_lo, 1)
        out = {"calls": 0, "call_errors": 0, "ms": 0.0, "base_calls": 0, "base_ms": 0.0}
        with self._lock:
            per = self._minutes.get((src, dst)) or {}
            now = [v for k, v in per.items() if lo <= k < hi]
            base = [v for k, v in per.items() if b_lo <= k < b_hi]
        if n := sum(v[0] for v in now):
            out |= {"calls": n, "call_errors": sum(v[1] for v in now)}
            out["ms"] = round(sum(v[2] for v in now) / n, 3)
        if n := sum(v[0] for v in base):
            out["base_calls"] = round(n * width / b_width)
            out["base_ms"] = round(sum(v[2] for v in base) / n, 3)
        return out


def broken(s: dict) -> str | None:
    """Why a link's calls look broken against the window before it, or None."""
    if s["base_calls"] >= 20 and s["calls"] < 0.5 * s["base_calls"]:
        return f"calls fell {s['base_calls']} -> {s['calls']}"
    if s["calls"] >= 10 and s["call_errors"] / s["calls"] >= 0.2:
        return f"{s['call_errors']} of {s['calls']} calls failed"
    if s["calls"] >= 10 and s["base_ms"] and s["ms"] >= 2 * s["base_ms"]:
        return f"calls slowed {s['base_ms']:g} -> {s['ms']:g} ms"
    return None


def _attrs(items) -> dict:
    out = {}
    for a in items or []:
        v = a.get("value") or {}
        out[a.get("key")] = next(iter(v.values()), None) if v else None
    return out


def _backend(attrs: dict) -> str | None:
    system = attrs.get("db.system.name") or attrs.get("db.system") or attrs.get("messaging.system")
    if not system:
        return None
    host = str(attrs.get("server.address") or attrs.get("net.peer.name") or "").split(":")[0]
    return str(attrs.get("peer.service") or host or system)


def decode(raw: bytes, content_type: str) -> dict:
    """An OTLP traces request (JSON, or protobuf converted to the same shape)."""
    import json

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    if content_type.split(";")[0].strip() == "application/x-protobuf":
        request = ExportTraceServiceRequest()
        try:
            request.ParseFromString(raw)
        except Exception as error:
            raise ValueError(f"not an OTLP ExportTraceServiceRequest: {error}") from error
        return MessageToDict(request)
    try:
        body = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"not JSON: {error}") from error
    if not isinstance(body, dict) or not isinstance(body.get("resourceSpans", []), list):
        raise ValueError('expected an OTLP traces request: {"resourceSpans": [...]}')  # noqa: TRY004 - shown as a 400
    return body


def ingest(log: CallLog, request: dict) -> int:
    """Feed every span of an OTLP request; the number of spans."""
    n = 0
    for rs in request.get("resourceSpans") or []:
        res = _attrs((rs.get("resource") or {}).get("attributes"))
        service = str(res.get("service.name") or "")
        for ss in rs.get("scopeSpans") or []:
            for sp in ss.get("spans") or []:
                start = int(sp.get("startTimeUnixNano") or 0)
                end = int(sp.get("endTimeUnixNano") or 0) or start
                at = datetime.fromtimestamp(start / 1e9, UTC) if start else datetime.now(UTC)
                error = (sp.get("status") or {}).get("code") in ERROR_CODES
                ms = max(end - start, 0) / 1e6
                log.span(
                    sp.get("spanId") or "", sp.get("parentSpanId") or "", service, error, ms, at
                )
                if sp.get("kind") in BACKEND_KINDS and (
                    dst := _backend(_attrs(sp.get("attributes")))
                ):
                    log.call(service, dst, error, ms, at)
                n += 1
    return n


def read_csv(log: CallLog, path: Path) -> int:
    """Feed an RCAEval (Jaeger export) ``traces.csv``: spanID, parentSpanID, serviceName,
    startTimeMillis, duration (µs), statusCode (gRPC/HTTP; 0 and 2xx are fine)."""
    csv.field_size_limit(sys.maxsize)
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r.get("statusCode") or ""
            try:
                c = int(float(code)) if code else 0
            except ValueError:
                c = 0
            log.span(
                r["spanID"],
                r.get("parentSpanID") or "",
                r["serviceName"],
                c != 0 and not 200 <= c < 400,
                float(r.get("duration") or 0) / 1000,
                datetime.fromtimestamp(int(r["startTimeMillis"]) / 1000, UTC),
            )
            n += 1
    return n
