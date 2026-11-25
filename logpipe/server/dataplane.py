"""The dataplane: the client's database behind a connection the client opens.

For data residency the client's network only dials out. ``logpipe dataplane`` runs next to the
client's store and connects to the hosted server (``logpipe server --store remote``); the server
sends its store calls down that connection, so parsed rows, control docs and metrics live only on
the client side. The hosted server parses each line, sends the row on and keeps nothing.

Wire: JSON text frames. The dataplane opens with a hello ``{"store", "engine", "table"}``; then
each request is ``{"id", "target", "method", "args", "kwargs"}`` and each answer ``{"id", "ok"}``
or ``{"id", "error", "kind"}``. Values go through :func:`encode` / :func:`decode`, which know a
fixed set of types: never pickle, since neither side may run code the other sends.
"""

from __future__ import annotations

import base64
import dataclasses
import hmac
import itertools
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from ..parsing.frozen_parser import Status
from ..storage.base import AnomalyAlert, LogRow

LOGGER = logging.getLogger("logpipe.dataplane")

MAX_FRAME = 64 << 20

# The only calls a dataplane answers: the store protocols, nothing that reaches the database
# client itself. `reads.query` is the read-only path the UI's Query page already uses.
METHODS: dict[str, frozenset[str]] = {
    "store": frozenset(
        {"insert_logs", "insert_alerts", "fetch_pending", "count_by_status", "flush"}
    ),
    "reads": frozenset(
        {
            "recent",
            "between",
            "histogram",
            "top",
            "source_lines",
            "source_rows",
            "alerts",
            "query",
            "records",
        }
    ),
    "compiler": frozenset(
        {"build", "schema", "rows", "template_counts", "buckets", "template_sample"}
    ),
    "series": frozenset({"write", "query"}),
    "docs": frozenset({"load", "write"}),
}
# Writes wait for the dataplane to come back instead of failing; every one of them is safe to
# repeat (the highest version per log_id wins, a control doc is replaced whole).
WAITS = {
    ("store", "insert_logs"),
    ("store", "insert_alerts"),
    ("store", "flush"),
    ("series", "write"),
    ("docs", "write"),
    ("docs", "load"),
}
# Errors that keep their type across the wire (the UI turns ValueError into a 400, and so on).
ERRORS: dict[str, type[Exception]] = {
    e.__name__: e for e in (ValueError, KeyError, PermissionError, TimeoutError, LookupError)
}
_RECORDS: dict[str, type] = {"LogRow": LogRow, "AnomalyAlert": AnomalyAlert}
_TAGS = {"$dt", "$date", "$uuid", "$tuple", "$set", "$bytes", "$map", "$rec"}


class DataplaneDown(RuntimeError):
    """The client's dataplane is not connected (or dropped mid-call)."""


def encode(v):
    """A JSON-safe form of ``v`` that :func:`decode` turns back into the same types."""
    if v is None or isinstance(v, bool | int | float | str):
        return v.value if isinstance(v, Enum) else v
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, date):
        return {"$date": v.isoformat()}
    if isinstance(v, uuid.UUID):
        return {"$uuid": str(v)}
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return {"$bytes": base64.b64encode(v).decode()}
    if type(v).__name__ in _RECORDS and dataclasses.is_dataclass(v):
        fields = {f.name: encode(getattr(v, f.name)) for f in dataclasses.fields(v)}
        return {"$rec": [type(v).__name__, fields]}
    if isinstance(v, dict):
        if all(isinstance(k, str) for k in v) and not (len(v) == 1 and next(iter(v)) in _TAGS):
            return {k: encode(x) for k, x in v.items()}
        return {"$map": [[encode(k), encode(x)] for k, x in v.items()]}
    if isinstance(v, tuple):
        return {"$tuple": [encode(x) for x in v]}
    if isinstance(v, list):
        return [encode(x) for x in v]
    if isinstance(v, set | frozenset):
        return {"$set": [encode(x) for x in v]}
    if hasattr(v, "item") and hasattr(v, "dtype"):  # a numpy scalar
        return encode(v.item())
    if type(v).__name__ == "ObjectId":  # MongoDB ids in raw query results
        return str(v)
    raise TypeError(f"the dataplane cannot send a {type(v).__name__}")


