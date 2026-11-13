"""Parse Java sources with tree-sitter into class/method chunks plus structural facts.

Chunks carry a contextual header (file, package, Spring stereotype, annotations, endpoints and
outgoing calls) so that both the embedding model and BM25 see what the code *is*, not only
what it says. The structural facts feed the call/dependency graph.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from codeatlas.endpoints import join_paths, literal_path
from codeatlas.ingest.text import line_windows, split_lines
from codeatlas.models import Chunk, make_chunk_id

TYPE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
METHOD_TYPES = {"method_declaration", "constructor_declaration", "compact_constructor_declaration"}
FIELD_TYPES = {"field_declaration", "constant_declaration"}

HTTP_MAPPINGS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": None,
}
STEREOTYPES = {
    "RestController": "controller",
    "Controller": "controller",
    "ControllerAdvice": "controller-advice",
    "RestControllerAdvice": "controller-advice",
    "Service": "service",
    "Repository": "repository",
    "Component": "component",
    "Configuration": "configuration",
    "SpringBootApplication": "application",
    "Entity": "entity",
    "Embeddable": "entity",
    "MappedSuperclass": "entity",
    "FeignClient": "http-client",
}

_IMPORT = re.compile(r"import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;")
_REQUEST_METHOD = re.compile(r"RequestMethod\.(\w+)")
_FEIGN_PATH = re.compile(r"\bpath\s*=\s*\"([^\"]*)\"")
_GENERICS = re.compile(r"<.*>")


@dataclass
class MethodInfo:
    name: str
    signature: str
    start_line: int
    end_line: int
    is_constructor: bool = False
    annotations: list[str] = field(default_factory=list)
    calls: list[list[str | None]] = field(default_factory=list)  # [receiver type | None | "?", name]
    type_refs: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)  # served, e.g. "GET /orders/{id}"
    client_endpoints: list[str] = field(default_factory=list)  # called via a Feign client
    path_literals: list[str] = field(default_factory=list)  # HTTP paths in string literals


@dataclass
class TypeInfo:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    stereotype: str | None = None
    annotations: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # field name -> simple type
    type_refs: list[str] = field(default_factory=list)
    methods: list[MethodInfo] = field(default_factory=list)
    # Methods that exist at compile time but not in the source: record accessors and Lombok getters/setters.
    generated_methods: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypeInfo:
        data = dict(data)
        data["methods"] = [MethodInfo(**m) for m in data.get("methods", [])]
        return cls(**data)


@dataclass
class JavaFileFacts:
    path: str
    package: str
    imports: list[str] = field(default_factory=list)
    types: list[TypeInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JavaFileFacts:
        return cls(
            path=data["path"],
            package=data.get("package", ""),
            imports=list(data.get("imports", [])),
            types=[TypeInfo.from_dict(t) for t in data.get("types", [])],
        )


@dataclass
class _Ctx:
    path: str
    lines: list[str]
    facts: JavaFileFacts
    chunks: list[Chunk]
    max_lines: int


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(Language(tsjava.language()))


def parse_java(path: str, source: str, max_chunk_lines: int = 120) -> tuple[JavaFileFacts, list[Chunk]]:
    """Parse one Java file. Returns its structural facts and its chunks (empty if no types)."""
    root = _parser().parse(source.encode("utf-8")).root_node
    facts = JavaFileFacts(path=path, package="")
    for child in root.named_children:
        if child.type == "package_declaration":
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    facts.package = _text(c)
        elif child.type == "import_declaration":
            match = _IMPORT.search(_text(child))
            if match:
                facts.imports.append(match.group(1))

    ctx = _Ctx(path=path, lines=split_lines(source), facts=facts, chunks=[], max_lines=max_chunk_lines)
    for child in root.named_children:
        if child.type in TYPE_KINDS:
            _parse_type(ctx, child, outer=None)
    return facts, ctx.chunks


def simple_type(type_text: str) -> str:
    """'java.util.List<Owner>[]' -> 'List'."""
    t = _GENERICS.sub("", type_text).replace("[]", "").replace("...", "").strip()
    return t.split(".")[-1].strip()


# --- tree helpers -------------------------------------------------------------------------


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _child(node: Node, type_: str) -> Node | None:
    for c in node.children:
        if c.type == type_:
            return c
    return None


def _walk(node: Node, types: set[str], stop: frozenset[str] | set[str] = frozenset()) -> Iterator[Node]:
    """Yield descendants of the given types, not descending into nodes whose type is in stop."""
    stack = list(reversed(node.children))
    while stack:
        n = stack.pop()
        if n.type in types:
            yield n
        if n.type in stop:
            continue
        stack.extend(reversed(n.children))


def _string_value(node: Node) -> str:
    s = _text(node)
    if s.startswith('"""'):
        return s[3:-3].strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _type_list(node: Node) -> list[str]:
    out: list[str] = []
    for c in node.named_children:
        if c.type == "type_list":
            out.extend(simple_type(_text(t)) for t in c.named_children)
    return out


