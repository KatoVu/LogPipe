"""``logpipe server``: agent intake, the operator UI's API and the learning lifecycle."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from ..observability import Datadog
from ..settings import get_settings


class _Console(logging.Formatter):
    """One line per record; tracebacks only go to the log file."""

    def __init__(self, log_file: Path):
        super().__init__(datefmt="%H:%M:%S")
        self.log_file = log_file

    def format(self, record: logging.LogRecord) -> str:
        line = f"{self.formatTime(record, self.datefmt)} {record.levelname.lower()} {record.getMessage()}"
        if record.exc_info and record.exc_info[1] is not None:
            line += f": {record.exc_info[1]} (details in {self.log_file})"
        return line


class _Json(logging.Formatter):
    """One JSON object per line (``LOGPIPE_LOG_FORMAT=json``), for log shippers, logpipe's own
    agent included."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        out = {
            "ts": self.formatTime(record),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(data_dir: Path) -> Path:
    """Everything, with tracebacks, to ``data_dir/server.log``; warnings and errors as one
    line each to the console."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = data_dir / "server.log"
    to_file = logging.handlers.RotatingFileHandler(log_file, maxBytes=10 << 20, backupCount=3)
    to_file.setFormatter(
        _Json()
        if get_settings().server.log_format == "json"
        else logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(_Console(log_file))
    logging.basicConfig(level=logging.INFO, handlers=[to_file, console], force=True)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # every heartbeat and long-poll
    logging.captureWarnings(True)  # library warnings (torch, sklearn) go to the file too
    return log_file


def residency_check(queue: str, datadog: bool) -> None:
    """``--store remote`` promises the client that this host keeps none of their lines: refuse
    the parts that would copy them elsewhere."""
    if queue != "memory":
        raise SystemExit("--store remote needs --queue memory: Kafka would keep lines on this host")
    if datadog:
        raise SystemExit(
            "--store remote cannot run with Datadog (it would send lines and LLM prompts there): "
            "unset DD_API_KEY and drop --datadog"
        )
    if get_settings().server.draft_cache:
        raise SystemExit("--store remote cannot use LOGPIPE_DRAFT_CACHE: it saves sample lines")


def _remote_store():
    """The client's dataplane, once it has connected: the control docs load from it."""
    from .dataplane import RemoteStore, Tunnel

    cfg = get_settings().server
    tunnel = Tunnel(cfg.dataplane_key or "", port=cfg.dataplane_port, timeout=cfg.dataplane_timeout)
    print(f"[server] waiting for the client's dataplane on port {tunnel.port}", flush=True)
    tunnel.wait_connected()
    return RemoteStore(tunnel)


def run(
    *,
    host: str,
    port: int,
    store: str,
    queue: str,
    llm: str,
    data_dir: Path,
    epochs: int,
    datadog: Datadog | None = None,
    datadog_pull: str = "",
) -> None:
    import uvicorn

    from ..enrich.llm import get_adapter
    from ..storage import open_store
    from ..unresolved import open_queue
    from .app import create_app
    from .control import ControlStore
    from .core import Server
    from .stores import docs_for

    dd_settings = get_settings().datadog
    dd_query = datadog_pull or dd_settings.pull_query
    if dd_query and not (dd_settings.api_key and dd_settings.app_key):
        raise SystemExit("pulling Datadog logs needs DD_API_KEY and DD_APP_KEY in .env")
    dd = datadog or Datadog()
    if store == "remote":
        residency_check(queue, dd.enabled or bool(dd_settings.api_key))
    log_file = setup_logging(data_dir)
    log_store = _remote_store() if store == "remote" else open_store(store)
    control = ControlStore(docs_for(log_store))
    server = Server(
        log_store,
        control,
        data_dir=data_dir,
        epochs=epochs,
        adapter_factory=lambda: dd.traced(get_adapter(llm), llm),  # type: ignore[arg-type]
        queue_factory=lambda: open_queue(queue),
        bootstrap_key=get_settings().server.bootstrap_key or None,
        datadog=dd,
    )
    if dd_settings.api_key:
        from ..datadog_api import DatadogSync

        server.dd_sync = DatadogSync(dd_settings, query=dd_query)
        print(
            f"[server] datadog: events to {dd_settings.site}"
            + (f", pulling logs for {dd_query!r}" if dd_query else ""),
            flush=True,
        )
    from .auth import Auth

    generated = Auth(control).ensure(get_settings().server.admin_password or None)
    print(
        f"[server] logpipe on http://{host}:{port}/ ({store} store, {queue} queue, {llm}); "
        f"log: {log_file}",
        flush=True,
    )
    if generated:
        print(
            f"[server] admin password for the UI: {generated}  (the default: the first login "
            "asks for a new one; set LOGPIPE_ADMIN_PASSWORD to reset a forgotten one)",
            flush=True,
        )
    try:
        # SSE streams and agent long-polls never end on their own
        uvicorn.run(
            create_app(server, {"store": store, "queue": queue, "llm": llm}),
            host=host,
            port=port,
            log_config=None,
            timeout_graceful_shutdown=2,
        )
    finally:
        server.close()
