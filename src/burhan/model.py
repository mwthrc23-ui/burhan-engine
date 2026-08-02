from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    GOAL = "goal"
    FILE = "file"
    SYMBOL = "symbol"
    EVENT = "event"
    CONSTRAINT = "constraint"
    HYPOTHESIS = "hypothesis"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class BirNode:
    id: str
    kind: NodeKind
    label: str
    attributes: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class BirEdge:
    source: str
    relation: str
    target: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    summary: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "summary": self.summary, "weight": self.weight}


@dataclass(frozen=True, slots=True)
class Hypothesis:
    kind: str
    target: str
    explanation: str
    location: str | None
    energy: float
    confidence: float
    suggested_replacement: str | None = None
    evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "explanation": self.explanation,
            "location": self.location,
            "energy": self.energy,
            "confidence": self.confidence,
            "suggested_replacement": self.suggested_replacement,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class Provenance:
    engine_version: str
    input_fingerprint: str
    analyzed_files: int
    scan_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "input_fingerprint": self.input_fingerprint,
            "analyzed_files": self.analyzed_files,
            "scan_truncated": self.scan_truncated,
        }


@dataclass(frozen=True, slots=True)
class BurhanState:
    goal: str
    nodes: tuple[BirNode, ...] = ()
    edges: tuple[BirEdge, ...] = ()

    @classmethod
    def empty(cls, goal: str) -> BurhanState:
        return cls(goal=goal.strip())

    def with_node(self, node: BirNode) -> BurhanState:
        remaining = tuple(existing for existing in self.nodes if existing.id != node.id)
        return replace(self, nodes=remaining + (node,))

    def with_edge(self, edge: BirEdge) -> BurhanState:
        if edge in self.edges:
            return self
        return replace(self, edges=self.edges + (edge,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    state: BurhanState
    hypotheses: tuple[Hypothesis, ...]
    elapsed_ms: float
    case_id: str
    provenance: Provenance
    residual_risks: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @property
    def primary(self) -> Hypothesis:
        if not self.hypotheses:
            raise RuntimeError("analysis produced no hypotheses")
        return self.hypotheses[0]

    @property
    def confidence(self) -> float:
        return self.primary.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "state": self.state.to_dict(),
            "hypotheses": [hypothesis.to_dict() for hypothesis in self.hypotheses],
            "confidence": self.confidence,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "questions": list(self.questions),
            "residual_risks": list(self.residual_risks),
            "provenance": self.provenance.to_dict(),
        }
