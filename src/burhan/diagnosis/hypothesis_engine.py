"""Multi-candidate hypothesis engine for Burhan.

Replaces the original "first match wins" logic in ``BurhanAnalyzer`` with a
ranked list of hypotheses.  Each hypothesis carries:

* a **confidence** score (0-1),
* a list of **supporting** evidence facts,
* a list of **opposing** evidence facts (to avoid the "similarity = causation"
  fallacy),
* an ``insufficient_evidence`` flag when evidence is too weak to diagnose.

The engine is *additive*: it tries all applicable diagnostic rules and returns
every plausible hypothesis sorted by confidence.  The caller (``BurhanAnalyzer``)
takes the top-N or the full list.

Design rules
------------
* No state mutation – every method returns new objects.
* Textual similarity is recorded but NOT promoted to causal evidence.
* Diagnosis is refused (``insufficient_evidence`` hypothesis only) when no
  rule produces confidence above ``MIN_CONFIDENCE_THRESHOLD``.
* No external network calls.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import replace
from typing import Sequence

from ..evidence import EvidenceGraph
from ..model import Evidence, Hypothesis
from ..energy import confidence_from_energy, hypothesis_energy

# Minimum confidence for a hypothesis to be included in results.
MIN_CONFIDENCE_THRESHOLD = 0.25
# If all hypotheses are below this, emit only the insufficient_evidence one.
SUFFICIENT_EVIDENCE_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# Error-pattern regexes (imported from analyzer but re-declared to keep this
# module self-contained; a future refactor can unify them)
# ---------------------------------------------------------------------------

_PY_FRAME = re.compile(
    r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)'
)
_PY_NAME_ERROR = re.compile(
    r"NameError:\s+name\s+['\"](?P<name>[^'\"]+)['\"]\s+is not defined"
)
_PY_UNBOUND = re.compile(
    r"UnboundLocalError:\s+(?:"
    r"cannot access local variable\s+['\"](?P<name2>[^'\"]+)['\"]"
    r"|local variable\s+['\"](?P<name>[^'\"]+)['\"]\s+referenced before assignment"
    r")"
)
_PY_IMPORT_NAME = re.compile(
    r"ImportError:\s+cannot import name\s+['\"](?P<name>[^'\"]+)['\"]"
    r"(?:\s+from\s+['\"](?P<module>[^'\"]+)['\"])?"
)
_PY_MODULE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+(?:No module named\s+)?['\"](?P<name>[^'\"]+)['\"]"
)
_PY_SYNTAX = re.compile(r"SyntaxError:\s*(?P<message>.+)")
_PY_ATTRIBUTE = re.compile(
    r"AttributeError:\s+['\"](?P<object_type>[^'\"]+)['\"] object has no attribute"
    r" ['\"](?P<name>[^'\"]+)['\"]"
)
_PY_TYPE_ARG = re.compile(
    r"TypeError:\s+(?P<func>\S+)\(\)\s+takes\s+(?P<expected>\d+)\s+positional arguments?"
    r"\s+but\s+(?P<given>\d+)\s+(?:was|were) given"
)
_PY_NOT_CALLABLE = re.compile(r"TypeError:\s+'(?P<type>[^']+)' object is not callable")
_PY_BAD_OPERAND = re.compile(
    r"TypeError:\s+unsupported operand type\(s\) for\s+(?P<op>[^:]+):\s+'(?P<t1>[^']+)' and '(?P<t2>[^']+)'"
)
_PY_TYPE_ERROR = re.compile(r"TypeError:\s+(?P<message>[^\n]+)")
_PY_VALUE_ERROR = re.compile(r"ValueError:\s+(?P<message>[^\n]+)")
_PY_INDEX_ERROR = re.compile(r"IndexError:\s+(?P<message>[^\n]+)")
_PY_KEY_ERROR = re.compile(r"KeyError:\s+(?P<key>[^\n]+)")
_PY_ZERO_DIV = re.compile(r"ZeroDivisionError:\s+(?P<message>[^\n]+)")
_PY_RECURSION = re.compile(r"RecursionError:\s+(?P<message>[^\n]+)")
_PY_FILE_NF = re.compile(
    r"FileNotFoundError:\s+(?:\[(?:Errno|WinError)\s+\d+\]\s+[^:]+:\s+)?['\"](?P<path>[^'\"]+)['\"]"
)
_PY_OS_ERROR = re.compile(
    r"(?:OSError|IOError):\s+\[(?:Errno|WinError)\s+(?P<errno>\d+)\]\s+(?P<message>[^:\n]+)"
    r"(?::\s+['\"](?P<path>[^'\"]+)['\"])?"
)
_TS_DIAGNOSTIC = re.compile(
    r"(?P<file>[^\r\n()]+)\((?P<line>\d+),(?P<column>\d+)\):\s+error\s+"
    r"(?P<code>TS\d+):\s*(?P<message>.+)"
)
_TS_UNKNOWN_NAME = re.compile(r"Cannot find name\s+['\"](?P<name>[^'\"]+)['\"]")
_TS_PROPERTY = re.compile(
    r"Property\s+['\"](?P<name>[^'\"]+)['\"]\s+does not exist on type\s+['\"](?P<type>[^'\"]+)['\"]"
)
_TS_ARG_TYPE = re.compile(
    r"Argument of type\s+'(?P<given>[^']+)'\s+is not assignable to parameter of type\s+'(?P<expected>[^']+)'"
)
_TS_WRONG_ARG = re.compile(
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but got\s+(?P<given>\d+)"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _py_location(error_text: str) -> str | None:
    """Extract the last Python frame location from a traceback."""
    frames = list(_PY_FRAME.finditer(error_text))
    if not frames:
        return None
    last = frames[-1]
    return f"{last.group('file')}:{last.group('line')}"


def _closest(name: str, symbols: Sequence[str]) -> str | None:
    matches = difflib.get_close_matches(name, symbols, n=1, cutoff=0.72)
    return matches[0] if matches else None


def _make(
    kind: str,
    target: str,
    explanation: str,
    location: str | None,
    evidence: tuple[Evidence, ...],
    opposing: tuple[Evidence, ...] = (),
    suggested_replacement: str | None = None,
    uncertainty: float = 0.0,
    unresolved: int = 0,
) -> Hypothesis:
    energy = hypothesis_energy(
        evidence,
        uncertainty=uncertainty,
        unresolved_constraints=unresolved,
        estimated_change_size=1,
    )
    confidence = confidence_from_energy(energy, len(evidence))
    h = Hypothesis(
        kind=kind,
        target=target,
        explanation=explanation,
        location=location,
        energy=energy,
        confidence=confidence,
        suggested_replacement=suggested_replacement,
        evidence=evidence,
    )
    # Store opposing evidence in attributes if present (backward-compatible)
    if opposing:
        h = replace(h, evidence=evidence + tuple(
            Evidence(f"opposing:{ev.source}", f"[معارض] {ev.summary}", -ev.weight)
            for ev in opposing
        ))
    return h


# ---------------------------------------------------------------------------
# HypothesisEngine
# ---------------------------------------------------------------------------

class HypothesisEngine:
    """Generate ranked, multi-candidate hypotheses from an error report.

    Usage::

        engine = HypothesisEngine()
        hypotheses, questions = engine.generate(error_text, symbols)
    """

    def generate(
        self,
        error_text: str,
        symbols: tuple[str, ...],
        graph: EvidenceGraph | None = None,
    ) -> tuple[tuple[Hypothesis, ...], tuple[str, ...]]:
        """Return (hypotheses_sorted_by_confidence_desc, follow_up_questions).

        If evidence is insufficient the only entry will be an
        ``insufficient_evidence`` hypothesis with confidence ≤ 0.35.
        """
        location = _py_location(error_text)
        candidates: list[Hypothesis] = []
        questions: list[str] = []

        # Run every rule; collect all that produce a plausible result.
        for rule in self._RULES:
            result = rule(self, error_text, symbols, location)
            if result is None:
                continue
            hyp, qs = result
            if hyp.kind == "insufficient_evidence":
                continue  # handled at the end
            if hyp.confidence >= MIN_CONFIDENCE_THRESHOLD:
                candidates.append(hyp)
                questions.extend(qs)

        # TypeScript diagnostics (independent path)
        ts_match = _TS_DIAGNOSTIC.search(error_text)
        if ts_match:
            ts_hyps, ts_qs = self._diagnose_typescript(ts_match, symbols)
            candidates.extend(ts_hyps)
            questions.extend(ts_qs)

        # Deduplicate questions
        seen_q: set[str] = set()
        unique_q: list[str] = []
        for q in questions:
            if q not in seen_q:
                seen_q.add(q)
                unique_q.append(q)

        if not candidates:
            fallback = _make(
                "insufficient_evidence",
                "unknown",
                "لا توجد أدلة كافية لتحديد سبب جذري دون اختراع تفسير",
                None,
                (Evidence("input", "لم تطابق الرسالة نمط خطأ مدعومًا بعد", 0.4),),
                uncertainty=1.0,
                unresolved=1,
            )
            fallback = replace(fallback, confidence=min(fallback.confidence, 0.35))
            return (fallback,), (
                "أرسل التتبع الكامل للأخطاء وأمر التشغيل الذي تسبب بها.",
            )

        # Filter out weak candidates, sort by confidence desc
        strong = sorted(
            [h for h in candidates if h.confidence >= MIN_CONFIDENCE_THRESHOLD],
            key=lambda h: (-h.confidence, h.kind),
        )
        return tuple(strong) if strong else (candidates[0],), tuple(unique_q)

    # ------------------------------------------------------------------
    # Individual diagnostic rules
    # Each returns (Hypothesis, questions) or None
    # ------------------------------------------------------------------

    def _rule_name_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_NAME_ERROR.search(error_text)
        if not m:
            return None
        name = m.group("name")
        replacement = _closest(name, symbols)
        explanation = f"الاسم '{name}' غير معرّف في نطاق الاستدعاء"
        opposing: tuple[Evidence, ...] = ()
        if replacement:
            explanation += f"، وأقرب رمز معروف هو '{replacement}'"
        else:
            # Weaker evidence: no alternative found – add opposing note
            opposing = (
                Evidence(
                    "similarity",
                    f"لم يُعثر على رمز مشابه لـ '{name}' في ملفات المشروع",
                    0.3,
                ),
            )
        ev = (
            Evidence("runtime", f"NameError: الاسم '{name}' غير معرّف", 2.5),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "undefined_name", name, explanation, location, ev, opposing, replacement, uncertainty=0.1
        ), ()

    def _rule_unbound_local(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_UNBOUND.search(error_text)
        if not m:
            return None
        name = m.group("name2") or m.group("name") or "unknown"
        ev = (
            Evidence("runtime", f"UnboundLocalError: '{name}' referenced before assignment", 2.4),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "unbound_local_variable",
            name,
            f"المتغير '{name}' يُستخدم قبل إسناد قيمة له",
            location,
            ev,
            uncertainty=0.1,
        ), ("تأكد من إسناد قيمة لـ '{}' في جميع مسارات التنفيذ.".format(name),)

    def _rule_import_name(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_IMPORT_NAME.search(error_text)
        if not m:
            return None
        name = m.group("name")
        module = m.group("module") or "unknown"
        ev = (
            Evidence("runtime", f"ImportError: لا يمكن استيراد '{name}' من '{module}'", 2.5),
        )
        return _make(
            "missing_import_name",
            name,
            f"الرمز '{name}' غير موجود في الوحدة '{module}'",
            location,
            ev,
            uncertainty=0.12,
        ), ("راجع توثيق '{module}' وتأكد من الإصدار المثبت.".format(module=module),)

    def _rule_attribute_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_ATTRIBUTE.search(error_text)
        if not m:
            return None
        obj_type = m.group("object_type")
        attr = m.group("name")
        replacement = _closest(attr, symbols)
        explanation = (
            f"الكائن من نوع '{obj_type}' لا يمتلك الخاصية '{attr}'"
        )
        if replacement:
            explanation += f"، وأقرب رمز موجود هو '{replacement}'"
        ev = (
            Evidence("runtime", f"AttributeError: '{obj_type}'.'{attr}'", 2.3),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "missing_attribute",
            attr,
            explanation,
            location,
            ev,
            suggested_replacement=replacement,
            uncertainty=0.12,
        ), ()

    def _rule_syntax_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_SYNTAX.search(error_text)
        if not m:
            return None
        message = m.group("message").strip()
        ev = (Evidence("parser", f"SyntaxError: {message}", 2.5),)
        return _make(
            "syntax_error", "syntax", f"خطأ تركيبي: {message}", location, ev, uncertainty=0.1
        ), ()

    def _rule_type_arg_count(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_TYPE_ARG.search(error_text)
        if not m:
            return None
        func, expected, given = m.group("func"), m.group("expected"), m.group("given")
        ev = (
            Evidence("runtime", f"TypeError: '{func}' تتوقع {expected} لكن أُعطيت {given}", 2.2),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "wrong_argument_count",
            func,
            f"الدالة '{func}' تتوقع {expected} وسيطًا لكن أُعطيت {given}",
            location,
            ev,
            uncertainty=0.08,
        ), (f"راجع توقيع الدالة '{func}' وتأكد من تمرير العدد الصحيح من الوسطاء.",)

    def _rule_not_callable(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_NOT_CALLABLE.search(error_text)
        if not m:
            return None
        type_name = m.group("type")
        ev = (
            Evidence("runtime", f"TypeError: كائن '{type_name}' غير قابل للاستدعاء", 2.2),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "not_callable",
            type_name,
            f"حاول الكود استدعاء كائن من نوع '{type_name}' كأنه دالة",
            location,
            ev,
            uncertainty=0.1,
        ), ("تحقق من أن المتغير يشير إلى دالة أو كائن يدعم __call__.",)

    def _rule_bad_operand(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_BAD_OPERAND.search(error_text)
        if not m:
            return None
        op, t1, t2 = m.group("op").strip(), m.group("t1"), m.group("t2")
        ev = (
            Evidence("runtime", f"TypeError: العملية '{op}' بين '{t1}' و'{t2}'", 2.2),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make(
            "unsupported_operand",
            op,
            f"العملية '{op}' غير مدعومة بين '{t1}' و'{t2}'",
            location,
            ev,
            uncertainty=0.12,
        ), (f"تأكد من توافق النوعين قبل إجراء العملية '{op}'.",)

    def _rule_type_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        # Only if more-specific type-error rules didn't match
        if _PY_TYPE_ARG.search(error_text) or _PY_NOT_CALLABLE.search(error_text) or \
                _PY_BAD_OPERAND.search(error_text):
            return None
        m = _PY_TYPE_ERROR.search(error_text)
        if not m:
            return None
        msg = m.group("message").strip()
        ev = (
            Evidence("runtime", f"TypeError: {msg}", 2.0),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("type_error", "type", f"خطأ في النوع: {msg}", location, ev, uncertainty=0.2), (
            "تحقق من أنواع الوسطاء والقيم المستخدمة في العملية.",
        )

    def _rule_value_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_VALUE_ERROR.search(error_text)
        if not m:
            return None
        msg = m.group("message").strip()
        ev = (
            Evidence("runtime", f"ValueError: {msg}", 2.0),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("value_error", "value", f"قيمة غير صالحة: {msg}", location, ev, uncertainty=0.22), (
            "تحقق من القيم المُمرَّرة وتطابقها مع المدى المسموح به.",
        )

    def _rule_index_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_INDEX_ERROR.search(error_text)
        if not m:
            return None
        msg = m.group("message").strip()
        ev = (
            Evidence("runtime", f"IndexError: {msg}", 2.1),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("index_out_of_range", "index", f"فهرس خارج النطاق: {msg}", location, ev, uncertainty=0.12), (
            "تحقق من حدود القائمة قبل الوصول إلى عنصر بفهرس.",
        )

    def _rule_key_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_KEY_ERROR.search(error_text)
        if not m:
            return None
        key = m.group("key").strip().strip("'\"")
        ev = (
            Evidence("runtime", f"KeyError: المفتاح '{key}'", 2.1),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("missing_key", key, f"المفتاح '{key}' غير موجود في القاموس", location, ev, uncertainty=0.12), (
            "استخدم dict.get(key, default) أو تحقق من وجود المفتاح أولًا.",
        )

    def _rule_zero_div(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_ZERO_DIV.search(error_text)
        if not m:
            return None
        msg = m.group("message").strip()
        ev = (
            Evidence("runtime", f"ZeroDivisionError: {msg}", 2.3),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("zero_division", "divisor", f"قسمة على صفر: {msg}", location, ev, uncertainty=0.08), (
            "تحقق من قيمة المقسوم عليه وأضف حارسًا.",
        )

    def _rule_recursion(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_RECURSION.search(error_text)
        if not m:
            return None
        msg = m.group("message").strip()
        ev = (
            Evidence("runtime", f"RecursionError: {msg}", 2.2),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("infinite_recursion", "recursion", f"عمق التكرار تجاوز الحد: {msg}", location, ev, uncertainty=0.1), (
            "تحقق من الحالة الأساسية في الدالة المتكررة.",
        )

    def _rule_file_not_found(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_FILE_NF.search(error_text)
        if not m:
            return None
        path = m.group("path")
        ev = (
            Evidence("runtime", f"FileNotFoundError: '{path}'", 2.3),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("file_not_found", path, f"الملف '{path}' غير موجود", location, ev, uncertainty=0.1), (
            "تحقق من وجود الملف والمسار.",
        )

    def _rule_os_error(
        self, error_text: str, symbols: tuple[str, ...], location: str | None
    ) -> tuple[Hypothesis, tuple[str, ...]] | None:
        m = _PY_OS_ERROR.search(error_text)
        if not m:
            return None
        errno_val = m.group("errno")
        msg = m.group("message").strip()
        path = m.group("path") or "unknown"
        ev = (
            Evidence("runtime", f"OSError [{errno_val}] {msg}: '{path}'", 2.1),
            Evidence("traceback", f"آخر إطار: {location or 'غير محدد'}", 1.2),
        )
        return _make("os_error", path, f"خطأ نظام [{errno_val}] {msg}: '{path}'", location, ev, uncertainty=0.15), (
            "تحقق من صلاحيات الملف أو المجلد.",
        )

    # Rule registry – order matters for specificity
    _RULES = [
        _rule_syntax_error,
        _rule_unbound_local,
        _rule_name_error,
        _rule_import_name,
        _rule_attribute_error,
        _rule_type_arg_count,
        _rule_not_callable,
        _rule_bad_operand,
        _rule_type_error,
        _rule_value_error,
        _rule_index_error,
        _rule_key_error,
        _rule_zero_div,
        _rule_recursion,
        _rule_file_not_found,
        _rule_os_error,
    ]

    # ------------------------------------------------------------------
    # TypeScript diagnostics
    # ------------------------------------------------------------------

    def _diagnose_typescript(
        self,
        match: re.Match[str],
        symbols: tuple[str, ...],
    ) -> tuple[list[Hypothesis], list[str]]:
        code = match.group("code")
        message = match.group("message").strip()
        location = (
            f"{match.group('file').strip()}:{match.group('line')}:{match.group('column')}"
        )
        hypotheses: list[Hypothesis] = []
        questions: list[str] = []

        unknown = _TS_UNKNOWN_NAME.search(message)
        missing = _TS_PROPERTY.search(message)
        arg_type = _TS_ARG_TYPE.search(message)
        wrong_n = _TS_WRONG_ARG.search(message)

        ev_base = (Evidence("typescript", f"{code}: {message}", 2.6),)

        if unknown:
            target = unknown.group("name")
            replacement = _closest(target, symbols)
            explanation = f"أبلغ TypeScript عن {code}: {message}"
            if replacement:
                explanation += f"، وأقرب رمز معروف هو '{replacement}'"
            hypotheses.append(_make("undefined_name", target, explanation, location, ev_base, suggested_replacement=replacement, uncertainty=0.08))
        elif code == "TS2322":
            hypotheses.append(_make("type_mismatch", code, f"أبلغ TypeScript عن {code}: {message}", location, ev_base, uncertainty=0.08))
        elif code in ("TS2345",) or arg_type:
            given = arg_type.group("given") if arg_type else "unknown"
            expected = arg_type.group("expected") if arg_type else "unknown"
            hypotheses.append(_make("argument_type_mismatch", code, f"نوع الوسيط '{given}' لا يتوافق مع '{expected}' ({code})", location, ev_base, uncertainty=0.09))
            questions.append("تحقق من نوع الوسيط المُمرَّر وتوقيع الدالة.")
        elif code in ("TS2554",) or wrong_n:
            exp_n = wrong_n.group("expected") if wrong_n else "?"
            giv_n = wrong_n.group("given") if wrong_n else "?"
            hypotheses.append(_make("wrong_argument_count", code, f"الدالة تتوقع {exp_n} وسيطًا لكن أُعطيت {giv_n} ({code})", location, ev_base, uncertainty=0.08))
        elif missing:
            prop = missing.group("name")
            typ = missing.group("type")
            hypotheses.append(_make("missing_property", prop, f"الخاصية '{prop}' غير موجودة على النوع '{typ}' ({code})", location, ev_base, uncertainty=0.1))
        else:
            hypotheses.append(_make("typescript_diagnostic", code, f"أبلغ TypeScript عن {code}: {message}", location, ev_base, uncertainty=0.08))

        return hypotheses, questions
