"""Canonical code knowledge graph element.

``CodeKnowledgeGraph`` is a fact-producing composition of source, AST, Git,
LSP and graph-store ports.  It deliberately does not import a project runtime
or decide which change should be made.  The small native parser in this module
is a provider implementation; callers may replace it with a richer AST/LSP
provider without changing the canonical element contract.
"""

from __future__ import annotations

import ast as python_ast
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from obase.element_contract import ElementContract, zero_authority

RELATION_TYPES = (
    "defines",
    "references",
    "calls",
    "imports",
    "inherits",
    "implements",
    "reads",
    "writes",
    "tests",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """Stable identity for a source symbol in one repository revision."""

    repo_id: str
    revision: str
    path: str
    language: str
    symbol: str
    symbol_kind: str
    qualified_name: str
    location: Mapping[str, Any] = field(default_factory=dict)

    @property
    def symbol_id(self) -> str:
        return _digest(
            {
                "repo_id": self.repo_id,
                "revision": self.revision,
                "path": self.path,
                "qualified_name": self.qualified_name,
                "symbol_kind": self.symbol_kind,
                "location": dict(self.location),
            }
        )[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "revision": self.revision,
            "path": self.path,
            "language": self.language,
            "symbol": self.symbol,
            "symbol_kind": self.symbol_kind,
            "qualified_name": self.qualified_name,
            "location": dict(self.location),
            "symbol_id": self.symbol_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SymbolRef:
        return cls(
            repo_id=str(data["repo_id"]),
            revision=str(data["revision"]),
            path=str(data["path"]),
            language=str(data.get("language", "unknown")),
            symbol=str(data.get("symbol", data.get("qualified_name", ""))),
            symbol_kind=str(data.get("symbol_kind", "unknown")),
            qualified_name=str(data.get("qualified_name", data.get("symbol", ""))),
            location=dict(data.get("location", {})),
        )


@dataclass(frozen=True, slots=True)
class SymbolNode:
    ref: SymbolRef
    signature: str = ""
    content_hash: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def symbol_id(self) -> str:
        return self.ref.symbol_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "signature": self.signature,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SymbolNode:
        return cls(
            ref=SymbolRef.from_dict(data["ref"]),
            signature=str(data.get("signature", "")),
            content_hash=str(data.get("content_hash", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class CodeRelation:
    source: SymbolRef
    target: SymbolRef
    relation_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"unsupported code relation: {self.relation_type!r}")

    @property
    def relation(self) -> str:
        """Compatibility alias for consumers that call the field ``relation``."""

        return self.relation_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "relation_type": self.relation_type,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CodeRelation:
        source = SymbolRef.from_dict(data["source"])
        target = SymbolRef.from_dict(data["target"])
        relation_type = str(data["relation_type"])
        metadata = dict(data.get("metadata", {}))
        edge_type = {
            "calls": CallEdge,
            "imports": DependencyEdge,
            "defines": DefinitionEdge,
            "references": ReferenceEdge,
        }.get(relation_type)
        if edge_type is not None:
            return edge_type(source, target, metadata)
        return cls(source, target, relation_type, metadata)


class _TypedEdge(CodeRelation):
    _relation_type = "references"

    def __init__(
        self,
        source: SymbolRef,
        target: SymbolRef,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(source, target, self._relation_type, metadata or {})


class CallEdge(_TypedEdge):
    _relation_type = "calls"


class DependencyEdge(_TypedEdge):
    _relation_type = "imports"


class DefinitionEdge(_TypedEdge):
    _relation_type = "defines"


class ReferenceEdge(_TypedEdge):
    _relation_type = "references"


@dataclass(frozen=True, slots=True)
class ImpactSet:
    root: SymbolRef
    symbols: tuple[SymbolRef, ...] = ()
    files: tuple[str, ...] = ()
    tests: tuple[SymbolRef, ...] = ()
    relations: tuple[CodeRelation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.to_dict(),
            "symbols": [item.to_dict() for item in self.symbols],
            "files": list(self.files),
            "tests": [item.to_dict() for item in self.tests],
            "relations": [item.to_dict() for item in self.relations],
        }


@dataclass(frozen=True, slots=True)
class CodePath:
    nodes: tuple[SymbolRef, ...] = ()
    relations: tuple[CodeRelation, ...] = ()
    complete: bool = False

    @property
    def symbols(self) -> tuple[SymbolRef, ...]:
        return self.nodes

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [item.to_dict() for item in self.nodes],
            "relations": [item.to_dict() for item in self.relations],
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    repo_id: str
    revision: str
    nodes: tuple[SymbolNode, ...] = ()
    relations: tuple[CodeRelation, ...] = ()
    source_digests: Mapping[str, str] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    schema_version: int = 1
    indexed_at: float = 0.0

    @property
    def snapshot_id(self) -> str:
        return _digest(
            {
                "repo_id": self.repo_id,
                "revision": self.revision,
                "nodes": [node.to_dict() for node in self.nodes],
                "relations": [relation.to_dict() for relation in self.relations],
                "source_digests": dict(self.source_digests),
            }
        )[:24]

    def node_map(self) -> dict[str, SymbolNode]:
        return {node.symbol_id: node for node in self.nodes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "revision": self.revision,
            "nodes": [node.to_dict() for node in self.nodes],
            "relations": [relation.to_dict() for relation in self.relations],
            "source_digests": dict(self.source_digests),
            "diagnostics": list(self.diagnostics),
            "schema_version": self.schema_version,
            "indexed_at": self.indexed_at,
            "snapshot_id": self.snapshot_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GraphSnapshot:
        return cls(
            repo_id=str(data["repo_id"]),
            revision=str(data["revision"]),
            nodes=tuple(SymbolNode.from_dict(item) for item in data.get("nodes", [])),
            relations=tuple(CodeRelation.from_dict(item) for item in data.get("relations", [])),
            source_digests=dict(data.get("source_digests", {})),
            diagnostics=tuple(str(item) for item in data.get("diagnostics", [])),
            schema_version=int(data.get("schema_version", 1)),
            indexed_at=float(data.get("indexed_at", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class GraphDelta:
    repo_id: str
    from_revision: str | None
    to_revision: str
    added_nodes: tuple[SymbolNode, ...] = ()
    removed_nodes: tuple[SymbolNode, ...] = ()
    changed_nodes: tuple[SymbolNode, ...] = ()
    added_relations: tuple[CodeRelation, ...] = ()
    removed_relations: tuple[CodeRelation, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(
            self.added_nodes
            or self.removed_nodes
            or self.changed_nodes
            or self.added_relations
            or self.removed_relations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "added_nodes": [node.to_dict() for node in self.added_nodes],
            "removed_nodes": [node.to_dict() for node in self.removed_nodes],
            "changed_nodes": [node.to_dict() for node in self.changed_nodes],
            "added_relations": [item.to_dict() for item in self.added_relations],
            "removed_relations": [item.to_dict() for item in self.removed_relations],
        }


@dataclass(frozen=True, slots=True)
class CodeContextSlice:
    root: SymbolRef
    symbols: tuple[SymbolNode, ...] = ()
    relations: tuple[CodeRelation, ...] = ()
    snippets: Mapping[str, str] = field(default_factory=dict)
    token_budget: int = 0
    estimated_tokens: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.to_dict(),
            "symbols": [node.to_dict() for node in self.symbols],
            "relations": [item.to_dict() for item in self.relations],
            "snippets": dict(self.snippets),
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "truncated": self.truncated,
        }


@runtime_checkable
class SourceTreePort(Protocol):
    async def list_files(self, *, repo_id: str, revision: str) -> Sequence[str]: ...

    async def read_file(self, *, repo_id: str, revision: str, path: str) -> str: ...


@runtime_checkable
class AstPort(Protocol):
    async def parse(
        self,
        *,
        repo_id: str,
        revision: str,
        path: str,
        language: str,
        source: str,
    ) -> ParsedFile: ...


@runtime_checkable
class LspPort(Protocol):
    async def callers(self, symbol: SymbolRef) -> Sequence[SymbolRef]: ...

    async def callees(self, symbol: SymbolRef) -> Sequence[SymbolRef]: ...

    async def references(self, symbol: SymbolRef) -> Sequence[SymbolRef]: ...


@runtime_checkable
class GitPort(Protocol):
    async def revision(self, *, repo_id: str) -> str | None: ...


@runtime_checkable
class GraphStorePort(Protocol):
    async def save(self, snapshot: GraphSnapshot) -> None: ...

    async def load(self, *, repo_id: str, revision: str) -> GraphSnapshot | None: ...


@runtime_checkable
class EmbeddingSearchPort(Protocol):
    async def search(self, *, query: str, limit: int) -> Sequence[SymbolRef]: ...


@dataclass(frozen=True, slots=True)
class ParsedFile:
    repo_id: str
    revision: str
    path: str
    language: str
    nodes: tuple[SymbolNode, ...] = ()
    relations: tuple[CodeRelation, ...] = ()
    diagnostics: tuple[str, ...] = ()


class MemoryGraphStore:
    """Small provider store useful for tests and process-local composition."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], GraphSnapshot] = {}

    async def save(self, snapshot: GraphSnapshot) -> None:
        self._snapshots[(snapshot.repo_id, snapshot.revision)] = snapshot

    async def load(self, *, repo_id: str, revision: str) -> GraphSnapshot | None:
        return self._snapshots.get((repo_id, revision))

    async def latest(self, *, repo_id: str) -> GraphSnapshot | None:
        candidates = [
            snapshot
            for (candidate_repo, _), snapshot in self._snapshots.items()
            if candidate_repo == repo_id
        ]
        return (
            max(candidates, key=lambda item: (item.indexed_at, item.revision))
            if candidates
            else None
        )


class JsonGraphStore:
    """Versioned file-backed graph store implementing ``GraphStorePort``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, repo_id: str, revision: str) -> Path:
        key = hashlib.sha256(f"{repo_id}\0{revision}".encode()).hexdigest()
        return self.root / f"{key}.json"

    async def save(self, snapshot: GraphSnapshot) -> None:
        path = self._path(snapshot.repo_id, snapshot.revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_json(snapshot.to_dict()), encoding="utf-8")
        tmp.replace(path)

    async def load(self, *, repo_id: str, revision: str) -> GraphSnapshot | None:
        path = self._path(repo_id, revision)
        try:
            return GraphSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError):
            return None

    async def latest(self, *, repo_id: str) -> GraphSnapshot | None:
        candidates: list[GraphSnapshot] = []
        if not self.root.exists():
            return None
        for path in self.root.glob("*.json"):
            try:
                snapshot = GraphSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if snapshot.repo_id == repo_id:
                candidates.append(snapshot)
        return (
            max(candidates, key=lambda item: (item.indexed_at, item.revision))
            if candidates
            else None
        )


class LocalSourceTreePort:
    """Read-only local source provider; all mutation remains outside the element."""

    def __init__(self, root: str | Path, *, extensions: Iterable[str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.extensions = frozenset(extensions or {".py", ".js", ".ts", ".tsx", ".go", ".rs"})

    async def list_files(self, *, repo_id: str, revision: str) -> Sequence[str]:
        del repo_id, revision
        return tuple(
            str(path.relative_to(self.root))
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
            and ".git" not in path.parts
            and (not self.extensions or path.suffix in self.extensions)
        )

    async def read_file(self, *, repo_id: str, revision: str, path: str) -> str:
        del repo_id, revision
        candidate = (self.root / path).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise ValueError("source path escapes source tree")
        return candidate.read_text(encoding="utf-8")


def _language_for(path: str) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
    }.get(Path(path).suffix.lower(), "unknown")


def _name_of(node: python_ast.AST) -> str | None:
    if isinstance(node, python_ast.Name):
        return node.id
    if isinstance(node, python_ast.Attribute):
        parent = _name_of(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


class PythonAstPort:
    """Deterministic stdlib-AST provider for Python plus generic fallback."""

    async def parse(
        self,
        *,
        repo_id: str,
        revision: str,
        path: str,
        language: str,
        source: str,
    ) -> ParsedFile:
        if language != "python":
            return self._parse_generic(repo_id, revision, path, language, source)
        try:
            tree = python_ast.parse(source, filename=path)
        except SyntaxError as exc:
            return ParsedFile(
                repo_id,
                revision,
                path,
                language,
                diagnostics=(f"partial parse failure at {path}:{exc.lineno or 0}: {exc.msg}",),
            )

        file_ref = SymbolRef(
            repo_id,
            revision,
            path,
            language,
            path,
            "file",
            path,
            {"line": 1, "column": 0},
        )
        nodes: list[SymbolNode] = [
            SymbolNode(
                file_ref,
                signature=path,
                content_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            )
        ]
        relations: list[CodeRelation] = []

        def unresolved(name: str, kind: str = "unknown") -> SymbolRef:
            return SymbolRef(
                repo_id,
                revision,
                "<unresolved>",
                language,
                name.rsplit(".", 1)[-1],
                kind,
                name,
                {},
            )

        class Visitor(python_ast.NodeVisitor):
            def __init__(self) -> None:
                self.scopes: list[SymbolRef] = []

            @property
            def current(self) -> SymbolRef:
                return self.scopes[-1] if self.scopes else file_ref

            def _define(self, node: python_ast.AST, name: str, kind: str) -> SymbolRef:
                qualified = ".".join([*(item.qualified_name for item in self.scopes), name])
                ref = SymbolRef(
                    repo_id,
                    revision,
                    path,
                    language,
                    name,
                    kind,
                    qualified,
                    {
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "end_line": int(getattr(node, "end_lineno", 0) or 0),
                        "column": int(getattr(node, "col_offset", 0) or 0),
                    },
                )
                nodes.append(SymbolNode(ref, signature=name))
                relations.append(DefinitionEdge(self.current, ref))
                return ref

            def visit_ClassDef(self, node: python_ast.ClassDef) -> None:
                ref = self._define(node, node.name, "class")
                for base in node.bases:
                    name = _name_of(base)
                    if name:
                        relations.append(CodeRelation(ref, unresolved(name, "class"), "inherits"))
                self.scopes.append(ref)
                self.generic_visit(node)
                self.scopes.pop()

            def _visit_function(
                self, node: python_ast.FunctionDef | python_ast.AsyncFunctionDef
            ) -> None:
                kind = (
                    "method"
                    if self.scopes and self.scopes[-1].symbol_kind == "class"
                    else "function"
                )
                ref = self._define(node, node.name, kind)
                self.scopes.append(ref)
                self.generic_visit(node)
                self.scopes.pop()

            def visit_FunctionDef(self, node: python_ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: python_ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

            def visit_Import(self, node: python_ast.Import) -> None:
                for alias in node.names:
                    relations.append(DependencyEdge(self.current, unresolved(alias.name, "module")))

            def visit_ImportFrom(self, node: python_ast.ImportFrom) -> None:
                module = node.module or ""
                for alias in node.names:
                    name = f"{module}.{alias.name}" if module else alias.name
                    relations.append(DependencyEdge(self.current, unresolved(name, "module")))

            def visit_Call(self, node: python_ast.Call) -> None:
                name = _name_of(node.func)
                if name:
                    relations.append(CallEdge(self.current, unresolved(name)))
                self.generic_visit(node)

            def visit_Name(self, node: python_ast.Name) -> None:
                if isinstance(node.ctx, python_ast.Load):
                    relations.append(ReferenceEdge(self.current, unresolved(node.id)))

        Visitor().visit(tree)
        return ParsedFile(repo_id, revision, path, language, tuple(nodes), tuple(relations))

    @staticmethod
    def _parse_generic(
        repo_id: str,
        revision: str,
        path: str,
        language: str,
        source: str,
    ) -> ParsedFile:
        patterns = {
            "javascript": re.compile(
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*class\s+(\w+)",
                re.M,
            ),
            "typescript": re.compile(
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*class\s+(\w+)",
                re.M,
            ),
            "go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.M),
            "rust": re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)", re.M),
        }
        file_ref = SymbolRef(repo_id, revision, path, language, path, "file", path, {"line": 1})
        nodes = [SymbolNode(file_ref, signature=path)]
        relations: list[CodeRelation] = []
        pattern = patterns.get(language)
        if pattern:
            for match in pattern.finditer(source):
                name = next((group for group in match.groups() if group), "unknown")
                line = source.count("\n", 0, match.start()) + 1
                ref = SymbolRef(
                    repo_id,
                    revision,
                    path,
                    language,
                    name,
                    "class" if "class" in match.group(0) else "function",
                    name,
                    {"line": line},
                )
                nodes.append(SymbolNode(ref, signature=name))
                relations.append(DefinitionEdge(file_ref, ref))
        return ParsedFile(repo_id, revision, path, language, tuple(nodes), tuple(relations))


def _resolve_relations(
    nodes: Sequence[SymbolNode], relations: Sequence[CodeRelation]
) -> tuple[CodeRelation, ...]:
    by_qualified: dict[tuple[str, str, str], list[SymbolRef]] = {}
    by_name: dict[tuple[str, str, str], list[SymbolRef]] = {}
    for node in nodes:
        ref = node.ref
        by_qualified.setdefault((ref.repo_id, ref.revision, ref.qualified_name), []).append(ref)
        by_name.setdefault((ref.repo_id, ref.revision, ref.symbol), []).append(ref)
    out: list[CodeRelation] = []
    for relation in relations:
        target = relation.target
        if target.path != "<unresolved>":
            out.append(relation)
            continue
        candidates: list[SymbolRef] = []
        if target.symbol_kind == "module":
            module_name = target.qualified_name
            if relation.relation_type == "imports" and "." in module_name:
                module_name = module_name.rsplit(".", 1)[0]
            module_paths = {
                module_name,
                f"{module_name}.py",
                f"{module_name.replace('.', '/')}.py",
                f"{module_name.replace('.', '/')}/__init__.py",
            }
            candidates = [
                node.ref
                for node in nodes
                if node.ref.symbol_kind == "file" and node.ref.path in module_paths
            ]
        if not candidates:
            candidates = by_qualified.get(
                (target.repo_id, target.revision, target.qualified_name), []
            )
        if not candidates:
            candidates = by_name.get((target.repo_id, target.revision, target.symbol), [])
        if not candidates:
            out.append(relation)
            continue
        metadata = dict(relation.metadata)
        if len(candidates) > 1:
            metadata["ambiguous"] = True
        for candidate in candidates:
            # ``CallEdge``/the other typed edge classes have a deliberately
            # narrower constructor than ``CodeRelation``.  Rebuild through
            # that constructor when possible so typed facts remain typed.
            if isinstance(relation, _TypedEdge):
                out.append(type(relation)(relation.source, candidate, metadata))
            else:
                out.append(
                    CodeRelation(
                        source=relation.source,
                        target=candidate,
                        relation_type=relation.relation_type,
                        metadata=metadata,
                    )
                )
    unique: dict[tuple[str, str, str, str], CodeRelation] = {}
    for relation in out:
        key = (
            relation.source.symbol_id,
            relation.target.symbol_id,
            relation.relation_type,
            _json(relation.metadata),
        )
        unique[key] = relation
    return tuple(unique.values())


def _as_symbol_ref(symbol: SymbolRef | SymbolNode | str) -> SymbolRef | str:
    return symbol.ref if isinstance(symbol, SymbolNode) else symbol


class CodeKnowledgeGraph:
    """Canonical, async, fact-only code graph API."""

    ELEMENT_CONTRACT = ElementContract(
        element_id="code_knowledge_graph",
        element_version=1,
        owner_repo="omodul",
        canonical_import="omodul.code_knowledge_graph",
        canonical_export="CodeKnowledgeGraph",
        input_contract="repo_id + revision + source/port-backed code files",
        output_contract="GraphSnapshot, GraphDelta, facts and bounded context slices",
        dependency_ports=(
            "SourceTreePort",
            "AstPort",
            "LspPort",
            "GitPort",
            "GraphStorePort",
            "EmbeddingSearchPort(optional)",
        ),
        state_model="revision-keyed graph snapshots; no task intent state",
        persistence_model="injected versioned GraphStorePort; memory or atomic JSON provider",
        authority_declaration=zero_authority(),
        async_contract="all public graph operations are awaitable; ports own I/O scheduling",
        failure_semantics="partial parse failures are diagnostics; invalid port failures surface",
        recovery_semantics="reload a revision snapshot or re-index/sync deterministically",
        observability_contract="diagnostics and snapshot ids are returned, no hidden success claim",
        compatibility_contract="facts only; no Veya/GoalRun/Session/project imports",
        conformance_suite=(
            "SYMBOL_IDENTITY",
            "CALL_GRAPH",
            "DEPENDENCY_GRAPH",
            "IMPACT_GRAPH",
            "INCREMENTAL_SYNC",
            "REVISION_ISOLATION",
            "MULTI_REPO_ISOLATION",
            "AFFECTED_TESTS",
            "CONTEXT_BUDGETING",
            "STALE_GRAPH_DETECTION",
        ),
    )

    def __init__(
        self,
        *,
        source_tree: SourceTreePort | str | Path | None = None,
        ast_port: AstPort | None = None,
        lsp: LspPort | None = None,
        git: GitPort | None = None,
        graph_store: GraphStorePort | None = None,
        search: EmbeddingSearchPort | None = None,
    ) -> None:
        if isinstance(source_tree, (str, Path)):
            source_tree = LocalSourceTreePort(source_tree)
        self.source_tree = source_tree
        self.ast_port = ast_port or PythonAstPort()
        self.lsp = lsp
        self.git = git
        self.graph_store = graph_store or MemoryGraphStore()
        self.search = search
        self._snapshots: dict[tuple[str, str], GraphSnapshot] = {}
        self._last_key: tuple[str, str] | None = None

    async def index(
        self,
        repo_id: str,
        revision: str,
        *,
        paths: Sequence[str] | Mapping[str, str] | None = None,
    ) -> GraphSnapshot:
        """Index one immutable repository revision."""

        files: dict[str, str] = {}
        if isinstance(paths, Mapping):
            files = {str(path): str(source) for path, source in paths.items()}
        else:
            selected = list(paths) if paths is not None else None
            if selected is None:
                if self.source_tree is None:
                    raise ValueError("source_tree or paths is required for indexing")
                selected = list(
                    await self.source_tree.list_files(repo_id=repo_id, revision=revision)
                )
            if self.source_tree is None:
                raise ValueError("source_tree is required when paths are file names")
            for path in selected:
                files[str(path)] = await self.source_tree.read_file(
                    repo_id=repo_id, revision=revision, path=str(path)
                )

        parsed: list[ParsedFile] = []
        source_digests: dict[str, str] = {}
        diagnostics: list[str] = []
        for path in sorted(files):
            source = files[path]
            source_digests[path] = hashlib.sha256(source.encode("utf-8")).hexdigest()
            try:
                result = await self.ast_port.parse(
                    repo_id=repo_id,
                    revision=revision,
                    path=path,
                    language=_language_for(path),
                    source=source,
                )
                normalized = (
                    result
                    if isinstance(result, ParsedFile)
                    else ParsedFile(
                        repo_id=repo_id,
                        revision=revision,
                        path=path,
                        language=_language_for(path),
                        nodes=tuple(
                            item if isinstance(item, SymbolNode) else SymbolNode.from_dict(item)
                            for item in result.get("nodes", [])
                        ),
                        relations=tuple(
                            item if isinstance(item, CodeRelation) else CodeRelation.from_dict(item)
                            for item in result.get("relations", [])
                        ),
                        diagnostics=tuple(str(item) for item in result.get("diagnostics", [])),
                    )
                )
                parsed.append(normalized)
                diagnostics.extend(normalized.diagnostics)
            except Exception as exc:
                diagnostics.append(f"partial parse failure at {path}: {exc}")

        nodes = tuple(
            sorted((node for item in parsed for node in item.nodes), key=lambda n: n.symbol_id)
        )
        relations = _resolve_relations(
            nodes,
            tuple(relation for item in parsed for relation in item.relations),
        )
        snapshot = GraphSnapshot(
            repo_id=repo_id,
            revision=revision,
            nodes=nodes,
            relations=relations,
            source_digests=source_digests,
            diagnostics=tuple(sorted(set(diagnostics))),
            indexed_at=time.time(),
        )
        self._snapshots[(repo_id, revision)] = snapshot
        self._last_key = (repo_id, revision)
        await self.graph_store.save(snapshot)
        return snapshot

    async def sync(
        self,
        repo_id: str,
        revision: str,
        *,
        paths: Sequence[str] | Mapping[str, str] | None = None,
    ) -> GraphDelta:
        """Index a revision and return only the deterministic graph delta."""

        previous = self._latest_for_repo(repo_id)
        if previous is None:
            latest = getattr(self.graph_store, "latest", None)
            if callable(latest):
                previous = await latest(repo_id=repo_id)
        current = await self.index(repo_id, revision, paths=paths)
        return self._delta(previous, current)

    async def resolve(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
        path: str | None = None,
    ) -> list[SymbolNode]:
        candidate = _as_symbol_ref(symbol)
        if isinstance(candidate, SymbolRef):
            repo_id = repo_id or candidate.repo_id
            revision = revision or candidate.revision
        snapshot = await self._require_snapshot(repo_id, revision)
        if isinstance(candidate, SymbolRef):
            return [node for node in snapshot.nodes if node.symbol_id == candidate.symbol_id]
        resolved = [
            node
            for node in snapshot.nodes
            if (node.ref.qualified_name == candidate or node.ref.symbol == candidate)
            and (path is None or node.ref.path == path)
        ]
        if not resolved and self.search is not None:
            refs = await self.search.search(query=str(candidate), limit=20)
            wanted = {ref.symbol_id for ref in refs}
            resolved = [node for node in snapshot.nodes if node.symbol_id in wanted]
        return resolved

    async def callers(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolNode]:
        return await self._related(
            symbol, "calls", reverse=True, repo_id=repo_id, revision=revision
        )

    async def callees(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolNode]:
        return await self._related(
            symbol, "calls", reverse=False, repo_id=repo_id, revision=revision
        )

    async def dependencies(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolNode]:
        return await self._related(
            symbol, "imports", reverse=False, repo_id=repo_id, revision=revision
        )

    async def dependents(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolNode]:
        return await self._related(
            symbol, "imports", reverse=True, repo_id=repo_id, revision=revision
        )

    async def references(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolNode]:
        return await self._related(
            symbol, "references", reverse=True, repo_id=repo_id, revision=revision
        )

    async def trace(
        self,
        start: SymbolRef | SymbolNode | str,
        end: SymbolRef | SymbolNode | str | None = None,
        *,
        relation_types: Sequence[str] = ("calls",),
        max_depth: int = 8,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> CodePath:
        snapshot = await self._require_snapshot(repo_id, revision)
        start_node = (await self.resolve(start, repo_id=repo_id, revision=revision))[:1]
        if not start_node:
            return CodePath()
        end_nodes = (
            await self.resolve(end, repo_id=repo_id, revision=revision) if end is not None else []
        )
        end_ids = {node.symbol_id for node in end_nodes}
        adjacency: dict[str, list[CodeRelation]] = {}
        for relation in snapshot.relations:
            if relation.relation_type in relation_types:
                adjacency.setdefault(relation.source.symbol_id, []).append(relation)
        queue: deque[tuple[SymbolRef, tuple[SymbolRef, ...], tuple[CodeRelation, ...]]] = deque(
            [(start_node[0].ref, (start_node[0].ref,), ())]
        )
        visited = {start_node[0].symbol_id}
        while queue:
            current, nodes, relations = queue.popleft()
            if end is None and len(nodes) > 1:
                return CodePath(nodes, relations, True)
            if end_ids and current.symbol_id in end_ids:
                return CodePath(nodes, relations, True)
            if len(relations) >= max_depth:
                continue
            for relation in adjacency.get(current.symbol_id, []):
                if relation.target.symbol_id in visited:
                    continue
                visited.add(relation.target.symbol_id)
                queue.append((relation.target, (*nodes, relation.target), (*relations, relation)))
        return CodePath((start_node[0].ref,), (), end is None and bool(start_node))

    async def impact(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        max_depth: int = 8,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> ImpactSet:
        snapshot = await self._require_snapshot(repo_id, revision)
        roots = await self.resolve(symbol, repo_id=repo_id, revision=revision)
        if not roots:
            raise KeyError(f"symbol not found: {symbol!r}")
        root = roots[0].ref
        adjacency: dict[str, list[tuple[CodeRelation, SymbolRef]]] = {}
        for relation in snapshot.relations:
            if relation.relation_type not in {
                "calls",
                "references",
                "imports",
                "inherits",
                "implements",
            }:
                continue
            adjacency.setdefault(relation.source.symbol_id, []).append((relation, relation.target))
            # Impact is intentionally bidirectional: callers/dependents and
            # callees/dependencies can both be affected by a changed symbol.
            adjacency.setdefault(relation.target.symbol_id, []).append((relation, relation.source))
        queue: deque[tuple[SymbolRef, int]] = deque([(root, 0)])
        seen = {root.symbol_id}
        relations: list[CodeRelation] = []
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for relation, target in adjacency.get(current.symbol_id, []):
                relations.append(relation)
                if target.symbol_id not in seen:
                    seen.add(target.symbol_id)
                    queue.append((target, depth + 1))
        node_map = snapshot.node_map()
        symbols = tuple(node_map[item] for item in sorted(seen) if item in node_map)
        tests = tuple(node.ref for node in symbols if _is_test_path(node.ref.path, node.ref.symbol))
        files = tuple(sorted({node.ref.path for node in symbols}))
        return ImpactSet(root, tuple(node.ref for node in symbols), files, tests, tuple(relations))

    async def affected_tests(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> list[SymbolRef]:
        return list((await self.impact(symbol, repo_id=repo_id, revision=revision)).tests)

    async def context_slice(
        self,
        symbol: SymbolRef | SymbolNode | str,
        *,
        token_budget: int = 2000,
        max_depth: int = 2,
        repo_id: str | None = None,
        revision: str | None = None,
    ) -> CodeContextSlice:
        if token_budget <= 0:
            raise ValueError("token_budget must be > 0")
        snapshot = await self._require_snapshot(repo_id, revision)
        roots = await self.resolve(symbol, repo_id=repo_id, revision=revision)
        if not roots:
            raise KeyError(f"symbol not found: {symbol!r}")
        root = roots[0].ref
        impact = await self.impact(root, max_depth=max_depth, repo_id=repo_id, revision=revision)
        node_map = snapshot.node_map()
        chosen: list[SymbolNode] = []
        snippets: dict[str, str] = {}
        used = 0
        for ref in impact.symbols:
            node = node_map.get(ref.symbol_id)
            if node is None:
                continue
            snippet = node.signature or ref.qualified_name
            cost = max(1, (len(snippet) + 3) // 4)
            if used + cost > token_budget:
                break
            chosen.append(node)
            snippets[ref.symbol_id] = snippet
            used += cost
        chosen_ids = {node.symbol_id for node in chosen}
        relations = tuple(
            relation
            for relation in impact.relations
            if relation.source.symbol_id in chosen_ids and relation.target.symbol_id in chosen_ids
        )
        return CodeContextSlice(
            root=root,
            symbols=tuple(chosen),
            relations=relations,
            snippets=snippets,
            token_budget=token_budget,
            estimated_tokens=used,
            truncated=len(chosen) < len(impact.symbols),
        )

    async def snapshot(
        self, *, repo_id: str | None = None, revision: str | None = None
    ) -> GraphSnapshot:
        return await self._require_snapshot(repo_id, revision, allow_missing=False)

    async def stale(self, *, repo_id: str | None = None, revision: str | None = None) -> bool:
        """Compare source fingerprints for a snapshot without rebuilding it."""

        snapshot = await self._require_snapshot(repo_id, revision)
        if self.git is not None:
            current_revision = await self.git.revision(repo_id=snapshot.repo_id)
            if current_revision is not None and current_revision != snapshot.revision:
                return True
        if self.source_tree is None:
            return False
        current: dict[str, str] = {}
        for path in await self.source_tree.list_files(
            repo_id=snapshot.repo_id, revision=snapshot.revision
        ):
            source = await self.source_tree.read_file(
                repo_id=snapshot.repo_id, revision=snapshot.revision, path=path
            )
            current[str(path)] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return dict(snapshot.source_digests) != current

    async def _require_snapshot(
        self,
        repo_id: str | None,
        revision: str | None,
        *,
        allow_missing: bool = False,
    ) -> GraphSnapshot:
        if repo_id is None or revision is None:
            if self._last_key is None:
                if allow_missing:
                    raise KeyError("no graph snapshot indexed")
                raise KeyError("no graph snapshot indexed")
            repo_id, revision = self._last_key
        key = (repo_id, revision)
        snapshot = self._snapshots.get(key)
        if snapshot is None:
            snapshot = await self.graph_store.load(repo_id=repo_id, revision=revision)
            if snapshot is not None:
                self._snapshots[key] = snapshot
        if snapshot is None:
            if allow_missing:
                raise KeyError(f"graph snapshot not found: {repo_id}@{revision}")
            raise KeyError(f"graph snapshot not found: {repo_id}@{revision}")
        self._last_key = key
        return snapshot

    def _latest_for_repo(self, repo_id: str) -> GraphSnapshot | None:
        candidates = [
            snapshot
            for (candidate_repo, _), snapshot in self._snapshots.items()
            if candidate_repo == repo_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.indexed_at, item.revision))

    async def _related(
        self,
        symbol: SymbolRef | SymbolNode | str,
        relation_type: str,
        *,
        reverse: bool,
        repo_id: str | None,
        revision: str | None,
    ) -> list[SymbolNode]:
        snapshot = await self._require_snapshot(repo_id, revision)
        roots = await self.resolve(symbol, repo_id=repo_id, revision=revision)
        root_ids = {node.symbol_id for node in roots}
        node_map = snapshot.node_map()
        refs: dict[str, SymbolRef] = {}
        for relation in snapshot.relations:
            if relation.relation_type != relation_type:
                continue
            if reverse and relation.target.symbol_id in root_ids:
                refs[relation.source.symbol_id] = relation.source
            elif not reverse and relation.source.symbol_id in root_ids:
                refs[relation.target.symbol_id] = relation.target
        if not refs and self.lsp and roots:
            lsp_method = {
                "calls": "callers" if reverse else "callees",
                "references": "references",
            }.get(relation_type)
            if lsp_method:
                fetch = getattr(self.lsp, lsp_method)
                for root in roots:
                    for ref in await fetch(root.ref):
                        refs[ref.symbol_id] = ref
        return [node_map[item] for item in sorted(refs) if item in node_map]

    @staticmethod
    def _delta(previous: GraphSnapshot | None, current: GraphSnapshot) -> GraphDelta:
        old_nodes = {_node_identity(node): node for node in (previous.nodes if previous else ())}
        new_nodes = {_node_identity(node): node for node in current.nodes}
        added = tuple(new_nodes[key] for key in sorted(set(new_nodes) - set(old_nodes)))
        removed = tuple(old_nodes[key] for key in sorted(set(old_nodes) - set(new_nodes)))
        changed = tuple(
            new_nodes[key]
            for key in sorted(set(new_nodes) & set(old_nodes))
            if _node_semantic_key(new_nodes[key]) != _node_semantic_key(old_nodes[key])
        )
        old_rel = {
            _relation_identity(item): item for item in (previous.relations if previous else ())
        }
        new_rel = {_relation_identity(item): item for item in current.relations}
        return GraphDelta(
            current.repo_id,
            previous.revision if previous else None,
            current.revision,
            added,
            removed,
            changed,
            tuple(new_rel[key] for key in sorted(set(new_rel) - set(old_rel))),
            tuple(old_rel[key] for key in sorted(set(old_rel) - set(new_rel))),
        )


def _ref_identity(ref: SymbolRef) -> tuple[str, str, str, str, str]:
    """Return a revision-independent identity used only for incremental diffs."""

    return (
        ref.repo_id,
        ref.path,
        ref.qualified_name,
        ref.symbol_kind,
        _json(ref.location),
    )


def _node_identity(node: SymbolNode) -> tuple[str, str, str, str, str]:
    return _ref_identity(node.ref)


def _node_semantic_key(node: SymbolNode) -> tuple[Any, ...]:
    return (*_ref_identity(node.ref), node.signature, node.content_hash, _json(node.metadata))


def _relation_identity(relation: CodeRelation) -> tuple[Any, ...]:
    return (
        _ref_identity(relation.source),
        _ref_identity(relation.target),
        relation.relation_type,
        _json(relation.metadata),
    )


def _is_test_path(path: str, symbol: str) -> bool:
    lowered = path.lower()
    return (
        "/test" in lowered
        or lowered.startswith("test")
        or "_test." in lowered
        or symbol.lower().startswith("test")
    )


__all__ = [
    "AstPort",
    "CallEdge",
    "CodeContextSlice",
    "CodeKnowledgeGraph",
    "CodePath",
    "CodeRelation",
    "DefinitionEdge",
    "DependencyEdge",
    "EmbeddingSearchPort",
    "GitPort",
    "GraphDelta",
    "GraphSnapshot",
    "GraphStorePort",
    "ImpactSet",
    "JsonGraphStore",
    "LocalSourceTreePort",
    "LspPort",
    "MemoryGraphStore",
    "ParsedFile",
    "PythonAstPort",
    "ReferenceEdge",
    "RELATION_TYPES",
    "SourceTreePort",
    "SymbolNode",
    "SymbolRef",
]
