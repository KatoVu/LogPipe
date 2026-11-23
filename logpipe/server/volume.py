"""Per-source log volume alerts, and the ingestion problems that would fake them.

Lines are counted per source by their own time (the agent stamps them as it reads them), so
shipping batches and Datadog pulls don't leave a minute looking empty; a minute is judged once it
is ``SETTLE`` old, by the source's VolumeModel (``logpipe.anomaly.volume``).

A host whose agent stopped reporting in, holds a backlog it couldn't send, has a file reader that
stopped scanning or a line waiting for its newline gets an ``ingestion`` warning instead of volume
alerts: its sources are neither judged nor learned until ``GRACE`` after it is healthy again, since
a reconnecting agent sends its backlog in a burst. The same holds for everyone after the server
itself starts or stalls.
"""

from __future__ import annotations

import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

from ..anomaly.volume import MINUTE, Change, VolumeModel
from .control import ControlStore

SETTLE = timedelta(minutes=2)
GRACE = timedelta(minutes=5)
STUCK = timedelta(seconds=60)
SAVE_EVERY = 10  # judged minutes between saving the models


def _minute(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(second=0, microsecond=0)


def ingestion_problems(agent: dict) -> list[str]:
    """What keeps this agent's logs from arriving on time, judged by its own clock."""
    if not agent.get("online"):
        return [f"its agent stopped reporting in (last seen {agent.get('last_seen', 'never')})"]
    status = agent.get("status") or {}
    out = []
    shipper = status.get("shipper") or {}
    if shipper.get("buffer_bytes"):
        why = f": {shipper['last_error']}" if shipper.get("last_error") else ""
        out.append(f"{shipper['buffer_bytes'] // 1024} KB of logs are waiting to be sent{why}")
    try:  # an agent's status is its own word: a malformed time must not stop the checks
        now = datetime.fromisoformat(status["updated_at"])
        for s in status.get("sources") or []:
            if s.get("scanned_at") and now - datetime.fromisoformat(s["scanned_at"]) > STUCK:
                out.append(f"the reader for {s.get('target')} has not run since {s['scanned_at']}")
            if s.get("partial_since") and now - datetime.fromisoformat(s["partial_since"]) > STUCK:
                out.append(
                    f"a line in {s.get('target')} has waited for its newline since {s['partial_since']}"
                )
    except (KeyError, TypeError, ValueError):
        pass
    return out


class Volume:
    def __init__(self, control: ControlStore, now: datetime | None = None):
        now = now or datetime.now(UTC)
        self.control = control
        self._lock = threading.Lock()
        self._counts: dict[datetime, Counter[str]] = {}
        self._next = _minute(now) + MINUTE  # the minute the server started in is partial
        self._last_tick = now
        self._resume = now + GRACE
        self._held: dict[str, datetime] = {}  # host -> judged again from
        self._unhealthy = {
            w["subject"]
            for w in control.all("warning")
            if w["kind"] == "ingestion" and w["status"] == "open"
        }
        self.models = {d["id"]: VolumeModel.from_dict(d) for d in control.all("volume")}
        self._unsaved = 0

    def count(self, events: list[tuple[str, datetime | None]], now: datetime | None = None) -> None:
        """``(source id, the line's own time)`` for each line taken in."""
        current = _minute(now or datetime.now(UTC))
        with self._lock:
            for sid, at in events:
                minute = _minute(at) if at and at.tzinfo else current
                if not self._next <= minute <= current:  # already judged, or a skewed clock
                    minute = current
                self._counts.setdefault(minute, Counter())[sid] += 1

    def tick(self, server, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        with self._lock:
            if now - self._last_tick > SETTLE:  # the server stalled: its counts are not fair
                self._resume = now + GRACE
            self._last_tick = now
            due = []
            while self._next + MINUTE + SETTLE <= now:
                due.append((self._next, self._counts.pop(self._next, Counter())))
                self._next += MINUTE
        if not due:
            return
        held = self._check_ingestion(server, now)
        changes = []
        with self._lock:
            for minute, counts in due:
                for sid in {*self.models, *counts}:
                    model = self.models.setdefault(sid, VolumeModel())
                    if now < self._resume or sid.partition("|")[0] in held:
                        model.pause()
                    elif change := model.observe(counts.get(sid, 0), minute):
                        changes.append((sid, change))
            self._unsaved += len(due)
        for sid, change in changes:
            self._report(server, sid, change)
        if self._unsaved >= SAVE_EVERY:
            self.save()

    def _check_ingestion(self, server, now: datetime) -> set[str]:
        for agent in server.agents():
            host = agent["hostname"]
            problems = ingestion_problems(agent)
            wid = f"ingestion:{host}"
            if problems:
                self._held[host] = now + GRACE
                detail = (
                    "; ".join(problems).capitalize()
                    + ". Volume alerts for this host wait until its logs arrive again."
                )
                doc = self.control.get("warning", wid)
                if host not in self._unhealthy or doc is None:
                    server._warn(
                        "ingestion",
                        host,
                        f"Logs from {host} are not arriving",
                        detail,
                        self._streams(server, host),
                    )
                elif doc["status"] == "open" and doc["detail"] != detail:
                    self.control.update("warning", wid, detail=detail)
                self._unhealthy.add(host)
            elif host in self._unhealthy:
                self._unhealthy.discard(host)
                self._resolve(wid, "logs arriving again")
        return {h for h, until in self._held.items() if until > now}

    @staticmethod
    def _streams(server, host: str) -> list[str]:
        return sorted(
            {
                s["id"]
                for sid, src in list(server.sources.items())
                if src["host"] == host
                for s in server.streams_for(sid)
            }
        )

    def _report(self, server, sid: str, change: Change) -> None:
        wid = f"volume:{sid}"
        if change.kind == "recovered":
            self._resolve(wid, "back to its usual volume")
            return
        host, service, source = (sid.split("|", 2) + ["", ""])[:3]
        where = f"{service or source or host} on {host}"
        expected = f"about {change.expected:.3g} expected a minute"
        if change.silent_minutes:
            title = f"No logs from {where} for {change.silent_minutes} minutes"
            detail = f"Nothing at all, {expected}."
            if host in {a["hostname"] for a in server.agents()}:
                detail += (
                    " Its agent is reporting in and reading its files, so the service stopped"
                    " logging: it is down, hung, or now logs somewhere else."
                )
        else:
            title = f"{where} is logging {'far less' if change.kind == 'drop' else 'far more'} than usual"
            detail = f"{change.count} lines in the last minute, {expected}."
        doc = self.control.get("warning", wid)
        if doc is not None and doc["status"] == "open":
            self.control.update("warning", wid, title=title, detail=detail)
        else:
            server._warn("volume", sid, title, detail, [s["id"] for s in server.streams_for(sid)])

    def _resolve(self, wid: str, note: str) -> None:
        doc = self.control.get("warning", wid)
        if doc is not None and doc["status"] == "open":
            self.control.update(
                "warning",
                wid,
                status="resolved",
                resolution=note,
                resolved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )

    def accept(self, sid: str) -> None:
        """A person resolved the source's volume warning: its current volume is normal."""
        with self._lock:
            model = self.models.get(sid)
            if model is None:
                return
            model.rebase()
            doc = model.to_dict()
        self.control.put("volume", sid, doc)

    def save(self) -> None:
        # shortcut: one control doc per source each SAVE_EVERY minutes; batch the writes if
        # thousands of sources make that a burden on the control store.
        with self._lock:
            docs = {sid: m.to_dict() for sid, m in self.models.items()}
            self._unsaved = 0
        for sid, doc in docs.items():
            self.control.put("volume", sid, doc)
