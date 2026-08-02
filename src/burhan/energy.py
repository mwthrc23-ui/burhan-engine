from __future__ import annotations

from .model import Evidence


def hypothesis_energy(
    evidence: tuple[Evidence, ...],
    *,
    unresolved_constraints: int = 0,
    estimated_change_size: int = 1,
    uncertainty: float = 0.0,
) -> float:
    """Lower energy means a smaller, better-supported solution candidate."""

    evidence_credit = sum(max(0.0, item.weight) for item in evidence)
    raw = (
        5.0
        + unresolved_constraints * 2.0
        + min(max(estimated_change_size, 0), 100) * 0.05
        + max(0.0, uncertainty) * 3.0
        - evidence_credit
    )
    return round(max(0.0, raw), 3)


def confidence_from_energy(energy: float, evidence_count: int) -> float:
    support = min(max(evidence_count, 0), 4) * 0.12
    confidence = 0.92 - min(max(energy, 0.0), 10.0) * 0.07 + support
    return round(min(0.95, max(0.05, confidence)), 3)
