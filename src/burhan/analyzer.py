from __future__ import annotations

import ast
import difflib
import hashlib
import re
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from .energy import confidence_from_energy, hypothesis_energy
from .model import AnalysisResult, BirEdge, BirNode, BurhanState, Evidence, Hypothesis, NodeKind, Provenance
from .scanner import ProjectScanner, ProjectSnapshot, SourceFile


PYTHON_FRAME = re.compile(r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)')
PYTHON_NAME_ERROR = re.compile(r"NameError:\s+name\s+['\"](?P<name>[^'\"]+)['\"]\s+is not defined")
PYTHON_MODULE_ERROR = re.compile(r"ModuleNotFoundError:\s+No module named\s+['\"](?P<name>[^'\"]+)['\"]")
PYTHON_SYNTAX_ERROR = re.compile(r"SyntaxError:\s*(?P<message>.+)")
PYTHON_ATTRIBUTE_ERROR = re.compile(
    r"AttributeError:\s+['\"](?P<object_type>[^'\"]+)['\"] object has no attribute ['\"](?P<name>[^'\"]+)['\"]"
)
TS_DIAGNOSTIC = re.compile(
    r"(?P<file>[^\r\n()]+)\((?P<line>\d+),(?P<column>\d+)\):\s+error\s+"
    r"(?P<code>TS\d+):\s*(?P<message>.+)"
)
TS_UNKNOWN_NAME = re.compile(r"Cannot find name\s+['\"](?P<name>[^'\"]+)['\"]")
ENGINE_VERSION = "0.2.0"


