"""The dataplane tunnel: its codec, its call allowlist, its key, and what happens when the
client's side is gone. The whole server suite also runs through it: ``STORE=remote pytest
tests/test_server.py``."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest

from logpipe.parsing.frozen_parser import Status
from logpipe.server.dataplane import (
    DataplaneDown,
    RemoteStore,
    Tunnel,
    decode,
    encode,
    serve,
)
from logpipe.server.stores import docs_for, reads_for, series_for
from logpipe.storage.base import AnomalyAlert, LogRow
from logpipe.storage.memory import MemoryStore
from tests.test_server import app_lines, env, events, run_jobs, send  # noqa: F401 - env is a fixture

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def row(i: int = 0, status: Status = Status.FINAL) -> LogRow:
    return LogRow(
        log_id=uuid.uuid4(),
        timestamp=NOW,
        device_id="web-1",
        template_id=2,
        status=status,
        registry_version=1,
        payload={"user": str(i)},
        raw_message=f"user {i} logged in",
        service="api",
        tags=("env:test",),
    )


def test_codec_round_trips_the_types_the_store_calls_carry():
    alert = AnomalyAlert("web-1", NOW, NOW, (2, 3), 0.5, 0.1, None, ("miss",), False, 1, "m1")
    value = {
        "rows": [row(1, Status.PENDING)],
        "alert": alert,
        "when": NOW,
        "pair": ("a", 1),
        "ints": {1: "one"},
        "looks_like_a_tag": {"$dt": "not a date"},
        "raw": b"\x00\x01",
    }
    back = decode(encode(value))
    assert back == value
    assert back["rows"][0].status is Status.PENDING
    assert isinstance(back["pair"], tuple)


def test_codec_refuses_unknown_records_and_types():
    with pytest.raises(ValueError, match="unknown record"):
        decode({"$rec": ["Popen", {}]})
    with pytest.raises(TypeError):
        encode(object())


@pytest.fixture
def plane():
    tunnel, stop, local = (
        Tunnel("k", host="127.0.0.1", port=0, timeout=5),
        threading.Event(),
        MemoryStore(),
    )
    thread = threading.Thread(
        target=serve,
        args=(f"ws://127.0.0.1:{tunnel.port}", "k", local),
        kwargs={"stop": stop},
        daemon=True,
    )
    thread.start()
    assert tunnel.wait_connected(10)
    yield RemoteStore(tunnel), local, stop, thread
    stop.set()
    thread.join(5)
    tunnel.close()


def test_rows_reach_the_client_store_on_flush(plane):
    remote, local, *_ = plane
    remote.insert_logs([row(i) for i in range(3)])
    assert local.count_by_status() == {}  # batched in memory until a flush
    remote.flush()
    assert local.count_by_status() == {"FINAL": 3}
    assert remote.count_by_status() == {"FINAL": 3}
    pending = row(9, Status.PENDING)
    remote.insert_logs([pending])
    assert [r.log_id for r in remote.fetch_pending()] == [pending.log_id]


def test_reads_series_and_docs_go_through(plane):
    remote, *_ = plane
    remote.insert_logs([row()])
    remote.flush()
    reads = reads_for(remote)
    assert reads.engine == reads_for(MemoryStore()).engine
    assert reads.source_lines("web-1", "api", "", 5) == ["user 0 logged in"]
    assert reads.compiler.table
    series = series_for(remote)
    series.write(NOW.timestamp(), {"x": 1.0})
    assert docs_for(remote).load() == []


def test_errors_keep_their_type_and_unlisted_calls_are_refused(plane):
    remote, *_ = plane
    with pytest.raises(ValueError):
        reads_for(remote).query("DROP TABLE logs")
    with pytest.raises(PermissionError, match="not a dataplane call"):
        remote.tunnel.call("store", "close")
    with pytest.raises(AttributeError):
        reads_for(remote).client  # noqa: B018 - only the allowlisted methods exist here


def test_a_wrong_key_never_connects():
    tunnel, stop = Tunnel("right", host="127.0.0.1", port=0), threading.Event()
    threading.Thread(
        target=serve,
        args=(f"ws://127.0.0.1:{tunnel.port}", "wrong", MemoryStore()),
        kwargs={"stop": stop},
        daemon=True,
    ).start()
    assert not tunnel.wait_connected(2)
    stop.set()
    tunnel.close()


def test_without_the_dataplane_reads_fail_fast_and_intake_says_busy(plane, tmp_path):
    from logpipe.enrich.llm import get_adapter
    from logpipe.server.control import ControlStore
    from logpipe.server.core import Busy, Server

    remote, _, stop, thread = plane
    server = Server(
        remote,
        ControlStore(docs_for(remote)),
        data_dir=tmp_path,
        adapter_factory=lambda: get_adapter("offline"),
        start_threads=False,
    )
    stop.set()
    thread.join(5)
    for _ in range(50):
        if not remote.connected:
            break
        threading.Event().wait(0.1)
    assert not remote.connected
    with pytest.raises(DataplaneDown):
        remote.count_by_status()
    with pytest.raises(Busy, match="dataplane"):
        server.ingest([{"message": "hello", "host": "h"}])
    from fastapi.testclient import TestClient

    from logpipe.server.app import create_app

    page = TestClient(create_app(server)).get("/api/logs", params={"limit": 1})
    assert page.status_code == 503 and "unreachable" in page.json()["detail"]


@pytest.fixture
def hosted(monkeypatch, request):
    monkeypatch.setenv("STORE", "remote")
    return request.getfixturevalue("env")


def test_the_hosted_side_keeps_no_line(hosted, tmp_path):
    server, client, headers = hosted
    marker = "zq7residency"
    send(server, client, headers, events([f"{m} {marker}" for m in app_lines(200)]))
    assert client.post("/api/review/generate", json={"sample_cap": 150}).status_code == 202
    run_jobs(server)
    batch = client.get("/api/setup").json()["review"]
    assert batch["drafts"] and any(marker in line for line in server.batch_lines(batch["id"]))
    logs = client.get("/api/logs", params={"limit": 5}).json()["logs"]
    assert logs and marker in logs[0]["message"]  # read back from the client's store
    for path in (p for p in tmp_path.rglob("*") if p.is_file()):
        assert marker.encode() not in path.read_bytes(), f"{path} holds a client line"


def test_remote_refuses_what_would_copy_lines_off_the_client():
    from logpipe.server import residency_check

    residency_check("memory", datadog=False)
    with pytest.raises(SystemExit, match="Kafka"):
        residency_check("kafka", datadog=False)
    with pytest.raises(SystemExit, match="Datadog"):
        residency_check("memory", datadog=True)
