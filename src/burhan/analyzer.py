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
from .scanner import ProjectScanner, ProjectSnapshot, SourceFile, build_code_tree


PYTHON_FRAME = re.compile(r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)')
PYTHON_NAME_ERROR = re.compile(r"NameError:\s+name\s+['\"](?P<name>[^'\"]+)['\"]\s+is not defined")
PYTHON_UNBOUND_ERROR = re.compile(
    r"UnboundLocalError:\s+(?:"
    r"cannot access local variable\s+['\"](?P<name2>[^'\"]+)['\"]"
    r"|local variable\s+['\"](?P<name>[^'\"]+)['\"]\s+referenced before assignment"
    r")"
)
PYTHON_IMPORT_NAME_ERROR = re.compile(
    r"ImportError:\s+cannot import name\s+['\"](?P<name>[^'\"]+)['\"](?:\s+from\s+['\"](?P<module>[^'\"]+)['\"])?"
)
PYTHON_MODULE_ERROR = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+(?:No module named\s+)?['\"](?P<name>[^'\"]+)['\"]"
)
PYTHON_SYNTAX_ERROR = re.compile(r"SyntaxError:\s*(?P<message>.+)")
PYTHON_ATTRIBUTE_ERROR = re.compile(
    r"AttributeError:\s+['\"](?P<object_type>[^'\"]+)['\"] object has no attribute ['\"](?P<name>[^'\"]+)['\"]"
)
PYTHON_TYPE_ERROR_ARG_COUNT = re.compile(
    r"TypeError:\s+(?P<func>\S+)\(\)\s+takes\s+(?P<expected>\d+)\s+positional arguments?\s+but\s+(?P<given>\d+)\s+(?:was|were) given"
)
PYTHON_TYPE_ERROR_NOT_CALLABLE = re.compile(
    r"TypeError:\s+'(?P<type>[^']+)' object is not callable"
)
PYTHON_TYPE_ERROR_BAD_OPERAND = re.compile(
    r"TypeError:\s+unsupported operand type\(s\) for\s+(?P<op>[^:]+):\s+'(?P<type1>[^']+)' and '(?P<type2>[^']+)'"
)
PYTHON_TYPE_ERROR = re.compile(r"TypeError:\s+(?P<message>[^\n]+)")
PYTHON_VALUE_ERROR = re.compile(r"ValueError:\s+(?P<message>[^\n]+)")
PYTHON_INDEX_ERROR = re.compile(r"IndexError:\s+(?P<message>[^\n]+)")
PYTHON_KEY_ERROR = re.compile(r"KeyError:\s+(?P<key>[^\n]+)")
PYTHON_ZERO_DIV_ERROR = re.compile(r"ZeroDivisionError:\s+(?P<message>[^\n]+)")
PYTHON_RECURSION_ERROR = re.compile(r"RecursionError:\s+(?P<message>[^\n]+)")
PYTHON_FILE_NOT_FOUND = re.compile(
    r"FileNotFoundError:\s+(?:\[(?:Errno|WinError)\s+\d+\]\s+[^:]+:\s+)?['\"](?P<path>[^'\"]+)['\"]"
)
PYTHON_OS_ERROR = re.compile(
    r"(?:OSError|IOError):\s+\[(?:Errno|WinError)\s+(?P<errno>\d+)\]\s+(?P<message>[^:\n]+)"
    r"(?::\s+['\"](?P<path>[^'\"]+)['\"])?"
)
TS_DIAGNOSTIC = re.compile(
    r"(?P<file>[^\r\n()]+)\((?P<line>\d+),(?P<column>\d+)\):\s+error\s+"
    r"(?P<code>TS\d+):\s*(?P<message>.+)"
)
TS_UNKNOWN_NAME = re.compile(r"Cannot find name\s+['\"](?P<name>[^'\"]+)['\"]")
TS_PROPERTY_MISSING = re.compile(
    r"Property\s+['\"](?P<name>[^'\"]+)['\"]\s+does not exist on type\s+['\"](?P<type>[^'\"]+)['\"]"
)
TS_ARG_TYPE_MISMATCH = re.compile(
    r"Argument of type\s+'(?P<given>[^']+)'\s+is not assignable to parameter of type\s+'(?P<expected>[^']+)'"
)
TS_WRONG_ARG_COUNT = re.compile(
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but got\s+(?P<given>\d+)"
)
JS_SYMBOL_PATTERN = re.compile(r"\b(?:function|class|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)")
ENGINE_VERSION = "0.7.1"


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
        code_tree = build_code_tree(snapshot)
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
                scan_truncated=snapshot.incomplete,
            ),
            residual_risks=residual_risks,
            questions=questions,
            code_tree=code_tree,
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
            return tuple(dict.fromkeys(JS_SYMBOL_PATTERN.findall(source.content)))
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

        unbound_match = PYTHON_UNBOUND_ERROR.search(error_text)
        if unbound_match:
            name = unbound_match.group("name") or unbound_match.group("name2") or "unknown"
            replacement = self._closest_symbol(name, symbols)
            evidence: tuple[Evidence, ...] = (
                Evidence("runtime", f"UnboundLocalError: المتغير المحلي '{name}' استُخدم قبل إسناد قيمة له", 2.2),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            if replacement:
                evidence += (Evidence("symbol-index", f"يوجد رمز قريب معرف باسم '{replacement}'", 1.3),)
            explanation = f"المتغير المحلي '{name}' استُخدم قبل أن تُسند إليه قيمة"
            if replacement:
                explanation += f"، وأقرب رمز معروف هو '{replacement}'"
            return (self._make_hypothesis(
                "unbound_local_variable", name, explanation, python_location, evidence, replacement, uncertainty=0.07
            ),), ("تحقق من أن المتغير يُسند قبل كل مسار تنفيذ ممكن.",)

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

        import_name_match = PYTHON_IMPORT_NAME_ERROR.search(error_text)
        if import_name_match:
            name = import_name_match.group("name")
            module = import_name_match.group("module") or "unknown"
            evidence = (
                Evidence("runtime", f"ImportError: لا يمكن استيراد الاسم '{name}' من الوحدة '{module}'", 2.3),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "missing_import_name",
                name,
                f"الاسم '{name}' غير موجود في الوحدة '{module}'",
                python_location,
                evidence,
                uncertainty=0.2,
            ),), ("هل الاسم موجود في الإصدار المثبت من الوحدة؟ تحقق من توثيق الوحدة.",)

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

        arg_count_match = PYTHON_TYPE_ERROR_ARG_COUNT.search(error_text)
        if arg_count_match:
            func = arg_count_match.group("func")
            expected = arg_count_match.group("expected")
            given = arg_count_match.group("given")
            evidence = (
                Evidence("runtime", f"TypeError: الدالة '{func}' تتوقع {expected} وسيطًا لكن أُعطيت {given}", 2.2),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "wrong_argument_count",
                func,
                f"الدالة '{func}' تتوقع {expected} وسيطًا لكن أُعطيت {given}",
                python_location,
                evidence,
                uncertainty=0.08,
            ),), (f"راجع توقيع الدالة '{func}' وتأكد من تمرير العدد الصحيح من الوسطاء.",)

        not_callable_match = PYTHON_TYPE_ERROR_NOT_CALLABLE.search(error_text)
        if not_callable_match:
            type_name = not_callable_match.group("type")
            evidence = (
                Evidence("runtime", f"TypeError: كائن من نوع '{type_name}' غير قابل للاستدعاء كدالة", 2.2),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "not_callable",
                type_name,
                f"حاول الكود استدعاء كائن من نوع '{type_name}' كأنه دالة",
                python_location,
                evidence,
                uncertainty=0.1,
            ),), ("تحقق من أن المتغير يشير إلى دالة أو كائن يدعم __call__.",)

        bad_operand_match = PYTHON_TYPE_ERROR_BAD_OPERAND.search(error_text)
        if bad_operand_match:
            op = bad_operand_match.group("op").strip()
            type1 = bad_operand_match.group("type1")
            type2 = bad_operand_match.group("type2")
            evidence = (
                Evidence("runtime", f"TypeError: العملية '{op}' غير مدعومة بين '{type1}' و'{type2}'", 2.2),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "unsupported_operand",
                op,
                f"العملية '{op}' غير مدعومة بين النوعين '{type1}' و'{type2}'",
                python_location,
                evidence,
                uncertainty=0.12,
            ),), (f"تأكد من توافق النوعين قبل إجراء العملية '{op}'.",)

        type_error_match = PYTHON_TYPE_ERROR.search(error_text)
        if type_error_match:
            message = type_error_match.group("message").strip()
            evidence = (
                Evidence("runtime", f"TypeError: {message}", 2.0),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "type_error",
                "type",
                f"خطأ في النوع: {message}",
                python_location,
                evidence,
                uncertainty=0.2,
            ),), ("تحقق من أنواع الوسطاء والقيم المستخدمة في العملية.",)

        value_error_match = PYTHON_VALUE_ERROR.search(error_text)
        if value_error_match:
            message = value_error_match.group("message").strip()
            evidence = (
                Evidence("runtime", f"ValueError: {message}", 2.0),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "value_error",
                "value",
                f"قيمة غير صالحة: {message}",
                python_location,
                evidence,
                uncertainty=0.22,
            ),), ("تحقق من القيم المُمرَّرة وتطابقها مع المدى المسموح به.",)

        index_error_match = PYTHON_INDEX_ERROR.search(error_text)
        if index_error_match:
            message = index_error_match.group("message").strip()
            evidence = (
                Evidence("runtime", f"IndexError: {message}", 2.1),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "index_out_of_range",
                "index",
                f"فهرس خارج النطاق: {message}",
                python_location,
                evidence,
                uncertainty=0.12,
            ),), ("تحقق من حدود القائمة أو المصفوفة قبل الوصول إلى عنصر بفهرس.",)

        key_error_match = PYTHON_KEY_ERROR.search(error_text)
        if key_error_match:
            key = key_error_match.group("key").strip().strip("'\"")
            evidence = (
                Evidence("runtime", f"KeyError: المفتاح '{key}' غير موجود في القاموس", 2.1),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "missing_key",
                key,
                f"المفتاح '{key}' غير موجود في القاموس",
                python_location,
                evidence,
                uncertainty=0.12,
            ),), ("استخدم dict.get(key, default) أو تحقق من وجود المفتاح بـ 'if key in dict' قبل الوصول.",)

        zero_div_match = PYTHON_ZERO_DIV_ERROR.search(error_text)
        if zero_div_match:
            message = zero_div_match.group("message").strip()
            evidence = (
                Evidence("runtime", f"ZeroDivisionError: {message}", 2.3),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "zero_division",
                "divisor",
                f"قسمة على صفر: {message}",
                python_location,
                evidence,
                uncertainty=0.08,
            ),), ("تحقق من قيمة المقسوم عليه وأضف حارسًا قبل عملية القسمة.",)

        recursion_match = PYTHON_RECURSION_ERROR.search(error_text)
        if recursion_match:
            message = recursion_match.group("message").strip()
            evidence = (
                Evidence("runtime", f"RecursionError: {message}", 2.2),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "infinite_recursion",
                "recursion",
                f"عمق التكرار تجاوز الحد الأقصى: {message}",
                python_location,
                evidence,
                uncertainty=0.1,
            ),), ("تحقق من الحالة الأساسية (base case) في الدالة المتكررة.",)

        file_not_found_match = PYTHON_FILE_NOT_FOUND.search(error_text)
        if file_not_found_match:
            path = file_not_found_match.group("path")
            evidence = (
                Evidence("runtime", f"FileNotFoundError: الملف '{path}' غير موجود", 2.3),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "file_not_found",
                path,
                f"الملف '{path}' غير موجود أو المسار غير صحيح",
                python_location,
                evidence,
                uncertainty=0.1,
            ),), ("تحقق من وجود الملف والمسار المستخدم، وتأكد من مجلد العمل الحالي.",)

        os_error_match = PYTHON_OS_ERROR.search(error_text)
        if os_error_match:
            errno_val = os_error_match.group("errno")
            message = os_error_match.group("message").strip()
            path = os_error_match.group("path") or "unknown"
            path_detail = f": '{path}'" if path != "unknown" else ""
            evidence = (
                Evidence("runtime", f"OSError [Errno {errno_val}] {message}{path_detail}", 2.1),
                Evidence("traceback", f"آخر إطار مرتبط بالموقع {python_location or 'غير المحدد'}", 1.2),
            )
            return (self._make_hypothesis(
                "os_error",
                path,
                f"خطأ نظام تشغيل [Errno {errno_val}] {message}{path_detail}",
                python_location,
                evidence,
                uncertainty=0.15,
            ),), ("تحقق من صلاحيات الملف أو المجلد وتأكد من وجود المسار.",)

        ts_match = TS_DIAGNOSTIC.search(error_text)
        if ts_match:
            return self._diagnose_typescript(ts_match, symbols)

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

    def _diagnose_typescript(
        self,
        match: re.Match[str],
        symbols: tuple[str, ...],
    ) -> tuple[tuple[Hypothesis, ...], tuple[str, ...]]:
        code = match.group("code")
        message = match.group("message").strip()
        location = (
            f"{self._normalize_path(match.group('file').strip())}:"
            f"{match.group('line')}:{match.group('column')}"
        )
        unknown_name = TS_UNKNOWN_NAME.search(message)
        missing_property = TS_PROPERTY_MISSING.search(message)
        arg_type_mismatch = TS_ARG_TYPE_MISMATCH.search(message)
        wrong_arg_count = TS_WRONG_ARG_COUNT.search(message)

        if unknown_name:
            target = unknown_name.group("name")
            replacement = self._closest_symbol(target, symbols)
            explanation = f"أبلغ TypeScript عن {code}: {message}"
            if replacement:
                explanation += f"، وأقرب رمز معروف هو '{replacement}'"
            hypothesis = self._make_hypothesis(
                "undefined_name",
                target,
                explanation,
                location,
                (Evidence("typescript", f"{code}: {message}", 2.6),),
                replacement,
                uncertainty=0.08,
            )
            return (hypothesis,), ()

        if code == "TS2322":
            hypothesis = self._make_hypothesis(
                "type_mismatch",
                code,
                f"أبلغ TypeScript عن {code}: {message}",
                location,
                (Evidence("typescript", f"{code}: {message}", 2.6),),
                uncertainty=0.08,
            )
            return (hypothesis,), ()

        if code == "TS2345" or arg_type_mismatch:
            given = arg_type_mismatch.group("given") if arg_type_mismatch else "unknown"
            expected = arg_type_mismatch.group("expected") if arg_type_mismatch else "unknown"
            hypothesis = self._make_hypothesis(
                "argument_type_mismatch",
                code,
                f"نوع الوسيط '{given}' لا يتوافق مع النوع المتوقع '{expected}' ({code})",
                location,
                (Evidence("typescript", f"{code}: {message}", 2.6),),
                uncertainty=0.09,
            )
            return (hypothesis,), ("تحقق من نوع الوسيط المُمرَّر وتوقيع الدالة المستدعاة.",)

        if code == "TS2554" or wrong_arg_count:
            expected_n = wrong_arg_count.group("expected") if wrong_arg_count else "?"
            given_n = wrong_arg_count.group("given") if wrong_arg_count else "?"
            hypothesis = self._make_hypothesis(
                "wrong_argument_count",
                code,
                f"الدالة تتوقع {expected_n} وسيطًا لكن أُعطيت {given_n} ({code})",
                location,
                (Evidence("typescript", f"{code}: {message}", 2.5),),
                uncertainty=0.08,
            )
            return (hypothesis,), ("راجع توقيع الدالة وتأكد من تمرير العدد الصحيح من الوسطاء.",)

        if missing_property:
            property_name = missing_property.group("name")
            type_name = missing_property.group("type")
            hypothesis = self._make_hypothesis(
                "missing_property",
                property_name,
                f"الخاصية '{property_name}' غير موجودة على النوع '{type_name}' ({code})",
                location,
                (Evidence("typescript", f"{code}: {message}", 2.6),),
                uncertainty=0.1,
            )
            return (hypothesis,), ("تحقق من تعريف النوع أو إصدار الحزمة الذي يوفر هذه الخاصية.",)

        hypothesis = self._make_hypothesis(
            "typescript_diagnostic",
            code,
            f"أبلغ TypeScript عن {code}: {message}",
            location,
            (Evidence("typescript", f"{code}: {message}", 2.6),),
            uncertainty=0.08,
        )
        return (hypothesis,), ()

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
        if snapshot.incomplete:
            risks.append("توقف مسح المشروع عند حد الموارد؛ قد يكون السياق ناقصًا")
        if hypothesis.kind == "insufficient_evidence":
            risks.append("السبب الجذري غير محدد بسبب نقص الأدلة")
        elif hypothesis.kind == "unbound_local_variable":
            risks.append("تأكد من إسناد قيمة للمتغير في جميع مسارات التنفيذ الممكنة قبل قراءته")
        elif hypothesis.suggested_replacement:
            risks.append("التعديل المقترح مبني على تشابه اسم ويحتاج تحققًا تنفيذيًا")
        elif hypothesis.kind in ("missing_key", "index_out_of_range"):
            risks.append("الإصلاح يتطلب مراجعة منطق بناء البيانات وليس تعديل سطر واحد فقط")
        elif hypothesis.kind in ("zero_division",):
            risks.append("تأكد من معالجة حالة الصفر في جميع مسارات الكود")
        elif hypothesis.kind == "infinite_recursion":
            risks.append("قد تكون هناك حالات حافّة تؤدي إلى التكرار اللانهائي حتى بعد إضافة الحالة الأساسية")
        elif hypothesis.kind in ("file_not_found", "os_error"):
            risks.append("تأكد من معالجة الأخطاء الاستثنائية للملفات وتجنب المسارات المشفرة مسبقًا")
        elif hypothesis.kind == "missing_import_name":
            risks.append("قد يتطلب الإصلاح تحديث إصدار الحزمة أو تصحيح مسار الاستيراد")
        return tuple(risks)
