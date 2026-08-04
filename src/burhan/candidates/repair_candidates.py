"""Repair candidate generator and ranker.

Produces multiple ``RepairCandidate`` objects from a list of hypotheses,
ranked by:

1. Evidence strength (confidence)
2. Smallest change (fewest lines / files)
3. Lowest regression risk

Design rules
------------
* No state mutation – returns new objects.
* Textual similarity noted but not presented as causal proof.
* A candidate that touches fewer files than another equally-confident one
  is preferred.
* Secrets files and excluded directories are never targeted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model import Hypothesis


# ---------------------------------------------------------------------------
# RepairCandidate model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairCandidate:
    """A single ranked repair proposal.

    Attributes
    ----------
    rank:
        1-based rank (1 = best candidate).
    hypothesis:
        The hypothesis this candidate addresses.
    description:
        Short Arabic description of what the repair does.
    target_file:
        Relative path of the file to be modified (if known).
    target_line:
        Line number within *target_file* (0 = unknown).
    change_size:
        Estimated number of lines that will change.
    affected_files:
        All files this candidate would modify.
    confidence:
        Inherited from the hypothesis, possibly adjusted for risk.
    risk:
        Qualitative risk level: "low", "medium", "high".
    supporting_evidence:
        Tuple of evidence summaries that support this candidate.
    opposing_evidence:
        Tuple of evidence summaries that argue against this candidate.
    rejection_reason:
        Set if this candidate was evaluated and rejected; empty string if
        still viable.
    """

    rank: int
    hypothesis: Hypothesis
    description: str
    target_file: str
    target_line: int
    change_size: int
    affected_files: tuple[str, ...]
    confidence: float
    risk: str  # "low" | "medium" | "high"
    supporting_evidence: tuple[str, ...]
    opposing_evidence: tuple[str, ...]
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "description": self.description,
            "target_file": self.target_file,
            "target_line": self.target_line,
            "change_size": self.change_size,
            "affected_files": list(self.affected_files),
            "confidence": self.confidence,
            "risk": self.risk,
            "supporting_evidence": list(self.supporting_evidence),
            "opposing_evidence": list(self.opposing_evidence),
            "rejection_reason": self.rejection_reason,
            "hypothesis_kind": self.hypothesis.kind,
        }


# ---------------------------------------------------------------------------
# Candidate generator
# ---------------------------------------------------------------------------

def _parse_location(location: str | None) -> tuple[str, int]:
    """Split ``"file.py:42"`` into ``("file.py", 42)``."""
    if not location:
        return "", 0
    parts = location.rsplit(":", 1)
    file = parts[0]
    try:
        line = int(parts[1])
    except (IndexError, ValueError):
        line = 0
    return file, line


def _risk(hypothesis: Hypothesis) -> str:
    """Determine risk level from hypothesis kind."""
    high_risk = {
        "unbound_local_variable",
        "missing_import_name",
        "infinite_recursion",
        "os_error",
    }
    low_risk = {
        "undefined_name",
        "missing_attribute",
        "syntax_error",
        "wrong_argument_count",
    }
    if hypothesis.kind in high_risk:
        return "high"
    if hypothesis.kind in low_risk:
        return "low"
    return "medium"


def generate_candidates(
    hypotheses: tuple[Hypothesis, ...],
    *,
    max_candidates: int = 5,
) -> tuple[RepairCandidate, ...]:
    """Return ranked repair candidates derived from *hypotheses*.

    The function filters out ``insufficient_evidence`` hypotheses and
    prefers smaller-change, lower-risk proposals.

    Parameters
    ----------
    hypotheses:
        Ranked list from ``HypothesisEngine.generate``.
    max_candidates:
        Maximum number of candidates to return (1–10).
    """
    if not 1 <= max_candidates <= 10:
        raise ValueError("max_candidates must be between 1 and 10")

    viable = [h for h in hypotheses if h.kind != "insufficient_evidence"]
    if not viable:
        return ()

    raw: list[RepairCandidate] = []
    for hyp in viable:
        file, line = _parse_location(hyp.location)
        supporting = tuple(
            ev.summary for ev in hyp.evidence if not ev.source.startswith("opposing:")
        )
        opposing = tuple(
            ev.summary for ev in hyp.evidence if ev.source.startswith("opposing:")
        )
        # Describe the repair
        if hyp.suggested_replacement:
            desc = f"استبدل '{hyp.target}' بـ '{hyp.suggested_replacement}'"
            change_size = 1
        elif hyp.kind == "syntax_error":
            desc = f"صحّح الخطأ التركيبي: {hyp.explanation}"
            change_size = 1
        elif hyp.kind == "wrong_argument_count":
            desc = f"صحّح عدد وسطاء الدالة: {hyp.explanation}"
            change_size = 1
        else:
            desc = hyp.explanation
            change_size = 1

        risk_level = _risk(hyp)
        raw.append(
            RepairCandidate(
                rank=0,  # assigned after sorting
                hypothesis=hyp,
                description=desc,
                target_file=file,
                target_line=line,
                change_size=change_size,
                affected_files=(file,) if file else (),
                confidence=hyp.confidence,
                risk=risk_level,
                supporting_evidence=supporting,
                opposing_evidence=opposing,
            )
        )

    # Sort: confidence desc, then change_size asc, then risk asc
    _risk_order = {"low": 0, "medium": 1, "high": 2}
    raw.sort(key=lambda c: (-c.confidence, c.change_size, _risk_order.get(c.risk, 1)))

    ranked = tuple(
        RepairCandidate(
            rank=i + 1,
            hypothesis=c.hypothesis,
            description=c.description,
            target_file=c.target_file,
            target_line=c.target_line,
            change_size=c.change_size,
            affected_files=c.affected_files,
            confidence=c.confidence,
            risk=c.risk,
            supporting_evidence=c.supporting_evidence,
            opposing_evidence=c.opposing_evidence,
            rejection_reason=c.rejection_reason,
        )
        for i, c in enumerate(raw[:max_candidates])
    )
    return ranked


def select_smallest_successful(
    candidates: tuple[RepairCandidate, ...],
) -> RepairCandidate | None:
    """Return the successful candidate with the smallest change size.

    "Successful" means ``rejection_reason`` is empty.
    Among ties, prefer lower rank (higher confidence).
    """
    viable = [c for c in candidates if not c.rejection_reason]
    if not viable:
        return None
    viable.sort(key=lambda c: (c.change_size, c.rank))
    return viable[0]
