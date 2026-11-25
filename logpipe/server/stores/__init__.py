"""Log store adapters: each backend's UI reads and query compiler behind one interface
(:class:`~.base.StoreReads`, :class:`~.base.Compiler`).

The one place that maps a store to what the server builds on it: its reads, its metrics series
and its control-docs sink. A new backend adds a branch to :func:`_kind` and to each function."""

from __future__ import annotations

from .base import FACETS, Compiler, StoreReads, level


def _kind(store) -> str:
    from ...storage.memory import MemoryStore

    if isinstance(store, MemoryStore):
        return "memory"
    if type(store).__name__ == "MongoStore":
        return "mongodb"
    if hasattr(store, "client"):  # the ClickHouse store; MongoStore has none on purpose
        return "clickhouse"
    raise ValueError(f"no reads for store {type(store).__name__}")


def reads_for(store) -> StoreReads:
    kind = _kind(store)
    if kind == "memory":
        from .memory import MemoryReads

        return MemoryReads(store)
    if kind == "mongodb":
        from .mongo import MongoReads

        return MongoReads(store)
    from .clickhouse import ClickHouseReads

    return ClickHouseReads(store)


def series_for(store):
    """Where the server's own metrics samples go."""
    from ..metrics import ClickHouseSeries, MemorySeries, MongoSeries

    kind = _kind(store)
    if kind == "memory":
        return MemorySeries()
    return MongoSeries(store.db) if kind == "mongodb" else ClickHouseSeries(store.client)


def docs_for(store):
    """The control plane's sink, or None to keep control docs in memory only."""
    kind = _kind(store)
    if kind == "memory":
        # shortcut: the memory store keeps control docs in memory too; agents, keys and models are lost on restart.
        return None
    if kind == "mongodb":
        from ...storage.mongo import MongoDocs

        return MongoDocs(store.db)
    from ..control import ClickHouseDocs

    return ClickHouseDocs(store.client)


__all__ = ["FACETS", "Compiler", "StoreReads", "docs_for", "level", "reads_for", "series_for"]
