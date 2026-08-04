"""KeyError family handler.

Produces multiple parallel hypotheses for ``KeyError`` tracebacks:

1. Missing key — key was never inserted into the dict.
2. Typo in key name — similar key exists.
3. Wrong dict variable — using the wrong dict entirely.
4. Key type mismatch — e.g. using int 0 when dict has string "0".

For each hypothesis the handler suggests ≥1 repair candidate:
- Use ``.get()`` with a default value.
- Add an ``in`` guard before access.
- Fix the key name / type.
- Initialise the key before use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_KEY_ERROR = re.compile(
    r"KeyError:\s*(?P<key>.+)$", re.MULTILINE
)
_PY_FRAME = re.compile(
    r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)'
)


# ---------------------------------------------------------------------------
# Lightweight result types (no dependency on heavy model types)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class KeyErrorHypothesis:
    """A single hypothesis for a KeyError."""

    kind: str  # canonical kind tag
    key_name: str
    explanation: str
    confidence: float
    supporting: tuple[str, ...]
    opposing: tuple[str, ...]
    candidates: tuple["KeyErrorCandidate", ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key_name": self.key_name,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "supporting": list(self.supporting),
            "opposing": list(self.opposing),
            "candidates": [c.to_dict() for c in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class KeyErrorCandidate:
    """A single repair candidate for a KeyError hypothesis."""

    rank: int
    description: str
    code_template: str  # Python code template (not AST-applied here)
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "description": self.description,
            "code_template": self.code_template,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class KeyErrorHandler:
    """Diagnose KeyError tracebacks and produce ranked hypotheses."""

    family = "key_error"

    def diagnose(self, error_text: str) -> tuple[KeyErrorHypothesis, ...]:
        """Return up to 4 parallel hypotheses, sorted by confidence desc."""
        key_match = _KEY_ERROR.search(error_text)
        if not key_match:
            return ()

        raw_key = key_match.group("key").strip().strip("'\"")
        location = self._extract_location(error_text)

        hypotheses: list[KeyErrorHypothesis] = []

        # Hypothesis 1: Missing key — most common cause
        hypotheses.append(
            KeyErrorHypothesis(
                kind="key_error",
                key_name=raw_key,
                explanation=(
                    f"Key {raw_key!r} does not exist in the dictionary. "
                    "It was never inserted or was removed before access."
                ),
                confidence=0.75,
                supporting=(
                    f"KeyError raised for key {raw_key!r}",
                    "Stack trace points to a dict subscript operation",
                ),
                opposing=(),
                candidates=self._candidates_for_missing_key(raw_key, location),
            )
        )

        # Hypothesis 2: Typo in key name
        hypotheses.append(
            KeyErrorHypothesis(
                kind="key_error",
                key_name=raw_key,
                explanation=(
                    f"Key {raw_key!r} may be a typo. A similarly-named key "
                    "likely exists in the dictionary."
                ),
                confidence=0.55,
                supporting=(
                    f"Key {raw_key!r} looks like a plausible human typo",
                ),
                opposing=(
                    "Cannot confirm without inspecting dict keys at runtime",
                ),
                candidates=(
                    KeyErrorCandidate(
                        rank=1,
                        description=f"Correct the key spelling near {raw_key!r}",
                        code_template=f"# Verify and correct the key name: {raw_key!r}",
                        confidence=0.50,
                    ),
                ),
            )
        )

        # Hypothesis 3: Wrong dict variable
        hypotheses.append(
            KeyErrorHypothesis(
                kind="key_error",
                key_name=raw_key,
                explanation=(
                    "The code may be accessing the wrong dictionary variable. "
                    "Another dict in scope may contain the expected key."
                ),
                confidence=0.35,
                supporting=(
                    f"Key {raw_key!r} is absent from the accessed dict",
                ),
                opposing=(
                    "No evidence of a second dict in the snippet",
                ),
                candidates=(
                    KeyErrorCandidate(
                        rank=1,
                        description="Verify the correct dict variable is being accessed",
                        code_template="# Review variable names and ensure correct dict is used",
                        confidence=0.30,
                    ),
                ),
            )
        )

        # Hypothesis 4: Key type mismatch
        hypotheses.append(
            KeyErrorHypothesis(
                kind="key_error",
                key_name=raw_key,
                explanation=(
                    f"Key {raw_key!r} may exist but with a different type "
                    "(e.g. integer vs string), causing a hash mismatch."
                ),
                confidence=0.30,
                supporting=(
                    "Python dict keys are type-sensitive",
                ),
                opposing=(
                    "Cannot confirm without inspecting dict contents",
                ),
                candidates=(
                    KeyErrorCandidate(
                        rank=1,
                        description=f"Check the type of the key; try int({raw_key}) or str({raw_key})",
                        code_template=f"val = d.get({raw_key!r}) or d.get(int({raw_key!r}), default)",
                        confidence=0.25,
                    ),
                ),
            )
        )

        return tuple(sorted(hypotheses, key=lambda h: -h.confidence))

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_location(error_text: str) -> str:
        match = _PY_FRAME.search(error_text)
        if match:
            return f"{match.group('file')}:{match.group('line')}"
        return ""

    @staticmethod
    def _candidates_for_missing_key(key: str, _location: str) -> tuple[KeyErrorCandidate, ...]:
        return (
            KeyErrorCandidate(
                rank=1,
                description=f"Use .get() to avoid KeyError: d.get({key!r}, default)",
                code_template=f"value = d.get({key!r}, None)  # or provide a real default",
                confidence=0.75,
            ),
            KeyErrorCandidate(
                rank=2,
                description=f"Guard with 'in': if {key!r} in d: value = d[{key!r}]",
                code_template=f"if {key!r} in d:\n    value = d[{key!r}]",
                confidence=0.65,
            ),
            KeyErrorCandidate(
                rank=3,
                description=f"Initialise key before use: d[{key!r}] = default",
                code_template=f"d.setdefault({key!r}, None)\nvalue = d[{key!r}]",
                confidence=0.50,
            ),
        )
