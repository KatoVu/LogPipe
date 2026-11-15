"""The showcase's web shop: its logs, and that its incident is what LogBERT flags (and normal
traffic isn't)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from logpipe import showcase, start
from logpipe.enrich.llm import get_adapter
from logpipe.server.app import create_app
from logpipe.server.control import ControlStore
from logpipe.server.core import Server
from logpipe.showcase.generator import FILES, ShopGenerator
from logpipe.storage.memory import MemoryStore


def test_generator_writes_files_with_multiline_traces(tmp_path):
    g = ShopGenerator(tmp_path, seed=1)
    g.write(g.order() + g.order(incident=True))
    api = (tmp_path / FILES["shop-api"][0]).read_text()
    assert "checkout started" in api and "\tat com.shop." in api  # a stack trace, many lines
    assert '" 502 ' in (tmp_path / FILES["nginx"][0]).read_text()
    assert not (tmp_path / FILES["inventory"][0]).exists()
    g.inventory = True
    g.write(g.order())
    assert "stock reserved" in (tmp_path / FILES["inventory"][0]).read_text()


def test_the_incident_alerts_and_normal_traffic_does_not(tmp_path):
    server = Server(
        MemoryStore(),
        ControlStore(),
        data_dir=tmp_path,
        adapter_factory=lambda: get_adapter("offline"),
        start_threads=False,
    )
    client = TestClient(create_app(server))
    headers = {"X-API-Key": client.post("/api/keys", json={"name": "t"}).json()["token"]}
    g, t0 = ShopGenerator(tmp_path / "out"), datetime.now(UTC) - timedelta(minutes=30)

    def feed(n: int, start: int, incident: bool = False) -> None:
        evs = []
        for i in range(n):
            now = t0 + timedelta(seconds=(start + i) / 3)
            evs += [
                {
                    "ts": now.isoformat(),
                    "host": "shop",
                    "service": e.service,
                    "source": FILES[e.service][1],
                    "message": e.message,
                }
                for e in g.order(now, incident)
            ]
        assert (
            client.post("/intake/v1/logs", json={"events": evs}, headers=headers).status_code == 202
        )
        while not server.inbox.empty():
            server.process(server.inbox.get_nowait())

    def run_jobs() -> None:
        while not server.jobs.empty():
            assert server.run_job(server.jobs.get_nowait())["status"] == "done"

    feed(500, 0)
    client.post("/api/review/generate", json={})
    run_jobs()
    bid = client.get("/api/setup").json()["review"]["id"]
    client.post(f"/api/review/{bid}/decide-all", json={"status": "approved"})
    client.post(f"/api/review/{bid}/apply")
    run_jobs()
    feed(200, 500)
    assert client.get("/api/alerts").json()["alerts"] == []
    feed(40, 700, incident=True)
    alerts = client.get("/api/alerts").json()["alerts"]
    assert alerts and {a["key"] for a in alerts} == {"shop/shop-api"}
    assert all(a["reasons"] == ["sequence"] for a in alerts)


def test_showcase_saves_its_answers_as_the_next_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    start.SAVED.parent.mkdir()
    start.SAVED.write_text(json.dumps({"store": "clickhouse", "server_port": 9191}))
    # Keep pytest's own SIGTERM handling.
    monkeypatch.setattr(showcase.signal, "signal", lambda *_: None)
    monkeypatch.setattr(showcase.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(start, "_free_port", lambda _: None)

    def stop(_a):
        raise SystemExit("stop")  # everything after the questions is out of scope here

    monkeypatch.setattr(start, "plan", stop)
    # queue, store (Enter = saved clickhouse), llm, port (Enter = saved 9191), data dir
    it = iter(["kafka", "", "offline", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(it))
    with pytest.raises(SystemExit):
        showcase.run()
    saved = json.loads(start.SAVED.read_text())
    assert saved["queue"] == "kafka" and saved["store"] == "clickhouse"
    assert saved["server_port"] == 9191


def test_a_failed_do_can_be_retried_with_enter(monkeypatch):
    """The draft act used to hang after its Enter-started job failed: Enter was spent."""
    import io
    import urllib.error

    monkeypatch.setattr(showcase, "_enter_pressed", lambda _s: True)  # Enter, every time
    monkeypatch.setattr(showcase, "_drop_typed_ahead", lambda: None)
    monkeypatch.setattr(showcase.time, "sleep", lambda _s: None)
    tour = showcase.Tour(api=None, url="", auto=False)  # type: ignore[arg-type]
    tries, pending = [], []

    def do():
        tries.append(1)
        if len(tries) == 1:  # refused by the server
            raise urllib.error.HTTPError(
                "u", 400, "Bad Request", None, io.BytesIO(b'{"detail": "no"}')
            )
        pending.append("draft job failed: the LLM is down" if len(tries) == 2 else "ok")

    def steps():
        out, pending[:] = list(pending), []
        return out

    tour.wait("templates drafted", lambda: len(tries) >= 3, do, steps=steps, timeout=60)
    assert len(tries) == 3


def test_fresh_volumes_removes_only_the_started_services_data(monkeypatch):
    monkeypatch.setattr(showcase.shutil, "which", lambda _: "/usr/bin/docker")
    calls = []

    def run(cmd, **_kw):
        calls.append(cmd)
        config = {"volumes": {v: {"name": f"proj_{v}"} for v in showcase.VOLUMES.values()}}
        return type("Done", (), {"stdout": json.dumps(config)})()

    assert showcase.fresh_volumes(["kafka", "mongo"], run=run) == [
        "proj_kafka_data",
        "proj_mongo_data",
    ]
    assert calls[1] == ["docker", "compose", "rm", "--stop", "--force", "kafka", "mongo"]
    assert calls[2] == ["docker", "volume", "rm", "--force", "proj_kafka_data", "proj_mongo_data"]
