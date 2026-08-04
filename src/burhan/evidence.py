"""Evidence Graph V2 – structured, traceable evidence for Burhan diagnoses.

Key design decisions
--------------------
* All objects are **frozen dataclasses** – mutation is achieved by creating
  new instances (``replace``), never by mutating the existing graph.
* Every fact records *who* produced it, *when* (monotonic counter), and a
  SHA-256 fingerprint of its content so two runs can be compared.
* Facts are classified as ``CONFIRMED``, ``INFERRED``, or ``ASSUMED`` so
  callers can distinguish evidence quality without parsing free-form text.
* The graph exposes a stable ``to_dict()`` / ``from_dict()`` round-trip
  with a ``schema_version`` field so consumers can detect breaking changes.
* Arabic summary fields are generated lazily via ``arabic_summary()``.
"""
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "burhan.evidence-graph/v2"


# ---------------------------------------------------------------------------
# Confidence level
# ---------------------------------------------------------------------------

class ConfidenceLevel(StrEnum):
    CONFIRMED = "confirmed"  # directly observed (stack trace, test failure)
    INFERRED = "inferred"    # derived from code analysis with supporting evidence
    ASSUMED = "assumed"      # plausible but not yet verified by any tool


# ---------------------------------------------------------------------------
# Individual evidence fact
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceFact:
    """A single, traceable piece of evidence.

    Attributes
    ----------
    source:
        Machine-readable source tag, e.g. ``"python_ast"``, ``"stack_trace"``,
        ``"test_run"``, ``"similarity_match"``.
    summary:
        Human-readable (Arabic) description of what was observed.
    weight:
        Importance weight used by the energy model (≥ 0).
    level:
        Epistemic classification of the fact.
    collected_at:
        Monotonically increasing counter (not wall-clock) so facts from the
        same run are ordered deterministically.
    fingerprint:
        SHA-256 hex digest of ``source + "\\0" + summary`` for deduplication
        and cross-run comparison.
    """

    source: str
    summary: str
    weight: float
    level: ConfidenceLevel = ConfidenceLevel.INFERRED
    collected_at: int = 0
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            raw = f"{self.source}\0{self.summary}".encode("utf-8")
            object.__setattr__(self, "fingerprint", hashlib.sha256(raw).hexdigest()[:16])

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "summary": self.summary,
            "weight": self.weight,
            "level": self.level.value,
            "collected_at": self.collected_at,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceFact:
        return cls(
            source=str(value["source"]),
            summary=str(value["summary"]),
            weight=float(value["weight"]),
            level=ConfidenceLevel(value.get("level", ConfidenceLevel.INFERRED)),
            collected_at=int(value.get("collected_at", 0)),
            fingerprint=str(value.get("fingerprint", "")),
        )


# ---------------------------------------------------------------------------
# Graph node
# ---------------------------------------------------------------------------

