import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from scipy import stats

from logpipe.anomaly.volume import LEARN, VolumeModel, tails
from tests.test_server import env  # noqa: F401 - a fixture

START = datetime(2026, 11, 2, tzinfo=UTC)  # a Monday


def run(model, counts, start=0):
    """Feed one count per minute; return (minute index, change kind) for every change."""
    out = []
    for i, n in enumerate(counts, start):
        change = model.observe(int(n), START + timedelta(minutes=i))
        if change:
            out.append((i, change.kind))
    return out


def poisson(rate, minutes, seed=0):
    return np.random.default_rng(seed).poisson(rate, minutes)


@pytest.mark.parametrize("count", [0, 3, 40, 100, 180])
@pytest.mark.parametrize("dispersion", [0.0, 0.3])
def test_tails_match_scipy(count, dispersion):
    mean = 100.0
    if dispersion:
        r = 1 / dispersion
        dist = stats.nbinom(r, r / (r + mean))
    else:
        dist = stats.poisson(mean)
    low, high, log_zero = tails(count, mean, dispersion)
    assert low == pytest.approx(dist.cdf(count), rel=1e-6, abs=1e-300)
    assert high == pytest.approx(dist.sf(count - 1), rel=1e-6, abs=1e-300)
    assert log_zero == pytest.approx(dist.logpmf(0), rel=1e-6)


def test_steady_and_bursty_traffic_raise_nothing():
    assert run(VolumeModel(), poisson(100, 4 * 1440)) == []
    r, mean = 1 / 0.3, 100
    bursty = np.random.default_rng(1).negative_binomial(r, r / (r + mean), 4 * 1440)
    assert run(VolumeModel(), bursty) == []


def test_busy_source_going_silent_alerts_after_three_empty_minutes_and_recovers():
    model = VolumeModel()
    t = LEARN + 100
    assert run(model, poisson(100, t)) == []
    assert run(model, [0] * 2, t) == []  # a restart
    assert run(model, poisson(100, 10, seed=6), t + 2) == []
    t += 12
    assert run(model, [0] * 30, t) == [(t + 2, "drop")]
    assert run(model, poisson(100, 10, seed=2), t + 30) == [(t + 32, "recovered")]


def test_quiet_source_needs_its_silence_to_become_unlikely():
    model = VolumeModel()
    t = LEARN + 100
    assert run(model, poisson(0.5, t)) == []
    (at, kind), *_ = run(model, [0] * 120, t)
    assert kind == "drop" and 15 <= at - t <= 60  # ~ln(1e6) / 0.5 minutes


def test_partial_drop_and_spike_need_three_minutes_and_a_real_change():
    model = VolumeModel()
    t = LEARN + 100
    run(model, poisson(100, t))
    assert run(model, [70] * 20, t) == []  # improbable but only 30% down
    assert run(model, [30] * 5, t + 20) == [(t + 22, "drop")]
    assert run(model, poisson(100, 5, seed=3), t + 25) == [(t + 27, "recovered")]
    assert run(model, [400] * 5, t + 30) == [(t + 32, "spike")]


def test_learning_alerts_on_silence_only():
    model = VolumeModel()
    assert run(model, poisson(100, 120)) == []
    assert run(model, [20] * 30, 120) == []  # could be a daily pattern
    assert run(model, [0] * 3, 150) == [(152, "drop")]


def test_daily_pattern_is_learned():
    minutes = np.arange(9 * 1440)
    day = (minutes // 60 % 24 >= 8) & (minutes // 60 % 24 < 20)
    counts = np.where(day, poisson(100, len(minutes)), poisson(5, len(minutes), seed=4))
    changes = run(VolumeModel(), counts)
    assert [c for c in changes if c[0] >= LEARN] == []


def test_rebase_accepts_the_new_normal():
    model = VolumeModel()
    t = LEARN + 100
    run(model, poisson(100, t))
    assert run(model, [30] * 5, t) == [(t + 2, "drop")]
    model.rebase()
    assert run(model, poisson(30, 60, seed=5), t + 5) == []


def test_state_survives_json():
    model = VolumeModel()
    run(model, poisson(50, 200))
    copy = VolumeModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert copy == model


def test_server_warns_when_a_source_goes_silent_unless_its_logs_are_held_up(env):  # noqa: F811
    from logpipe.server.volume import SETTLE, Volume

    server, _, _ = env
    t0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=4)
    server.volume = Volume(server.control, now=t0)
    minute = [0]

    def play(lines_per_minute, minutes):
        for _ in range(minutes):
            at = t0 + timedelta(minutes=minute[0])
            server.ingest(
                [
                    {"message": f"GET /x {i}", "host": "h1", "service": "api", "source": "app"}
                    | {"ts": (at + timedelta(seconds=i % 60)).isoformat()}
                    for i in range(lines_per_minute)
                ]
            )
            minute[0] += 1
            server.volume.tick(server, now=t0 + timedelta(minutes=minute[0]) + SETTLE)
        server.inbox.queue.clear()

    def warning(wid):
        return server.control.get("warning", wid) or {}

    play(20, 80)
    play(0, 5)
    w = warning("volume:h1|api|app")
    assert w["status"] == "open" and w["title"].startswith("No logs from api on h1 for 3 minutes")
    play(20, 5)
    assert warning("volume:h1|api|app")["status"] == "resolved"

    now = datetime.now(UTC)
    status = {
        "updated_at": now.isoformat(),
        "sources": [
            {"target": "/var/log/app.log", "scanned_at": now.isoformat()}
            | {"partial_since": (now - timedelta(minutes=5)).isoformat()}
        ],
    }
    server.control.put(
        "agent", "h1", {"hostname": "h1", "last_seen": now.isoformat(), "status": status}
    )
    play(0, 10)
    assert warning("volume:h1|api|app")["status"] == "resolved"
    w = warning("ingestion:h1")
    assert w["status"] == "open" and "waited for its newline" in w["detail"]

    server.control.update("agent", "h1", status={"updated_at": now.isoformat()})
    play(20, 1)
    assert warning("ingestion:h1")["status"] == "resolved"
    server.resolve_warning("volume:h1|api|app")  # resolving accepts the volume as normal
    assert server.volume.models["h1|api|app"].learning > 0


def test_a_malformed_agent_status_is_not_an_error():
    from logpipe.server.volume import ingestion_problems

    agent = {"online": True, "status": {"updated_at": "2026-11-24T10:00:00Z"}}
    agent["status"]["sources"] = [{"scanned_at": "yesterday"}, {"partial_since": 5}]
    assert ingestion_problems(agent) == []
    agent["status"]["updated_at"] = "2026-11-24T10:00:00"  # naive against aware
    agent["status"]["sources"] = [{"scanned_at": "2026-11-24T09:00:00Z"}]
    assert ingestion_problems(agent) == []