def _javadoc(node: Node) -> Node | None:
    prev = node.prev_named_sibling
    if (
        prev is not None
        and prev.type in ("block_comment", "comment")
        and _text(prev).startswith("/**")
        and prev.end_point[0] >= node.start_point[0] - 1
    ):
        return prev
    return None


def _signature(node: Node) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        raw = _text(node).rstrip(";")
    else:
        raw = node.text[: body.start_byte - node.start_byte].decode("utf-8", errors="replace")
    return " ".join(raw.split())


# --- annotations and endpoints -----------------------------------------------------------


def _annotations(modifiers: Node | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if modifiers is None:
        return out
    for child in modifiers.named_children:
        if child.type not in ("annotation", "marker_annotation"):
            continue
        out.append(
            {
                "name": simple_type(_text(child.child_by_field_name("name"))),
                "values": _annotation_values(child.child_by_field_name("arguments")),
                "raw": _text(child),
            }
        )
    return out


def _annotation_values(args: Node | None) -> list[str]:
    """String values of the annotation's default/value/path element."""
    if args is None:
        return []
    values: list[str] = []
    for child in args.named_children:
        if child.type == "element_value_pair":
            if _text(child.child_by_field_name("key")) not in ("value", "path"):
                continue
            target = child.child_by_field_name("value")
        else:
            target = child
        if target is None:
            continue
        nodes = [target] if target.type == "string_literal" else list(_walk(target, {"string_literal"}))
        values.extend(_string_value(n) for n in nodes)
    return values


def _mappings(annotations: list[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for a in annotations:
        if a["name"] not in HTTP_MAPPINGS:
            continue
        verb = HTTP_MAPPINGS[a["name"]]
        verbs = [verb] if verb else (_REQUEST_METHOD.findall(a["raw"]) or ["ANY"])
        for v in verbs:
            for p in a["values"] or [""]:
                out.append((v, p))
    return out


# --- types and methods -------------------------------------------------------------------


def _members(body: Node | None) -> list[Node]:
    if body is None:
        return []
    out: list[Node] = []
    for c in body.named_children:
        if c.type == "enum_body_declarations":
            out.extend(c.named_children)
        else:
            out.append(c)
    return out


def _parse_type(ctx: _Ctx, node: Node, outer: TypeInfo | None) -> None:
    name = _text(node.child_by_field_name("name")) or "Anonymous"
    if outer is not None:
        qn = f"{outer.qualified_name}.{name}"
    else:
        qn = f"{ctx.facts.package}.{name}" if ctx.facts.package else name

    annotations = _annotations(_child(node, "modifiers"))
    ann_names = [a["name"] for a in annotations]
    stereotype = next((STEREOTYPES[n] for n in ann_names if n in STEREOTYPES), None)

    extends: list[str] = []
    implements: list[str] = []
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        extends.extend(simple_type(_text(c)) for c in superclass.named_children)
    for c in node.named_children:
        if c.type == "super_interfaces":
            implements.extend(_type_list(c))
        elif c.type == "extends_interfaces":
            extends.extend(_type_list(c))

    body = node.child_by_field_name("body")
    members = _members(body)

    fields: dict[str, str] = {}
    for m in members:
        if m.type in FIELD_TYPES:
            ftype = simple_type(_text(m.child_by_field_name("type")))
            for d in m.named_children:
                if d.type == "variable_declarator":
                    fields[_text(d.child_by_field_name("name"))] = ftype
    generated: list[str] = []
    if node.type == "record_declaration":
        params = node.child_by_field_name("parameters")
        for p in params.named_children if params is not None else []:
            if p.type == "formal_parameter":
                component = _text(p.child_by_field_name("name"))
                fields[component] = simple_type(_text(p.child_by_field_name("type")))
                generated.append(component)  # record accessor: point.x()
    generated += _lombok_accessors(members, {a["name"] for a in annotations})

    base_paths = [""]
    for a in annotations:
        if a["name"] == "RequestMapping" and a["values"]:
            base_paths = a["values"]
        elif a["name"] == "FeignClient":
            match = _FEIGN_PATH.search(a["raw"])
            base_paths = [match.group(1)] if match else [""]

    doc = _javadoc(node)
    refs = {_text(t) for t in _walk(node, {"type_identifier"}, stop=set(TYPE_KINDS))}
    info = TypeInfo(
        name=name,
        qualified_name=qn,
        kind=TYPE_KINDS[node.type],
        start_line=_line(doc or node),
        end_line=_end_line(node),
        stereotype=stereotype,
        annotations=ann_names,
        extends=extends,
        implements=implements,
        fields=fields,
        type_refs=sorted(refs - {name, "var"}),
    )
    ctx.facts.types.append(info)

    nested: list[Node] = []
    for m in members:
        if m.type in METHOD_TYPES:
            info.methods.append(_parse_method(m, info, base_paths))
        elif m.type in TYPE_KINDS:
            nested.append(m)
    declared = {m.name for m in info.methods}  # an explicit method replaces the generated one
    info.generated_methods = [g for g in dict.fromkeys(generated) if g not in declared]

    _type_chunks(ctx, info, body, members)
    for method in info.methods:
        _method_chunks(ctx, info, method)
    for n in nested:
        _parse_type(ctx, n, outer=info)


_LOMBOK_GETTER = {"Data", "Getter", "Value"}
_LOMBOK_SETTER = {"Data", "Setter"}


def _lombok_accessors(members: list[Node], type_annotations: set[str]) -> list[str]:
    """Getter/setter names Lombok generates from @Data/@Value/@Getter/@Setter on the type or a field."""
    out: list[str] = []
    for m in members:
        if m.type != "field_declaration":
            continue
        modifiers = _child(m, "modifiers")
        words = set(_text(modifiers).split())
        if "static" in words:
            continue
        field_annotations = {a["name"] for a in _annotations(modifiers)}
        getter = bool((type_annotations & _LOMBOK_GETTER) or "Getter" in field_annotations)
        immutable = "final" in words or "Value" in type_annotations
        setter = not immutable and bool((type_annotations & _LOMBOK_SETTER) or "Setter" in field_annotations)
        primitive_boolean = _text(m.child_by_field_name("type")) == "boolean"
        for d in m.named_children:
            if d.type != "variable_declarator":
                continue
            name = _text(d.child_by_field_name("name"))
            cap = name[:1].upper() + name[1:]
            if getter:
                out.append(("is" if primitive_boolean else "get") + cap)
            if setter:
                out.append("set" + cap)
    return out


def _parse_method(node: Node, owner: TypeInfo, base_paths: list[str]) -> MethodInfo:
    is_ctor = node.type != "method_declaration"
    name = _text(node.child_by_field_name("name")) or owner.name
    annotations = _annotations(_child(node, "modifiers"))
    body = node.child_by_field_name("body")

    var_types = dict(owner.fields)
    params = node.child_by_field_name("parameters")
    for p in params.named_children if params is not None else []:
        if p.type == "formal_parameter":
            var_types[_text(p.child_by_field_name("name"))] = simple_type(_text(p.child_by_field_name("type")))
    if body is not None:
        nested_stop = set(TYPE_KINDS) | {"class_body"}
        for decl in _walk(body, {"local_variable_declaration"}, stop=nested_stop):
            vtype = simple_type(_text(decl.child_by_field_name("type")))
            if vtype == "var":
                continue
            for d in decl.named_children:
                if d.type == "variable_declarator":
                    var_types[_text(d.child_by_field_name("name"))] = vtype
        for loop in _walk(body, {"enhanced_for_statement"}, stop=nested_stop):
            vtype = simple_type(_text(loop.child_by_field_name("type")))
            vname = _text(loop.child_by_field_name("name"))
            if vname and vtype != "var":
                var_types[vname] = vtype

    endpoints = [f"{verb} {join_paths(base, p)}" for verb, p in _mappings(annotations) for base in base_paths]
    client_endpoints: list[str] = []
    if owner.stereotype == "http-client":
        client_endpoints, endpoints = endpoints, []

    doc = _javadoc(node)
    return MethodInfo(
        name=name,
        signature=_signature(node),
        start_line=_line(doc or node),
        end_line=_end_line(node),
        is_constructor=is_ctor,
        annotations=[a["name"] for a in annotations],
        calls=_calls(body, var_types) if body is not None else [],
        type_refs=sorted({_text(t) for t in _walk(node, {"type_identifier"})} - {"var"}),
        endpoints=endpoints,
        client_endpoints=client_endpoints,
        path_literals=_path_literals(body) if body is not None else [],
    )


def _calls(body: Node, var_types: dict[str, str]) -> list[list[str | None]]:
    """Outgoing calls as [receiver, method]. Receiver is a simple type name, None for calls on
    this/the same class, "super" for super calls and "?" when it cannot be resolved statically."""
    out: list[list[str | None]] = []
    seen: set[tuple[str | None, str]] = set()
    for inv in _walk(body, {"method_invocation"}):
        name = _text(inv.child_by_field_name("name"))
        obj = inv.child_by_field_name("object")
        recv: str | None
        if obj is None or obj.type == "this":
            recv = None
        elif obj.type == "super":
            recv = "super"
        elif obj.type == "identifier":
            ident = _text(obj)
            recv = var_types.get(ident) or (ident if ident[:1].isupper() else "?")
        elif obj.type == "field_access":
            inner = obj.child_by_field_name("object")
            fld = _text(obj.child_by_field_name("field"))
            recv = var_types.get(fld, "?") if inner is not None and inner.type == "this" else "?"
        else:
            recv = "?"
        key = (recv, name)
        if name and key not in seen:
            seen.add(key)
            out.append([recv, name])
    return out


def _path_literals(body: Node) -> list[str]:
    out: list[str] = []
    for s in _walk(body, {"string_literal"}):
        p = literal_path(_string_value(s))
        if p and p not in out:
            out.append(p)
    return out


# --- chunk construction ------------------------------------------------------------------


def _type_header(ctx: _Ctx, info: TypeInfo) -> list[str]:
    header = [f"File: {ctx.path}"]
    if ctx.facts.package:
        header.append(f"Package: {ctx.facts.package}")
    desc = f"{info.kind.capitalize()}: {info.qualified_name.removeprefix(ctx.facts.package + '.')}"
    if info.stereotype:
        desc += f" (Spring {info.stereotype})"
    header.append(desc)
    if info.annotations:
        header.append("Annotations: " + " ".join("@" + a for a in info.annotations))
    if info.extends:
        header.append("Extends: " + ", ".join(info.extends))
    if info.implements:
        header.append("Implements: " + ", ".join(info.implements))
    return header


def _type_chunks(ctx: _Ctx, info: TypeInfo, body: Node | None, members: list[Node]) -> None:
    """A skeleton of the type: declaration, fields and member signatures with line numbers."""
    header_end = _line(body) if body is not None else info.end_line
    decl = textwrap.dedent("\n".join(ctx.lines[info.start_line - 1 : header_end])).rstrip()
    member_lines: list[str] = []
    for m in members:
        if m.type in FIELD_TYPES:
            member_lines.append(" ".join(_text(m).split()))
        elif m.type in METHOD_TYPES:
            member_lines.append(f"{_signature(m)};  // line {_line(m)}")
        elif m.type in TYPE_KINDS:
            nested_name = _text(m.child_by_field_name("name"))
            member_lines.append(f"{TYPE_KINDS[m.type]} {nested_name} {{ ... }}  // line {_line(m)}")
        elif m.type == "enum_constant":
            member_lines.append(_text(m.child_by_field_name("name")) + ",")
    skeleton = decl + "\n" + "\n".join("    " + line for line in member_lines) + "\n}"

    header = "\n".join(_type_header(ctx, info))
    pieces = line_windows(skeleton.splitlines(), info.start_line, ctx.max_lines)
    metadata = {
        "package": ctx.facts.package,
        "type_kind": info.kind,
        "stereotype": info.stereotype,
        "annotations": info.annotations,
        "extends": info.extends,
        "implements": info.implements,
    }
    for part, (_, _, text) in enumerate(pieces):
        ctx.chunks.append(
            Chunk(
                id=make_chunk_id(ctx.path, "class", info.qualified_name, info.start_line, part),
                path=ctx.path,
                kind="class",
                text=text,
                embed_text=f"{header}\n\n{text}",
                start_line=info.start_line,
                end_line=info.end_line,
                language="java",
                symbol=info.qualified_name,
                metadata=metadata,
            )
        )


def _method_chunks(ctx: _Ctx, info: TypeInfo, method: MethodInfo) -> None:
    header_lines = _type_header(ctx, info)
    header_lines.append(f"{'Constructor' if method.is_constructor else 'Method'}: {method.signature}")
    if method.endpoints:
        header_lines.append("Endpoints: " + ", ".join(method.endpoints))
    if method.client_endpoints:
        header_lines.append("Calls HTTP endpoints: " + ", ".join(method.client_endpoints))
    if method.path_literals:
        header_lines.append("HTTP paths referenced: " + ", ".join(method.path_literals))
    calls = [f"{recv or info.name}.{name}" for recv, name in method.calls if recv != "?"]
    if calls:
        header_lines.append("Calls: " + ", ".join(calls[:20]))
    header = "\n".join(header_lines)

    symbol = f"{info.qualified_name}#{method.name}"
    lines = ctx.lines[method.start_line - 1 : method.end_line]
    pieces = line_windows(lines, method.start_line, ctx.max_lines, overlap=10)
    metadata = {
        "package": ctx.facts.package,
        "type": info.qualified_name,
        "type_kind": info.kind,
        "stereotype": info.stereotype,
        "annotations": method.annotations,
        "type_annotations": info.annotations,
        "signature": method.signature,
        "endpoints": method.endpoints,
        "client_endpoints": method.client_endpoints,
        "path_literals": method.path_literals,
        "calls": calls,
    }
    for part, (start, end, text) in enumerate(pieces):
        body = textwrap.dedent(text)
        part_header = header + (f"\nPart: {part + 1}/{len(pieces)}" if len(pieces) > 1 else "")
        ctx.chunks.append(
            Chunk(
                id=make_chunk_id(ctx.path, "method", symbol, start, part),
                path=ctx.path,
                kind="method",
                text=body,
                embed_text=f"{part_header}\n\n{body}",
                start_line=start,
                end_line=end,
                language="java",
                symbol=symbol,
                metadata=metadata,
            )
        )