class EvidenceNodeKind(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    CALL = "call"
    IMPORT = "import"
    TYPE = "type"
    TEST = "test"
    ERROR = "error"
    HYPOTHESIS = "hypothesis"


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    """A node in the evidence graph.

    Attributes
    ----------
    id:
        Unique identifier within the graph (e.g. ``"file:src/app.py"``).
    kind:
        Semantic category of the node.
    label:
        Short human-readable label.
    facts:
        Evidence facts attached directly to this node.
    attributes:
        Arbitrary key-value pairs for extension data.
    """

    id: str
    kind: EvidenceNodeKind
    label: str
    facts: tuple[EvidenceFact, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "facts": [f.to_dict() for f in self.facts],
            "attributes": dict(self.attributes),
        }


# ---------------------------------------------------------------------------
# Graph edge
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    """A directed, labelled edge between two ``EvidenceNode``\\ s."""

    source: str
    relation: str   # e.g. "calls", "imports", "defines", "supports", "contradicts"
    target: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# The graph itself
# ---------------------------------------------------------------------------

_counter = itertools.count()


@dataclass(frozen=True, slots=True)
class EvidenceGraph:
    """Immutable evidence graph built from analysis artefacts.

    All mutating operations return a **new** ``EvidenceGraph`` instance;
    the original is never modified.
    """

    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()
    # Facts not yet attached to a node (e.g. from a stack trace scan)
    loose_facts: tuple[EvidenceFact, ...] = ()

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def with_node(self, node: EvidenceNode) -> EvidenceGraph:
        """Return a new graph with *node* added (replacing any same-id node)."""
        remaining = tuple(n for n in self.nodes if n.id != node.id)
        return replace(self, nodes=remaining + (node,))

    def with_edge(self, edge: EvidenceEdge) -> EvidenceGraph:
        """Return a new graph with *edge* added (idempotent)."""
        if edge in self.edges:
            return self
        return replace(self, edges=self.edges + (edge,))

    def with_fact(self, fact: EvidenceFact) -> EvidenceGraph:
        """Attach a loose fact (not yet bound to a node)."""
        return replace(self, loose_facts=self.loose_facts + (fact,))

    def attach_fact_to_node(self, node_id: str, fact: EvidenceFact) -> EvidenceGraph:
        """Attach *fact* to the node with *node_id*; create a placeholder node if absent."""
        existing = self._find_node(node_id)
        if existing is None:
            existing = EvidenceNode(
                id=node_id,
                kind=EvidenceNodeKind.HYPOTHESIS,
                label=node_id,
            )
        updated_node = replace(existing, facts=existing.facts + (fact,))
        return self.with_node(updated_node)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _find_node(self, node_id: str) -> EvidenceNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def neighbours(self, node_id: str, relation: str | None = None) -> tuple[str, ...]:
        """Return target IDs reachable from *node_id*."""
        return tuple(
            e.target
            for e in self.edges
            if e.source == node_id and (relation is None or e.relation == relation)
        )

    def all_facts(self) -> tuple[EvidenceFact, ...]:
        """Return all facts: node-attached + loose."""
        attached = tuple(f for n in self.nodes for f in n.facts)
        return attached + self.loose_facts

    def confirmed_facts(self) -> tuple[EvidenceFact, ...]:
        return tuple(f for f in self.all_facts() if f.level == ConfidenceLevel.CONFIRMED)

    def opposing_facts(self, hypothesis_id: str) -> tuple[EvidenceFact, ...]:
        """Return facts attached to nodes that *contradict* *hypothesis_id*."""
        contradicted_by = [
            e.source
            for e in self.edges
            if e.target == hypothesis_id and e.relation == "contradicts"
        ]
        return tuple(
            f
            for node_id in contradicted_by
            for node in self.nodes
            if node.id == node_id
            for f in node.facts
        )

    # ------------------------------------------------------------------
    # Arabic summary
    # ------------------------------------------------------------------

    def arabic_summary(self, hypothesis_id: str | None = None) -> str:
        """Return a short Arabic summary of the graph's evidence state."""
        total = len(self.all_facts())
        confirmed = len(self.confirmed_facts())
        lines = [
            f"إجمالي الأدلة: {total} ({confirmed} مؤكد)",
            f"العقد: {len(self.nodes)}  الحواف: {len(self.edges)}",
        ]
        if hypothesis_id:
            opposing = self.opposing_facts(hypothesis_id)
            lines.append(
                f"الأدلة المعارضة للفرضية '{hypothesis_id}': {len(opposing)}"
            )
        return "  |  ".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "loose_facts": [f.to_dict() for f in self.loose_facts],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceGraph:
        nodes = tuple(
            EvidenceNode(
                id=n["id"],
                kind=EvidenceNodeKind(n["kind"]),
                label=n["label"],
                facts=tuple(EvidenceFact.from_dict(f) for f in n.get("facts", [])),
                attributes=tuple(n.get("attributes", {}).items()),
            )
            for n in value.get("nodes", [])
        )
        edges = tuple(
            EvidenceEdge(
                source=e["source"],
                relation=e["relation"],
                target=e["target"],
                confidence=float(e.get("confidence", 1.0)),
            )
            for e in value.get("edges", [])
        )
        loose_facts = tuple(
            EvidenceFact.from_dict(f) for f in value.get("loose_facts", [])
        )
        return cls(nodes=nodes, edges=edges, loose_facts=loose_facts)
