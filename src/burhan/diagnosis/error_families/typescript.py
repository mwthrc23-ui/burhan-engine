"""TypeScript error family handler.

Handles four common TypeScript compiler errors:

* TS2304 — Cannot find name (missing symbol / import)
* TS2339 — Property does not exist on type
* TS2345 — Argument type mismatch
* TS2554 — Expected N arguments but got M
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_TS2304 = re.compile(
    r"TS2304:\s+Cannot find name\s+['\"](?P<name>[^'\"]+)['\"]"
)
_TS2339 = re.compile(
    r"TS2339:\s+Property\s+['\"](?P<prop>[^'\"]+)['\"]"
    r"\s+does not exist on type\s+['\"]?(?P<type>[^'\".\n]+)"
)
_TS2345 = re.compile(
    r"TS2345:\s+Argument of type\s+['\"](?P<from_type>[^'\"]+)['\"]"
    r"\s+is not assignable to parameter of type\s+['\"](?P<to_type>[^'\"]+)['\"]"
)
_TS2554 = re.compile(
    r"TS2554:\s+Expected\s+(?P<expected>\d+)\s+arguments?,\s+but got\s+(?P<got>\d+)"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TSCandidate:
    rank: int
    description: str
    code_template: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "description": self.description,
            "code_template": self.code_template,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class TSHypothesis:
    kind: str
    sub_kind: str
    explanation: str
    confidence: float
    supporting: tuple[str, ...]
    opposing: tuple[str, ...]
    candidates: tuple[TSCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "sub_kind": self.sub_kind,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "supporting": list(self.supporting),
            "opposing": list(self.opposing),
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class TypeScriptErrorHandler:
    """Diagnose TypeScript compiler errors."""

    family = "typescript"

    def diagnose(self, error_text: str) -> tuple[TSHypothesis, ...]:
        hypotheses: list[TSHypothesis] = []

        m = _TS2304.search(error_text)
        if m:
            hypotheses.extend(self._missing_name(m.group("name")))

        m = _TS2339.search(error_text)
        if m:
            hypotheses.extend(self._missing_property(m.group("prop"), m.group("type").strip()))

        m = _TS2345.search(error_text)
        if m:
            hypotheses.extend(self._type_mismatch(m.group("from_type"), m.group("to_type")))

        m = _TS2554.search(error_text)
        if m:
            hypotheses.extend(
                self._param_count(int(m.group("expected")), int(m.group("got")))
            )

        return tuple(sorted(hypotheses, key=lambda h: -h.confidence))

    # ------------------------------------------------------------------

    @staticmethod
    def _missing_name(name: str) -> list[TSHypothesis]:
        return [
            TSHypothesis(
                kind="typescript_missing_symbol",
                sub_kind="missing_import",
                explanation=(
                    f"'{name}' is not in scope. The most likely cause is a missing import."
                ),
                confidence=0.85,
                supporting=(f"TS2304: Cannot find name '{name}'",),
                opposing=(),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=f"Add import for '{name}'",
                        code_template=f"import {{ {name} }} from '<module>';",
                        confidence=0.80,
                    ),
                    TSCandidate(
                        rank=2,
                        description=f"Define '{name}' before use",
                        code_template=f"const {name} = /* define here */;",
                        confidence=0.60,
                    ),
                    TSCandidate(
                        rank=3,
                        description=f"Check for typo in '{name}'",
                        code_template=f"// Verify spelling of '{name}'",
                        confidence=0.45,
                    ),
                ),
            ),
            TSHypothesis(
                kind="typescript_missing_symbol",
                sub_kind="typo_in_name",
                explanation=(
                    f"'{name}' may be a typo of an existing identifier."
                ),
                confidence=0.60,
                supporting=(f"'{name}' looks like a plausible misspelling",),
                opposing=("Cannot confirm without the full symbol table",),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=f"Correct the spelling of '{name}'",
                        code_template=f"// Fix spelling: was '{name}', should be '...'",
                        confidence=0.55,
                    ),
                ),
            ),
            TSHypothesis(
                kind="typescript_missing_symbol",
                sub_kind="missing_type_declaration",
                explanation=(
                    f"'{name}' may be a global or ambient type that requires a @types package."
                ),
                confidence=0.40,
                supporting=(),
                opposing=("More context needed to confirm",),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=f"Install @types package or add declare const {name}: any",
                        code_template=f"declare const {name}: any; // temporary",
                        confidence=0.35,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _missing_property(prop: str, type_name: str) -> list[TSHypothesis]:
        return [
            TSHypothesis(
                kind="typescript_missing_symbol",
                sub_kind="missing_property",
                explanation=(
                    f"Property '{prop}' does not exist on type '{type_name}'. "
                    "It may be a typo or a property that must be added to the type definition."
                ),
                confidence=0.85,
                supporting=(f"TS2339: Property '{prop}' does not exist on '{type_name}'",),
                opposing=(),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=f"Fix typo in '{prop}'",
                        code_template=f"// Correct property name from '{prop}' to the right name",
                        confidence=0.78,
                    ),
                    TSCandidate(
                        rank=2,
                        description=f"Add '{prop}' to the '{type_name}' interface/type",
                        code_template=(
                            f"interface {type_name} {{\n"
                            f"  {prop}: unknown; // add correct type\n"
                            f"}}"
                        ),
                        confidence=0.60,
                    ),
                    TSCandidate(
                        rank=3,
                        description="Use type assertion as a last resort",
                        code_template=f"(obj as any).{prop}",
                        confidence=0.30,
                    ),
                ),
            ),
            TSHypothesis(
                kind="typescript_missing_symbol",
                sub_kind="wrong_type_used",
                explanation=(
                    f"The variable may be typed as '{type_name}' but actually holds "
                    "a more specific type that does have '{prop}'."
                ),
                confidence=0.50,
                supporting=(),
                opposing=("Requires type-flow analysis to confirm",),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description="Narrow the type with a type guard",
                        code_template=f"if ('{prop}' in obj) {{ obj.{prop}; }}",
                        confidence=0.45,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _type_mismatch(from_type: str, to_type: str) -> list[TSHypothesis]:
        return [
            TSHypothesis(
                kind="typescript_type_mismatch",
                sub_kind="wrong_argument_type",
                explanation=(
                    f"Argument is of type '{from_type}' but the parameter expects '{to_type}'. "
                    "An explicit conversion or the correct type must be used."
                ),
                confidence=0.88,
                supporting=(f"TS2345: '{from_type}' not assignable to '{to_type}'",),
                opposing=(),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=f"Convert the value from {from_type} to {to_type}",
                        code_template=f"// Convert: {from_type} → {to_type}",
                        confidence=0.80,
                    ),
                    TSCandidate(
                        rank=2,
                        description=f"Widen the parameter type to accept {from_type}",
                        code_template=f"function f(arg: {to_type} | {from_type}) {{}}",
                        confidence=0.55,
                    ),
                    TSCandidate(
                        rank=3,
                        description="Use a type assertion (last resort)",
                        code_template=f"f(value as {to_type})",
                        confidence=0.30,
                    ),
                ),
            ),
            TSHypothesis(
                kind="typescript_type_mismatch",
                sub_kind="nullable_mismatch",
                explanation=(
                    f"'{from_type}' includes null/undefined but '{to_type}' does not. "
                    "A null check or non-null assertion may be needed."
                ),
                confidence=0.55,
                supporting=("null/undefined is a common source of TS2345",),
                opposing=("Cannot confirm without full type context",),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description="Add null check before passing the value",
                        code_template="if (value != null) { f(value); }",
                        confidence=0.60,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _param_count(expected: int, got: int) -> list[TSHypothesis]:
        diff = got - expected
        if diff > 0:
            primary = f"Remove {diff} extra argument(s)"
            template = f"// Call with exactly {expected} argument(s)"
        else:
            primary = f"Add {-diff} missing argument(s)"
            template = f"// Provide the missing {-diff} argument(s)"

        return [
            TSHypothesis(
                kind="typescript_type_mismatch",
                sub_kind="wrong_param_count",
                explanation=(
                    f"Function expected {expected} argument(s) but received {got}. "
                    "The call site or function signature must be corrected."
                ),
                confidence=0.90,
                supporting=(f"TS2554: Expected {expected} arguments, got {got}",),
                opposing=(),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description=primary,
                        code_template=template,
                        confidence=0.85,
                    ),
                    TSCandidate(
                        rank=2,
                        description="Make extra parameters optional in the function signature",
                        code_template=f"function f(a: T, b?: T2) {{}}  // make param optional",
                        confidence=0.60,
                    ),
                    TSCandidate(
                        rank=3,
                        description="Use rest parameters if variable arg count is intended",
                        code_template="function f(...args: T[]) {}",
                        confidence=0.45,
                    ),
                ),
            ),
            TSHypothesis(
                kind="typescript_type_mismatch",
                sub_kind="overload_mismatch",
                explanation=(
                    "The call may match a different function overload. "
                    "Check that the correct overload signature is being used."
                ),
                confidence=0.40,
                supporting=(),
                opposing=("Requires overload context to confirm",),
                candidates=(
                    TSCandidate(
                        rank=1,
                        description="Review the function's overload signatures",
                        code_template="// Check all overload signatures for this function",
                        confidence=0.35,
                    ),
                ),
            ),
        ]
