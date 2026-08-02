"""Burhan: evidence-first semantic diagnosis for source code."""

from .analyzer import BurhanAnalyzer
from .model import AnalysisResult, BirEdge, BirNode, BurhanState, Evidence, Hypothesis, NodeKind, Provenance
from .memory import MemoryMatch, MemoryQuery, RepairEpisode, RepairMemory
from .patcher import PatchEngine, PatchResult, VerificationResult

__all__ = [
    "AnalysisResult",
    "BirEdge",
    "BirNode",
    "BurhanAnalyzer",
    "BurhanState",
    "Evidence",
    "Hypothesis",
    "NodeKind",
    "MemoryMatch",
    "MemoryQuery",
    "PatchEngine",
    "PatchResult",
    "Provenance",
    "RepairEpisode",
    "RepairMemory",
    "VerificationResult",
]

__version__ = "0.4.0"
