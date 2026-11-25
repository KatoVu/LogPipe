"""Where the component graph lives: in memory. It's derived from control docs (labels, edits,
traced links), so a restart rebuilds it and nothing here needs a database.

The graph is small (one node per component, tens to hundreds of edges) and is derived again from
the template labels whenever they change, so ``replace`` swaps it whole.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

MAX_DEPTH = 6


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    relation: str = "depends_on"
    templates: tuple[int, ...] = ()  # the evidence: templates that show this link
    by: str = "llm"  # llm | rules | manual | traces
    status: tuple[str, ...] = ()  # what the evidence says about the link (timeout, refused, ...)

    @property
    def key(self) -> str:
        return f"{self.src}>{self.dst}>{self.relation}"

    def to_dict(self) -> dict:
        return asdict(self) | {"id": self.key}


def _depth(depth: int) -> int:
    return max(1, min(int(depth), MAX_DEPTH))


@dataclass
class MemoryGraph:
    _edges: dict[str, Edge] = field(default_factory=dict)

    def replace(self, edges: Iterable[Edge]) -> None:
        self._edges = {e.key: e for e in edges}

    def edges(self) -> list[Edge]:
        return sorted(self._edges.values(), key=lambda e: e.key)

    def _walk(self, node: str, depth: int, forward: bool) -> dict[str, int]:
        nxt: dict[str, set[str]] = {}
        for e in self._edges.values():
            a, b = (e.src, e.dst) if forward else (e.dst, e.src)
            nxt.setdefault(a, set()).add(b)
        seen, todo = {node: 0}, deque([node])
        while todo:
            cur = todo.popleft()
            if seen[cur] >= _depth(depth):
                continue
            for n in sorted(nxt.get(cur, ())):
                if n not in seen:
                    seen[n] = seen[cur] + 1
                    todo.append(n)
        seen.pop(node)
        return seen

    def downstream(self, node: str, depth: int = 2) -> dict[str, int]:
        """What ``node`` depends on, directly or through others: component -> hops."""
        return self._walk(node, depth, True)

    def upstream(self, node: str, depth: int = 2) -> dict[str, int]:
        """What depends on ``node`` (what breaks when it fails): component -> hops."""
        return self._walk(node, depth, False)

    def path(self, a: str, b: str) -> list[Edge]:
        """The shortest chain of edges linking ``a`` and ``b`` in either direction ([] if none)."""
        links: dict[str, list[tuple[str, Edge]]] = {}
        for e in self.edges():
            links.setdefault(e.src, []).append((e.dst, e))
            links.setdefault(e.dst, []).append((e.src, e))
        prev: dict[str, tuple[str, Edge] | None] = {a: None}
        todo = deque([a])
        while todo and b not in prev:
            cur = todo.popleft()
            for n, e in links.get(cur, ()):
                if n not in prev:
                    prev[n] = (cur, e)
                    todo.append(n)
        if b not in prev or a == b:
            return []
        out, cur = [], b
        while (step := prev[cur]) is not None:
            cur, e = step
            out.append(e)
        return out[::-1]

    def close(self) -> None:
        pass