class BurhanAnalyzer:
    def __init__(self, scanner: ProjectScanner | None = None) -> None:
        self._scanner = scanner or ProjectScanner()

    def analyze(self, project: Path, goal: str, error_text: str) -> AnalysisResult:
        started = perf_counter()
        if not goal.strip():
            raise ValueError("goal must not be empty")
        if not error_text.strip():
            raise ValueError("error text must not be empty")

        snapshot = self._scanner.scan(project)
        state, symbols = self._build_state(snapshot, goal, error_text)
        hypotheses, questions = self._diagnose(error_text, symbols)
        state = self._attach_hypotheses(state, hypotheses)
        digest = self._input_digest(snapshot, goal, error_text)
        residual_risks = self._residual_risks(snapshot, hypotheses[0])
        elapsed_ms = (perf_counter() - started) * 1000
        return AnalysisResult(
            state=state,
            hypotheses=hypotheses,
            elapsed_ms=elapsed_ms,
            case_id=f"case-{digest[:12]}",
            provenance=Provenance(
                engine_version=ENGINE_VERSION,
                input_fingerprint=f"sha256:{digest}",
                analyzed_files=len(snapshot.files),
                scan_truncated=snapshot.truncated,
            ),
            residual_risks=residual_risks,
            questions=questions,
        )

    def _build_state(
        self, snapshot: ProjectSnapshot, goal: str, error_text: str
    ) -> tuple[BurhanState, tuple[str, ...]]:
        state = BurhanState.empty(goal)
        state = state.with_node(BirNode("goal:primary", NodeKind.GOAL, state.goal))
        for index, constraint in enumerate(self._extract_constraints(goal)):
            constraint_id = f"constraint:{index}"
            state = state.with_node(BirNode(constraint_id, NodeKind.CONSTRAINT, constraint))
            state = state.with_edge(BirEdge("goal:primary", "requires", constraint_id))
        symbols: list[str] = []

        for source in snapshot.files:
            file_id = f"file:{source.relative_path}"
            state = state.with_node(
                BirNode(
                    file_id,
                    NodeKind.FILE,
                    source.relative_path,
                    (("bytes", str(source.size_bytes)),),
                )
            )
            for symbol in self._extract_symbols(source):
                symbols.append(symbol)
                symbol_id = f"symbol:{source.relative_path}:{symbol}"
                state = state.with_node(
                    BirNode(symbol_id, NodeKind.SYMBOL, symbol, (("file", source.relative_path),))
                )
                state = state.with_edge(BirEdge(file_id, "defines", symbol_id))

        error_label = error_text.strip().splitlines()[-1][:240]
        state = state.with_node(BirNode("event:error", NodeKind.EVENT, error_label))
        state = state.with_edge(BirEdge("goal:primary", "responds_to", "event:error"))
        return state, tuple(dict.fromkeys(symbols))

    @staticmethod
    def _extract_symbols(source: SourceFile) -> tuple[str, ...]:
        if source.relative_path.endswith((".py", ".pyi")):
            try:
                tree = ast.parse(source.content)
            except SyntaxError:
                return ()
            names = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            names.extend(
                target.id
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in BurhanAnalyzer._assignment_targets(node)
            )
            return tuple(dict.fromkeys(names))

        if source.relative_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            pattern = re.compile(
                r"\b(?:function|class|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)"
            )
            return tuple(dict.fromkeys(pattern.findall(source.content)))
        return ()

    @staticmethod
    def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> tuple[ast.Name, ...]:
        candidates = node.targets if isinstance(node, ast.Assign) else [node.target]
        return tuple(target for target in candidates if isinstance(target, ast.Name))

    def _diagnose(
        self, error_text: str, symbols: tuple[str, ...]
    ) -> tuple[tuple[Hypothesis, ...], tuple[str, ...]]:
        frames = tuple(PYTHON_FRAME.finditer(error_text))
        python_location = None
        if frames:
            last = frames[-1]
            python_location = f"{self._normalize_path(last.group('file'))}:{last.group('line')}"

        name_match = PYTHON_NAME_ERROR.search(error_text)
        if name_match:
            name = name_match.group("name")
            replacement = self._closest_symbol(name, symbols)
            evidence = (
                Evidence("runtime", f"NameError صرّح بأن الاسم '{name}' غير معرّف", 2.1),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            if replacement:
                evidence += (Evidence("symbol-index", f"يوجد رمز قريب معرف باسم '{replacement}'", 1.4),)
            explanation = f"الاسم '{name}' استُخدم دون تعريف"
            if replacement:
                explanation += f"، وأقرب رمز معروف هو '{replacement}'"
            return (self._make_hypothesis(
                "undefined_name", name, explanation, python_location, evidence, replacement, uncertainty=0.05
            ),), ()

        module_match = PYTHON_MODULE_ERROR.search(error_text)
        if module_match:
            module = module_match.group("name")
            evidence = (Evidence("runtime", f"Python لم يجد الوحدة '{module}'", 2.3),)
            return (self._make_hypothesis(
                "missing_module",
                module,
                f"الوحدة '{module}' غير متاحة في بيئة التشغيل أو أن اسم الاستيراد غير صحيح",
                python_location,
                evidence,
                uncertainty=0.2,
            ),), ("هل الوحدة مدرجة ضمن تبعيات المشروع وبيئة التشغيل الحالية؟",)

        syntax_match = PYTHON_SYNTAX_ERROR.search(error_text)
        if syntax_match:
            message = syntax_match.group("message").strip()
            evidence = (Evidence("parser", f"Python أبلغ عن SyntaxError: {message}", 2.5),)
            return (self._make_hypothesis(
                "syntax_error", "syntax", f"خطأ تركيبي: {message}", python_location, evidence, uncertainty=0.1
            ),), ()

        attribute_match = PYTHON_ATTRIBUTE_ERROR.search(error_text)
        if attribute_match:
            object_type = attribute_match.group("object_type")
            name = attribute_match.group("name")
            evidence = (
                Evidence(
                    "runtime",
                    f"AttributeError صرّح بأن الكائن '{object_type}' لا يملك الخاصية '{name}'",
                    2.3,
                ),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "attribute_error",
                name,
                f"الكائن '{object_type}' لا يعرّف الخاصية أو الدالة '{name}'",
                python_location,
                evidence,
                uncertainty=0.18,
            ),), ("تحقق من إصدار المكتبة واسم API المتاح في هذا الإصدار.",)

        ts_match = TS_DIAGNOSTIC.search(error_text)
        if ts_match:
            code = ts_match.group("code")
            message = ts_match.group("message").strip()
            location = (
                f"{self._normalize_path(ts_match.group('file').strip())}:"
                f"{ts_match.group('line')}:{ts_match.group('column')}"
            )
            unknown_name = TS_UNKNOWN_NAME.search(message)
            target = unknown_name.group("name") if unknown_name else code
            replacement = self._closest_symbol(target, symbols) if unknown_name else None
            kind = "undefined_name" if code == "TS2304" or unknown_name else "type_mismatch" if code == "TS2322" else "typescript_diagnostic"
            evidence = (Evidence("typescript", f"{code}: {message}", 2.6),)
            return (self._make_hypothesis(
                kind,
                target,
                f"أبلغ TypeScript عن {code}: {message}",
                location,
                evidence,
                replacement,
                uncertainty=0.08,
            ),), ()

        evidence = (Evidence("input", "لم تطابق الرسالة نمط خطأ مدعومًا بعد", 0.4),)
        hypothesis = self._make_hypothesis(
            "insufficient_evidence",
            "unknown",
            "لا توجد أدلة كافية لتحديد سبب جذري دون اختراع تفسير",
            None,
            evidence,
            uncertainty=1.0,
            unresolved_constraints=1,
        )
        hypothesis = replace(hypothesis, confidence=min(hypothesis.confidence, 0.35))
        return (hypothesis,), ("أرسل التتبع الكامل للأخطاء وأمر التشغيل الذي تسبب بها.",)

    @staticmethod
    def _make_hypothesis(
        kind: str,
        target: str,
        explanation: str,
        location: str | None,
        evidence: tuple[Evidence, ...],
        suggested_replacement: str | None = None,
        *,
        uncertainty: float = 0.0,
        unresolved_constraints: int = 0,
    ) -> Hypothesis:
        energy = hypothesis_energy(
            evidence,
            uncertainty=uncertainty,
            unresolved_constraints=unresolved_constraints,
            estimated_change_size=1,
        )
        confidence = confidence_from_energy(energy, len(evidence))
        return Hypothesis(
            kind=kind,
            target=target,
            explanation=explanation,
            location=location,
            energy=energy,
            confidence=confidence,
            suggested_replacement=suggested_replacement,
            evidence=evidence,
        )

    @staticmethod
    def _closest_symbol(name: str, symbols: tuple[str, ...]) -> str | None:
        matches = difflib.get_close_matches(name, symbols, n=1, cutoff=0.72)
        return matches[0] if matches else None

    @staticmethod
    def _normalize_path(value: str) -> str:
        return value.replace("\\", "/")

    @staticmethod
    def _attach_hypotheses(state: BurhanState, hypotheses: tuple[Hypothesis, ...]) -> BurhanState:
        updated = state
        for index, hypothesis in enumerate(hypotheses):
            hypothesis_id = f"hypothesis:{index}"
            updated = updated.with_node(
                BirNode(
                    hypothesis_id,
                    NodeKind.HYPOTHESIS,
                    hypothesis.explanation,
                    (("energy", str(hypothesis.energy)), ("confidence", str(hypothesis.confidence))),
                )
            )
            updated = updated.with_edge(BirEdge("event:error", "supports", hypothesis_id, hypothesis.confidence))
        return updated

    @staticmethod
    def _extract_constraints(goal: str) -> tuple[str, ...]:
        candidates: list[str] = []
        for marker in ("لا ", "لا ت", "دون "):
            position = goal.find(marker)
            if position < 0:
                continue
            candidate = re.split(r"[،؛,;.]", goal[position:], maxsplit=1)[0].strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates[:3])

    @staticmethod
    def _input_digest(snapshot: ProjectSnapshot, goal: str, error_text: str) -> str:
        digest = hashlib.sha256()
        digest.update(goal.encode("utf-8"))
        digest.update(b"\0")
        digest.update(error_text.encode("utf-8"))
        for source in snapshot.files:
            digest.update(b"\0")
            digest.update(source.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(source.content.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _residual_risks(snapshot: ProjectSnapshot, hypothesis: Hypothesis) -> tuple[str, ...]:
        risks = ["لم تُشغّل اختبارات المشروع للتحقق من التشخيص بعد"]
        if snapshot.truncated:
            risks.append("توقف مسح المشروع عند حد الموارد؛ قد يكون السياق ناقصًا")
        if hypothesis.kind == "insufficient_evidence":
            risks.append("السبب الجذري غير محدد بسبب نقص الأدلة")
        elif hypothesis.suggested_replacement:
            risks.append("التعديل المقترح مبني على تشابه اسم ويحتاج تحققًا تنفيذيًا")
        return tuple(risks)
