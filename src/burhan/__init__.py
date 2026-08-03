"""Burhan: evidence-first semantic diagnosis for source code."""

from .analyzer import ENGINE_VERSION as _ENGINE_VERSION
from .analyzer import BurhanAnalyzer
from .memory import MemoryMatch, MemoryQuery, RepairEpisode, RepairMemory
from .model import (
    AnalysisResult,
    BirEdge,
    BirNode,
    BurhanState,
    Evidence,
    Hypothesis,
    NodeKind,
    Provenance,
)
from .patcher import (
    CommandRun,
    PatchEngine,
    PatchResult,
    ProofResult,
    ProofRunner,
    VerificationResult,
)

__all__ = [
    "AnalysisResult",
    "BirEdge",
    "BirNode",
    "BurhanAnalyzer",
    "BurhanState",
    "CommandRun",
    "Evidence",
    "Hypothesis",
    "MemoryMatch",
    "MemoryQuery",
    "NodeKind",
    "PatchEngine",
    "PatchResult",
    "ProofResult",
    "ProofRunner",
    "Provenance",
    "RepairEpisode",
    "RepairMemory",
    "VerificationResult",
]

__version__ = _ENGINE_VERSION