def decode(v) -> Any:
    if isinstance(v, list):
        return [decode(x) for x in v]
    if not isinstance(v, dict):
        return v
    if len(v) == 1 and (tag := next(iter(v))) in _TAGS:
        x = v[tag]
        if tag == "$dt":
            return datetime.fromisoformat(x)
        if tag == "$date":
            return date.fromisoformat(x)
        if tag == "$uuid":
            return uuid.UUID(x)
        if tag == "$bytes":
            return base64.b64decode(x)
        if tag == "$tuple":
            return tuple(decode(i) for i in x)
        if tag == "$set":
            return {decode(i) for i in x}
        if tag == "$map":
            return {decode(k): decode(i) for k, i in x}
        name, fields = x
        if name not in _RECORDS:
            raise ValueError(f"unknown record type {name!r}")
        out = {k: decode(i) for k, i in fields.items()}
        if name == "LogRow":
            out["status"] = Status(out["status"])
        return _RECORDS[name](**out)
    return {k: decode(x) for k, x in v.items()}


class Tunnel:
    """The hosted end: listens for this server's one dataplane and sends it calls.

    Its own port and thread, apart from the UI's event loop, so a call made from any thread
    (an async handler included) never waits on the loop that would deliver its answer."""

    def __init__(self, key: str, host: str = "0.0.0.0", port: int = 8090, timeout: float = 60):
        from websockets.sync.server import serve

        if not key:
            raise ValueError("the dataplane needs a key (LOGPIPE_DATAPLANE_KEY)")
        self._key, self.timeout = key, timeout
        self._conn = None
        self._send_lock = threading.Lock()
        self._pending: dict[int, Future] = {}
        self._ids = itertools.count(1)
        self._up = threading.Event()
        self.hello: dict = {}
        self._server = serve(
            self._handle, host, port, process_request=self._admit, max_size=MAX_FRAME
        )
        self.port = self._server.socket.getsockname()[1]
        threading.Thread(
            target=self._server.serve_forever, name="dataplane-tunnel", daemon=True
        ).start()

    @property
    def connected(self) -> bool:
        return self._up.is_set()

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._up.wait(timeout)

    def close(self) -> None:
        self._server.shutdown()

    def _admit(self, connection, request):
        given = request.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {self._key}".encode()):
            return connection.respond(401, "bad dataplane key\n")
        return None

    def _fail_pending(self, why: str) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():  # its answer may have landed a moment ago
                fut.set_exception(DataplaneDown(why))

    def _handle(self, conn) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            hello = json.loads(conn.recv(timeout=30))
        except (TimeoutError, ConnectionClosed, ValueError):
            return
        old = self._conn
        # The newest connection wins: after a network blip the old one may not have noticed yet.
        self._conn, self.hello = conn, hello
        if old is not None:
            self._fail_pending("the dataplane reconnected")
            old.close(1012, "replaced by a new connection")
        self._up.set()
        LOGGER.warning("dataplane connected: %s store", hello.get("store"))
        try:
            for frame in conn:
                msg = json.loads(frame)
                fut = self._pending.pop(msg.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except ConnectionClosed:
            pass
        finally:
            if self._conn is conn:
                self._conn = None
                self._up.clear()
                self._fail_pending("the dataplane disconnected")
                LOGGER.warning("dataplane disconnected")

    def call(
        self, target: str, method: str, args=(), kwargs=None, *, wait: bool | None = None
    ) -> Any:
        """Run ``target.method(*args, **kwargs)`` on the dataplane. Reads fail fast with
        :class:`DataplaneDown`; writes (:data:`WAITS`) retry until the dataplane is back."""
        from websockets.exceptions import ConnectionClosed

        wait = (target, method) in WAITS if wait is None else wait
        body = {"target": target, "method": method}
        body |= {"args": encode(list(args)), "kwargs": encode(kwargs or {})}
        while True:
            conn = self._conn
            if conn is None:
                if not wait:
                    raise DataplaneDown("the client's dataplane is not connected")
                self._up.wait(5)
                continue
            n = next(self._ids)
            fut: Future = Future()
            self._pending[n] = fut
            frame = json.dumps({"id": n, **body})
            try:
                with self._send_lock:
                    conn.send(frame)
                msg = fut.result(timeout=self.timeout)
            except (ConnectionClosed, DataplaneDown, TimeoutError) as error:
                self._pending.pop(n, None)
                if not wait:
                    raise DataplaneDown(f"{target}.{method}: {error}") from error
                LOGGER.warning("dataplane %s.%s failed (%s); retrying", target, method, error)
                time.sleep(1)
                continue
            if "error" in msg:
                raise ERRORS.get(msg.get("kind"), RuntimeError)(msg["error"])
            return decode(msg["ok"])


class Remote:
    """One of the dataplane's objects on the hosted side: its allowed methods become calls."""

    def __init__(self, tunnel: Tunnel, target: str, before: Callable | None = None, **attrs):
        self._tunnel, self._target, self._before = tunnel, target, before
        self.__dict__.update(attrs)

    def __getattr__(self, name: str) -> Callable:
        if name.startswith("_") or name not in METHODS[self._target]:
            raise AttributeError(name)

        def call(*args, **kwargs):
            if self._before is not None:
                self._before()
            return self._tunnel.call(self._target, name, args, kwargs)

        return call


class RemoteStore:
    """A LogStore on the client's dataplane. Rows wait here in memory, in batches like the
    ClickHouse store's, and go on ``flush`` or a full batch; nothing is written to disk."""

    BATCH = 2_000
    residency = True

    def __init__(self, tunnel: Tunnel):
        self.tunnel = tunnel
        self._rows: list[LogRow] = []
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.tunnel.connected

    def reads(self) -> Remote:
        h = self.tunnel.hello
        compiler = Remote(self.tunnel, "compiler", engine=h.get("engine"), table=h.get("table"))
        # Reads first send the batched rows, so the server reads what it wrote (as in memory).
        return Remote(
            self.tunnel, "reads", before=self._send, engine=h.get("engine"), compiler=compiler
        )

    def _send(self, wait: bool | None = None) -> None:
        with self._lock:
            rows, self._rows = self._rows, []
        if rows:
            self.tunnel.call("store", "insert_logs", (rows,), wait=wait)

    def insert_logs(self, rows) -> None:
        with self._lock:
            self._rows.extend(rows)
            full = len(self._rows) >= self.BATCH
        if full:
            self._send()

    def insert_alerts(self, alerts) -> None:
        self.tunnel.call("store", "insert_alerts", (list(alerts),))

    def fetch_pending(self, limit: int = 10_000, template_id: int | None = None) -> list[LogRow]:
        self._send()
        return self.tunnel.call("store", "fetch_pending", (limit, template_id))

    def count_by_status(self) -> dict[str, int]:
        self._send()
        return self.tunnel.call("store", "count_by_status")

    def flush(self) -> None:
        self._send()
        self.tunnel.call("store", "flush")

    def close(self) -> None:
        """Send what is batched if the dataplane is there; shutting down never waits for it."""
        try:
            self._send(wait=False)
            self.tunnel.call("store", "flush", wait=False)
        except DataplaneDown as error:
            LOGGER.warning(
                "closing without the dataplane, %d rows not sent: %s", len(self._rows), error
            )
        self.tunnel.close()


def _answer(ws, lock: threading.Lock, targets: dict, frame: str) -> None:
    msg: dict = {}
    try:
        msg = json.loads(frame)
        target, method = msg["target"], msg["method"]
        if method not in METHODS.get(target, ()):
            raise PermissionError(f"{target}.{method} is not a dataplane call")
        result = getattr(targets[target], method)(*decode(msg["args"]), **decode(msg["kwargs"]))
        out = {"id": msg["id"], "ok": encode(result)}
    except Exception as error:
        kind = type(error).__name__
        out = {"id": msg.get("id"), "error": str(error), "kind": kind if kind in ERRORS else ""}
        if kind not in ERRORS:
            LOGGER.exception("dataplane call failed")
    with lock:
        ws.send(json.dumps(out))


class _NoDocs:
    """The memory store keeps control docs nowhere: the server's own copy is all there is."""

    def load(self) -> list:
        return []

    def write(self, kind, doc_id, doc) -> None:
        return None


def serve(url: str, key: str, store, *, workers: int = 8, stop: threading.Event | None = None):
    """The client end: connect out to the hosted server and answer its store calls, forever
    (reconnecting with backoff) or until ``stop`` is set."""
    from websockets.exceptions import ConnectionClosed, InvalidHandshake
    from websockets.sync.client import connect

    from .stores import docs_for, reads_for, series_for

    reads = reads_for(store)
    targets = {
        "store": store,
        "reads": reads,
        "compiler": reads.compiler,
        "series": series_for(store),
        "docs": docs_for(store) or _NoDocs(),
    }
    hello = {"store": type(store).__name__, "engine": reads.engine, "table": reads.compiler.table}
    stop = stop or threading.Event()
    backoff = 1.0
    with ThreadPoolExecutor(workers, thread_name_prefix="dataplane") as pool:
        while not stop.is_set():
            try:
                with connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {key}"},
                    max_size=MAX_FRAME,
                    open_timeout=15,
                ) as ws:
                    ws.send(json.dumps(hello))
                    backoff = 1.0
                    print(f"[dataplane] connected to {url}", flush=True)
                    lock = threading.Lock()
                    while not stop.is_set():
                        try:
                            frame = ws.recv(timeout=1)
                        except TimeoutError:
                            continue
                        pool.submit(_answer, ws, lock, targets, frame)
            except (OSError, ConnectionClosed, InvalidHandshake) as error:
                print(f"[dataplane] {error}; retrying in {backoff:.0f}s", flush=True)
                stop.wait(backoff)
                backoff = min(backoff * 2, 30)
