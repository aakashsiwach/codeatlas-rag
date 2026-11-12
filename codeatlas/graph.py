"""Static call/dependency graph built from parsed Java facts.

Edges point from the dependent to the dependency (A -> B means "A depends on B"), so the impact
of changing B is everything that can reach B: a reverse breadth-first search.

Relations:
  calls           method -> method
  implemented_by  interface/superclass method -> overriding method (dynamic dispatch)
  extends         type -> supertype
  implements      type -> interface
  field           type -> type of one of its fields (typically an injected Spring bean)
  uses            type/method -> any other type it references
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import networkx as nx

from codeatlas.endpoints import endpoint_path, paths_match
from codeatlas.ingest.java import JavaFileFacts, TypeInfo

IMPACT_INTENT = re.compile(
    r"\b(what (would )?(break|breaks|changes|is affected|depends)|impact|affected|blast radius"
    r"|who (calls|uses)|which \w+ (call|calls|use|uses|depend)|callers?|called (by|from)"
    r"|depend(s|ency|encies|ents)? on|usages?|used by|references? to)\b",
    re.IGNORECASE,
)
_MENTION = re.compile(r"\b([A-Z][A-Za-z0-9_]*)(?:[.#]([a-z_][A-Za-z0-9_]*))?|\b([a-z]+[A-Z][A-Za-z0-9_]*)\b")
_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b")
_CALL_RELATIONS = {"calls", "implemented_by"}


@dataclass
class ImpactHit:
    node: str
    depth: int
    relation: str
    via: str
    kind: str
    path: str | None
    line: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodeGraph:
    def __init__(self) -> None:
        self.g = nx.DiGraph()
        self._by_simple: dict[str, list[str]] = {}
        self._by_lower: dict[str, list[str]] = {}
        self._methods_by_name: dict[str, list[str]] = {}
        self._members: dict[str, list[str]] = {}

    # --- construction ----------------------------------------------------------------------

    @classmethod
    def build(cls, facts: Iterable[JavaFileFacts]) -> CodeGraph:
        graph = cls()
        files = list(facts)
        types: dict[str, tuple[TypeInfo, JavaFileFacts]] = {}

        for f in files:
            for t in f.types:
                types[t.qualified_name] = (t, f)
                graph._by_simple.setdefault(t.name, []).append(t.qualified_name)
                graph._by_lower.setdefault(t.name.lower(), []).append(t.qualified_name)
                short = t.qualified_name.removeprefix(f.package + ".") if f.package else t.qualified_name
                graph.g.add_node(
                    t.qualified_name,
                    kind="type",
                    short=short,
                    type_kind=t.kind,
                    stereotype=t.stereotype,
                    path=f.path,
                    line=t.start_line,
                )
                for m in t.methods:
                    nid = f"{t.qualified_name}#{m.name}"
                    if nid in graph.g:  # overloads share one node
                        attrs = graph.g.nodes[nid]
                        attrs["endpoints"] = attrs["endpoints"] + m.endpoints
                        attrs["client_endpoints"] = attrs["client_endpoints"] + m.client_endpoints
                        attrs["path_literals"] = attrs["path_literals"] + m.path_literals
                        continue
                    graph.g.add_node(
                        nid,
                        kind="method",
                        short=f"{short}#{m.name}",
                        owner=t.qualified_name,
                        path=f.path,
                        line=m.start_line,
                        endpoints=list(m.endpoints),
                        client_endpoints=list(m.client_endpoints),
                        path_literals=list(m.path_literals),
                    )
                    graph._members.setdefault(t.qualified_name, []).append(nid)
                    graph._methods_by_name.setdefault(m.name, []).append(nid)

        def resolve(simple: str, f: JavaFileFacts, ctx: TypeInfo) -> str | None:
            candidates = graph._by_simple.get(simple)
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            nested = f"{ctx.qualified_name}.{simple}"
            if nested in candidates:
                return nested
            for c in candidates:
                if c in f.imports or c == f"{f.package}.{simple}":
                    return c
                if any(imp.endswith(".*") and c == f"{imp[:-2]}.{simple}" for imp in f.imports):
                    return c
            return candidates[0]

        def find_method(type_qn: str, name: str, seen: frozenset[str] = frozenset()) -> str | None:
            nid = f"{type_qn}#{name}"
            if nid in graph.g:
                return nid
            if type_qn not in types or type_qn in seen:
                return None
            t, f = types[type_qn]
            for sup in t.extends + t.implements:
                sq = resolve(sup, f, t)
                found = find_method(sq, name, seen | {type_qn}) if sq else None
                if found:
                    return found
            return None

        for qn, (t, f) in types.items():
            for relation, names in (("extends", t.extends), ("implements", t.implements)):
                for n in names:
                    target = resolve(n, f, t)
                    if target and target != qn:
                        graph._edge(qn, target, relation)
            field_types = set(t.fields.values())
            for n in t.type_refs:
                target = resolve(n, f, t)
                if target and target != qn and not target.startswith(qn + "."):
                    graph._edge(qn, target, "field" if n in field_types else "uses")
            for m in t.methods:
                src = f"{qn}#{m.name}"
                for recv, name in m.calls:
                    if recv == "?" or not name:
                        continue
                    if recv is None:
                        owner = qn
                    elif recv == "super":
                        owner = resolve(t.extends[0], f, t) if t.extends else None
                    else:
                        owner = resolve(recv, f, t)
                    target = find_method(owner, name) if owner else None
                    if target and target != src:
                        graph._edge(src, target, "calls")
                for n in m.type_refs:
                    target = resolve(n, f, t)
                    if target and target != qn:
                        graph._edge(src, target, "uses")

        # Dynamic dispatch: a call to an interface method may run any implementation.
        for qn, (t, f) in types.items():
            for sup in t.extends + t.implements:
                sq = resolve(sup, f, t)
                if not sq or sq not in types:
                    continue
                for m in types[sq][0].methods:
                    impl = f"{qn}#{m.name}"
                    if impl in graph.g:
                        graph._edge(f"{sq}#{m.name}", impl, "implemented_by")
        return graph

    def _edge(self, src: str, dst: str, relation: str) -> None:
        if not self.g.has_edge(src, dst):
            self.g.add_edge(src, dst, relation=relation)

    # --- queries ---------------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        kinds = [a.get("kind") for _, a in self.g.nodes(data=True)]
        return {
            "types": kinds.count("type"),
            "methods": kinds.count("method"),
            "edges": self.g.number_of_edges(),
        }

    def short(self, node: str) -> str:
        return self.g.nodes[node].get("short", node) if node in self.g else node

    def resolve_mentions(self, text: str, limit: int = 8) -> list[str]:
        """Graph nodes mentioned in free text: 'Customer', 'OrderService.placeOrder', 'placeOrder'."""
        found: list[str] = []

        def add(node: str) -> None:
            if node in self.g and node not in found:
                found.append(node)

        for match in _MENTION.finditer(text):
            cls_name, method, bare = match.groups()
            if cls_name and cls_name in self._by_simple:
                for qn in self._by_simple[cls_name]:
                    if method and f"{qn}#{method}" in self.g:
                        add(f"{qn}#{method}")
                    else:
                        add(qn)
            elif bare and bare in self._methods_by_name:
                for nid in self._methods_by_name[bare][:5]:
                    add(nid)
        if not found:  # fall back to case-insensitive type names ("the customer entity")
            for word in _WORD.findall(text):
                for qn in self._by_lower.get(word.lower(), []):
                    add(qn)
        return found[:limit]

    def endpoint_roles(self, paths: Iterable[str]) -> dict[str, list[str]]:
        """Methods that serve, call (via Feign) or reference (via string literals) the given paths."""
        paths = list(paths)
        roles: dict[str, list[str]] = {"serves": [], "http_clients": [], "literal_callers": []}
        for node, attrs in self.g.nodes(data=True):
            if attrs.get("kind") != "method":
                continue
            if any(paths_match(p, endpoint_path(e)) for p in paths for e in attrs.get("endpoints", [])):
                roles["serves"].append(node)
            if any(paths_match(p, endpoint_path(e)) for p in paths for e in attrs.get("client_endpoints", [])):
                roles["http_clients"].append(node)
            if any(
                paths_match(p, lit, prefix=lit.endswith("/"))
                for p in paths
                for lit in attrs.get("path_literals", [])
            ):
                roles["literal_callers"].append(node)
        return roles

    def neighbors(self, node: str, limit: int = 4) -> list[str]:
        """Direct callers and callees of a method (including dispatch edges)."""
        if node not in self.g:
            return []
        out: list[str] = []
        for pred in self.g.predecessors(node):
            if self.g.edges[pred, node].get("relation") in _CALL_RELATIONS:
                out.append(pred)
        for succ in self.g.successors(node):
            if self.g.edges[node, succ].get("relation") in _CALL_RELATIONS:
                out.append(succ)
        return out[:limit]

    def impact(self, targets: Iterable[str], depth: int = 3, limit: int = 60) -> list[ImpactHit]:
        """Everything that transitively depends on the targets, nearest first."""
        start: set[str] = set()
        for t in targets:
            if t not in self.g:
                continue
            start.add(t)
            if self.g.nodes[t].get("kind") == "type":
                start.update(self._members.get(t, []))
        seen = set(start)
        frontier = sorted(start)
        hits: list[ImpactHit] = []
        for d in range(1, depth + 1):
            next_frontier: list[str] = []
            for node in frontier:
                for pred in sorted(self.g.predecessors(node)):
                    if pred in seen:
                        continue
                    seen.add(pred)
                    attrs = self.g.nodes[pred]
                    hits.append(
                        ImpactHit(
                            node=pred,
                            depth=d,
                            relation=self.g.edges[pred, node].get("relation", ""),
                            via=node,
                            kind=attrs.get("kind", ""),
                            path=attrs.get("path"),
                            line=attrs.get("line"),
                        )
                    )
                    next_frontier.append(pred)
            frontier = next_frontier
            if not frontier or len(hits) >= limit:
                break
        return hits[:limit]

    def describe(
        self,
        targets: list[str],
        hits: list[ImpactHit],
        endpoint_paths: list[str] | None = None,
        roles: dict[str, list[str]] | None = None,
    ) -> str:
        """Plain-text impact report that is handed to the LLM as a citable context block."""

        def loc(node: str) -> str:
            a = self.g.nodes[node]
            return f"{a.get('path')}:{a.get('line')}"

        lines = ["Static call/dependency graph analysis (does not see reflection, events or config wiring)."]
        if endpoint_paths and roles:
            lines.append(f"HTTP path {', '.join(endpoint_paths)}:")
            labels = {
                "serves": "served by",
                "http_clients": "called through HTTP client",
                "literal_callers": "referenced as a URL string in",
            }
            for role, label in labels.items():
                for node in roles.get(role, []):
                    lines.append(f"  - {label}: {self.short(node)} ({loc(node)})")
        if targets:
            lines.append("Targets: " + ", ".join(f"{self.short(t)} ({loc(t)})" for t in targets))
        if not hits:
            lines.append("No dependents found in the indexed code.")
        for depth in sorted({h.depth for h in hits}):
            label = "Direct dependents" if depth == 1 else f"Indirect dependents (depth {depth})"
            lines.append(f"{label}:")
            for hit in hits:
                if hit.depth != depth:
                    continue
                lines.append(
                    f"  - {self.short(hit.node)} [{hit.kind}] {hit.relation} -> {self.short(hit.via)}"
                    f" ({hit.path}:{hit.line})"
                )
        return "\n".join(lines)
