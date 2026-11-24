"""Where the component graph lives: Neo4j (``NEO4J_URI``), or memory for tests, evals and a
server without Neo4j. Both answer the same traversals; nothing here takes a query from outside,
each method runs one fixed, parameterised statement.

The graph is small (one node per component, tens to hundreds of edges) and is derived again from
the template labels whenever they change, so ``replace`` swaps it whole.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Protocol

MAX_DEPTH = 6


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    relation: str = "depends_on"
    templates: tuple[int, ...] = ()  # the evidence: templates that show this link
    by: str = "llm"  # llm | rules | manual
    status: tuple[str, ...] = ()  # what the evidence says about the link (timeout, refused, ...)

    @property
    def key(self) -> str:
        return f"{self.src}>{self.dst}>{self.relation}"

    def to_dict(self) -> dict:
        return asdict(self) | {"id": self.key}


class GraphStore(Protocol):
    def replace(self, edges: Iterable[Edge]) -> None: ...

    def edges(self) -> list[Edge]: ...

    def downstream(self, node: str, depth: int = 2) -> dict[str, int]:
        """What ``node`` depends on, directly or through others: component -> hops."""
        ...

    def upstream(self, node: str, depth: int = 2) -> dict[str, int]:
        """What depends on ``node`` (what breaks when it fails): component -> hops."""
        ...

    def path(self, a: str, b: str) -> list[Edge]:
        """The shortest chain of edges linking ``a`` and ``b`` in either direction ([] if none)."""
        ...

    def close(self) -> None: ...


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
        return self._walk(node, depth, True)

    def upstream(self, node: str, depth: int = 2) -> dict[str, int]:
        return self._walk(node, depth, False)

    def path(self, a: str, b: str) -> list[Edge]:
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


class Neo4jGraph:
    """Components are ``(:Component {name})``; an edge is ``[:LINK {relation, templates, by,
    status}]`` (one relationship type, so the relation can be a parameter)."""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None):
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database
        self._run(
            "CREATE CONSTRAINT component_name IF NOT EXISTS FOR (c:Component) REQUIRE c.name IS UNIQUE"
        )

    def _run(self, cypher: str, **params) -> list:
        records, _, _ = self._driver.execute_query(cypher, params, database_=self._db)
        return records

    def replace(self, edges: Iterable[Edge]) -> None:
        rows = [
            asdict(e) | {"templates": list(e.templates), "status": list(e.status)} for e in edges
        ]
        with self._driver.session(database=self._db) as s:
            s.execute_write(self._replace, rows)

    @staticmethod
    def _replace(tx, rows: list[dict]) -> None:
        tx.run("MATCH ()-[r:LINK]->() DELETE r")
        tx.run(
            "UNWIND $rows AS e "
            "MERGE (a:Component {name: e.src}) MERGE (b:Component {name: e.dst}) "
            "CREATE (a)-[:LINK {relation: e.relation, templates: e.templates, by: e.by, status: e.status}]->(b)",
            rows=rows,
        )
        tx.run("MATCH (c:Component) WHERE NOT (c)--() DELETE c")

    def edges(self) -> list[Edge]:
        recs = self._run(
            "MATCH (a:Component)-[r:LINK]->(b:Component) "
            "RETURN a.name AS src, b.name AS dst, r.relation AS relation, r.templates AS templates, "
            "r.by AS by, r.status AS status"
        )
        return sorted((_edge(r) for r in recs), key=lambda e: e.key)

    def _walk(self, node: str, depth: int, forward: bool) -> dict[str, int]:
        arrow = "-[:LINK*1..%d]->" if forward else "<-[:LINK*1..%d]-"
        recs = self._run(
            f"MATCH p = (a:Component {{name: $node}}){arrow % _depth(depth)}(b:Component) "
            "WHERE b.name <> $node RETURN b.name AS name, min(length(p)) AS hops",
            node=node,
        )
        return {r["name"]: r["hops"] for r in recs}

    def downstream(self, node: str, depth: int = 2) -> dict[str, int]:
        return self._walk(node, depth, True)

    def upstream(self, node: str, depth: int = 2) -> dict[str, int]:
        return self._walk(node, depth, False)

    def path(self, a: str, b: str) -> list[Edge]:
        if a == b:
            return []
        recs = self._run(
            "MATCH (a:Component {name: $a}), (b:Component {name: $b}) "
            f"MATCH p = shortestPath((a)-[:LINK*..{MAX_DEPTH}]-(b)) "
            "UNWIND relationships(p) AS r "
            "RETURN startNode(r).name AS src, endNode(r).name AS dst, r.relation AS relation, "
            "r.templates AS templates, r.by AS by, r.status AS status",
            a=a,
            b=b,
        )
        return [_edge(r) for r in recs]

    def close(self) -> None:
        self._driver.close()


def _edge(r) -> Edge:
    return Edge(
        r["src"],
        r["dst"],
        r["relation"],
        tuple(r["templates"] or ()),
        r["by"],
        tuple(r["status"] or ()),
    )


def open_graph(uri: str | None, user: str = "neo4j", password: str = "") -> GraphStore:
    return Neo4jGraph(uri, user, password) if uri else MemoryGraph()
