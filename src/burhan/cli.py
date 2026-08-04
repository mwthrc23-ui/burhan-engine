from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .analyzer import ENGINE_VERSION, BurhanAnalyzer
from .memory import MemoryQuery, RepairMemory
from .model import CodeTreeNode
from .patcher import (
    DEFAULT_DOCKER_IMAGE,
    PYTEST_DOCKER_IMAGE,
    PatchEngine,
    PatchResult,
    ProofConfigurationError,
    ProofInfrastructureError,
    ProofRejected,
    ProofResult,
    ProofRunner,
    inject_test_evidence,
)
from .policy import GatePolicy, evaluate_gate, load_policy, proof_failure_report
from .scanner import ProjectScanner, build_code_tree, is_reparse_path
from .sources import (
    BugsInPySource,
    GitHubPullRequestSource,
    SourceStore,
    SweBenchVerifiedSource,
)


class BurhanArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "the following arguments are required: command" in message:
            message += "\nhint: ابدأ بأحد الأوامر مثل burhan analyze --help"
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    parser = BurhanArgumentParser(
        prog="burhan",
        description="بُرهان: تشخيص برمجي قائم على السياق والأدلة",
        epilog=(
            "أمثلة سريعة:\n"
            "  burhan analyze --project . --goal \"شخّص الخطأ\" --error-file error.txt\n"
            "  burhan repair --project . --goal \"أصلح الخطأ\" --error-file error.txt\n"
            "  burhan repair-proof --project . --goal \"أثبت الإصلاح\" "
            "--error-file error.txt --trust-local-tests"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {ENGINE_VERSION}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="حلل مشروعًا ورسالة خطأ")
    _add_case_arguments(analyze)
    analyze.add_argument("--memory", type=Path, help="قاعدة ذاكرة إصلاحات SQLite")
    analyze.add_argument("--dependency", action="append", default=[], help="اعتماد موجود في سياق الخطأ")
    analyze.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    analyze.add_argument("--code-tree", action="store_true", help="أضف شجرة الكود إلى المخرجات")
    analyze.add_argument("--explain", action="store_true", help="اعرض شرحًا مفصّلًا للتشخيص والدليل بالعربية")
    repair = subcommands.add_parser("repair", help="شخّص وأنشئ patch آمنًا لمعاينته")
    _add_case_arguments(repair)
    repair.add_argument("--apply", action="store_true", help="طبّق patch بعد اجتياز تحقق V0")
    repair.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    repair.add_argument("--explain", action="store_true", help="اعرض شرحًا مفصّلًا للتشخيص والإصلاح بالعربية")
    proof = subcommands.add_parser(
        "repair-proof",
        help="أثبت انتقال اختبار موثوق من الفشل إلى النجاح دون تعديل الأصل",
    )
    _add_case_arguments(proof)
    _add_proof_arguments(proof, backend_default="local")
    proof.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    ci_gate = subcommands.add_parser(
        "ci-gate",
        help="شغّل إثباتًا وقيّمه بسياسة مؤسسية وأصدر تقرير تدقيق آمنًا",
    )
    _add_case_arguments(ci_gate)
    _add_proof_arguments(ci_gate, backend_default="docker")
    ci_gate.add_argument("--policy", type=Path, help="ملف سياسة JSON؛ الافتراضي يتطلب V2")
    ci_gate.add_argument("--report", type=Path, help="اكتب تقرير التدقيق ذريًا إلى ملف JSON")
    ci_gate.add_argument("--json", action="store_true", help="أخرج تقرير القرار بصيغة JSON")
    memory_add = subcommands.add_parser(
        "memory-add",
        help="إضافة JSON مباشرة معطلة مؤقتًا حتى تُفصل الحالات غير الموثوقة عن الذاكرة المرقّاة",
    )
    memory_add.add_argument("--database", type=Path, required=True, help="مسار قاعدة ذاكرة SQLite")
    memory_add.add_argument("--episode", type=Path, required=True, help="ملف RepairEpisode بصيغة JSON")
    memory_add.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    memory_promote = subcommands.add_parser(
        "memory-promote",
        help=(
            "بوابة ترقية V2 معطلة مؤقتًا حتى يمكن إعادة إثبات الحلقة وربطها بالرقعة"
        ),
    )
    memory_promote.add_argument("--database", type=Path, required=True, help="مسار قاعدة ذاكرة SQLite")
    memory_promote.add_argument("--episode", type=Path, required=True, help="ملف RepairEpisode بصيغة JSON")
    memory_promote.add_argument(
        "--proof",
        type=Path,
        required=True,
        help="ملف ProofResult بصيغة JSON (مخرج repair-proof --json)",
    )
    memory_promote.add_argument(
        "--human-review-note",
        required=True,
        metavar="NOTE",
        help="ملاحظة المراجع البشري التي تؤكد صحة الإصلاح (نص غير فارغ)",
    )
    memory_promote.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    memory_search = subcommands.add_parser("memory-search", help="ابحث عن حالة إصلاح مشابهة")
    memory_search.add_argument("--database", type=Path, required=True, help="مسار قاعدة ذاكرة SQLite")
    search_error = memory_search.add_mutually_exclusive_group(required=True)
    search_error.add_argument("--error", help="نص الخطأ مباشرة")
    search_error.add_argument("--error-file", type=Path, help="ملف UTF-8 يحتوي رسالة الخطأ")
    memory_search.add_argument("--language", default="python")
    memory_search.add_argument("--framework", default="pytest")
    memory_search.add_argument("--dependency", action="append", default=[])
    memory_search.add_argument("--limit", type=int, default=5)
    memory_search.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    swebench = subcommands.add_parser(
        "source-import-swebench",
        help="اجمع حالات AttributeError من SWE-bench Verified",
    )
    swebench.add_argument("--database", type=Path, required=True)
    swebench.add_argument("--offset", type=int, default=0)
    swebench.add_argument("--length", type=int, default=100)
    swebench.add_argument(
        "--input",
        type=Path,
        help="ملف استجابة محفوظ للاختبار دون شبكة",
    )
    swebench.add_argument("--json", action="store_true")
    bugsinpy = subcommands.add_parser(
        "source-import-bugsinpy",
        help="اجمع حالة خام محددة من BugsInPy",
    )
    bugsinpy.add_argument("--database", type=Path, required=True)
    bugsinpy.add_argument("--project", required=True)
    bugsinpy.add_argument("--bug", required=True)
    bugsinpy.add_argument(
        "--bundle",
        type=Path,
        help="مجلد محلي يحوي bug.info وbug_patch.txt وrun_test.sh",
    )
    bugsinpy.add_argument("--json", action="store_true")
    github_pr = subcommands.add_parser(
        "source-import-github-pr",
        help="اجمع PR محدد من GitHub كسجل خام غير مرقى",
    )
    github_pr.add_argument("--database", type=Path, required=True)
    github_pr.add_argument("--repo", required=True, help="مثال: owner/repository")
    github_pr.add_argument("--pr", required=True, help="رقم Pull Request")
    github_pr.add_argument(
        "--input",
        type=Path,
        help="ملف JSON محفوظ يحوي issue وpull_request وfiles للاختبار دون شبكة",
    )
    github_pr.add_argument("--json", action="store_true")
    source_search = subcommands.add_parser(
        "source-search",
        help="استرجع رقعًا واختبارات مشابهة من السجلات الخام",
    )
    source_search.add_argument("--database", type=Path, required=True)
    source_error = source_search.add_mutually_exclusive_group(required=True)
    source_error.add_argument("--error")
    source_error.add_argument("--error-file", type=Path)
    source_search.add_argument("--limit", type=int, default=5)
    source_search.add_argument("--json", action="store_true")
    code_tree = subcommands.add_parser(
        "code-tree",
        help="اعرض شجرة الكود الهرمية للمشروع (ملفات ورموز)",
    )
    code_tree.add_argument("--project", type=Path, required=True, help="مسار المشروع")
    code_tree.add_argument(
        "--depth",
        type=int,
        default=0,
        help="أقصى عمق للشجرة (0 = غير محدود)",
    )
    code_tree.add_argument("--json", action="store_true", help="أخرج الشجرة بصيغة JSON")
    doctor = subcommands.add_parser(
        "doctor",
        help="افحص توفر Docker والأدوات والصور والسياسات قبل بدء الإثبات",
    )
    doctor.add_argument("--json", action="store_true", help="أخرج نتيجة الفحص بصيغة JSON")
    return parser


def _add_case_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--project", type=Path, required=True, help="مسار المشروع")
    command.add_argument(
        "--goal",
        type=_goal_text,
        required=True,
        help="هدف المستخدم والقيود المهمة",
    )
    error_group = command.add_mutually_exclusive_group(required=True)
    error_group.add_argument("--error", type=_direct_error_text, help="نص الخطأ مباشرة")
    error_group.add_argument("--error-file", type=Path, help="ملف UTF-8 يحتوي رسالة الخطأ")


def _add_proof_arguments(command: argparse.ArgumentParser, *, backend_default: str) -> None:
    command.add_argument(
        "--trust-local-tests",
        action="store_true",
        help="أقر بأن أمر الاختبار من مشروع محلي موثوق",
    )
    command.add_argument("--test-program", choices=("python", "pytest"), default="python")
    command.add_argument("--test-arg", action="append", default=[])
    command.add_argument("--timeout", type=float, default=30.0)
    command.add_argument(
        "--backend",
        choices=("local", "docker"),
        default=backend_default,
    )
    command.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)


def _goal_text(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > 16_384:
        raise argparse.ArgumentTypeError("goal must be non-empty and at most 16 KiB")
    return value


def _direct_error_text(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > 1_000_000:
        raise argparse.ArgumentTypeError("error text must be non-empty and at most 1 MB")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_signal:
        code = exit_signal.code
        return int(code) if isinstance(code, int) else 1
    if args.command == "memory-add":
        return _memory_add(args)
    if args.command == "memory-promote":
        return _memory_promote(args)
    if args.command == "memory-search":
        return _memory_search(args)
    if args.command == "source-import-swebench":
        return _source_import_swebench(args)
    if args.command == "source-import-bugsinpy":
        return _source_import_bugsinpy(args)
    if args.command == "source-import-github-pr":
        return _source_import_github_pr(args)
    if args.command == "source-search":
        return _source_search(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "code-tree":
        return _code_tree(args)
    if args.command == "repair-proof":
        return _repair_proof(args)
    if args.command == "ci-gate":
        return _ci_gate(args)
    try:
        error_text = _read_error(args.error, args.error_file)
        result = BurhanAnalyzer().analyze(args.project, args.goal, error_text)
        patch = PatchEngine().repair(args.project, result.primary, apply=args.apply) if args.command == "repair" else None
        memory_matches = ()
        if args.command == "analyze" and args.memory:
            memory_matches = RepairMemory(args.memory).search(
                MemoryQuery(
                    error_text=error_text,
                    language="python",
                    test_framework="pytest",
                    dependencies=tuple(args.dependency),
                )
            )
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2

    if args.json:
        payload = result.to_dict()
        if patch is not None:
            payload = {"analysis": payload, "patch": patch.to_dict()}
        elif memory_matches:
            payload = {
                "analysis": payload,
                "memory_matches": [match.to_dict() for match in memory_matches],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_analysis(result)
        if getattr(args, "explain", False):
            _print_explain(result, patch)
        if args.command == "analyze" and getattr(args, "code_tree", False) and result.code_tree is not None:
            print("شجرة الكود:")
            lines: list[str] = []
            _render_code_tree(result.code_tree, prefix="", is_last=True, lines=lines, depth=0, max_depth=None)
            print("\n".join(lines))
        if memory_matches:
            _print_memory_matches(memory_matches)
        if patch is not None:
            _print_patch(patch)
    return 0


def _memory_add(args: argparse.Namespace) -> int:
    """Reject untrusted JSON until storage distinguishes staged and promoted episodes."""
    del args
    print(
        "خطأ: memory-add معطلة مؤقتًا؛ ملفات RepairEpisode المقدمة من المستخدم "
        "لا يجوز أن تتجاوز بوابة الترقية أو تُكتب في الذاكرة الموثوقة مباشرة.",
        file=sys.stderr,
    )
    return 2


def _memory_promote(args: argparse.Namespace) -> int:
    """Fail closed until the proof can be re-run and bound to the episode."""
    del args
    print(
        "خطأ: memory-promote معطلة مؤقتًا؛ ProofRunner لا يستطيع حاليًا إعادة إثبات "
        "نطاق AttributeError وربط الإثبات بالحلقة والرقعة. لا تُقبل ملفات ProofResult "
        "المقدمة من المستخدم بوصفها مصدر ثقة.",
        file=sys.stderr,
    )
    return 2


def _repair_proof(args: argparse.Namespace) -> int:
    if not args.trust_local_tests:
        print(
            "خطأ: يتطلب الإثبات إقرار --trust-local-tests لأن الاختبار ينفذ كود المشروع",
            file=sys.stderr,
        )
        return 2
    try:
        error_text = _read_error(args.error, args.error_file)
        analysis = BurhanAnalyzer().analyze(args.project, args.goal, error_text)
        default_args = ("app.py",) if args.test_program == "python" else ("-q",)
        docker_image = _resolve_proof_docker_image(
            test_program=args.test_program,
            backend=args.backend,
            docker_image=args.docker_image,
        )
        proof = ProofRunner().prove(
            args.project,
            analysis.primary,
            test_program=args.test_program,
            test_args=tuple(args.test_arg) or default_args,
            timeout_seconds=args.timeout,
            backend=args.backend,
            docker_image=docker_image,
        )
        # أعد نتيجة الاختبار إلى BIR كأدلة
        analysis = inject_test_evidence(analysis, proof)
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        print(f"لم يثبت الإصلاح: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {"analysis": analysis.to_dict(), "proof": proof.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_analysis(analysis)
        _print_proof(proof)
    return 0


def _ci_gate(args: argparse.Namespace) -> int:
    if not args.trust_local_tests:
        print(
            "خطأ: تتطلب بوابة CI إقرار --trust-local-tests لأن الاختبار ينفذ كود المشروع",
            file=sys.stderr,
        )
        return 2

    try:
        policy = load_policy(args.policy) if args.policy is not None else GatePolicy()
        report_path = _validate_gate_report_target(
            args.report,
            policy_path=args.policy,
            error_path=args.error_file,
        )
        error_text = _read_error(args.error, args.error_file)
        default_args = ("app.py",) if args.test_program == "python" else ("-q",)
        docker_image = _resolve_proof_docker_image(
            test_program=args.test_program,
            backend=args.backend,
            docker_image=args.docker_image,
        )
        test_args = tuple(args.test_arg) or default_args
        project_fingerprint = ProofRunner.fingerprint_project(
            args.project, backend=args.backend
        )
        analysis = BurhanAnalyzer().analyze(args.project, args.goal, error_text)
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        del error
        print("خطأ في إعداد بوابة CI [configuration_error]", file=sys.stderr)
        return 2
    except Exception:
        print("خطأ في إعداد بوابة CI [internal_error]", file=sys.stderr)
        return 2

    try:
        proof = ProofRunner().prove(
            args.project,
            analysis.primary,
            test_program=args.test_program,
            test_args=test_args,
            timeout_seconds=args.timeout,
            backend=args.backend,
            docker_image=docker_image,
            expected_project_fingerprint=project_fingerprint,
        )
        analysis = inject_test_evidence(analysis, proof)
        report = evaluate_gate(analysis, proof, policy)
    except ProofRejected as error:
        report = proof_failure_report(
            analysis,
            policy,
            backend=args.backend,
            command=(args.test_program,) + test_args,
            runtime=docker_image if args.backend == "docker" else sys.version.split()[0],
            project_manifest_fingerprint=project_fingerprint,
        )
        try:
            _emit_gate_report(report.to_dict(), path=report_path, as_json=args.json)
        except (OSError, UnicodeError, ValueError) as report_error:
            del report_error
            print("خطأ في كتابة تقرير بوابة CI [report_write_failed]", file=sys.stderr)
            return 2
        del error
        print("رفضت بوابة CI الإثبات [proof_rejected]", file=sys.stderr)
        return 1
    except (
        OSError,
        ProofConfigurationError,
        ProofInfrastructureError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ) as error:
        code = (
            "proof_infrastructure_error"
            if isinstance(error, (OSError, ProofInfrastructureError))
            else "proof_configuration_error"
        )
        print(f"خطأ في تشغيل بوابة CI [{code}]", file=sys.stderr)
        return 2
    except Exception:
        print("خطأ في تشغيل بوابة CI [internal_error]", file=sys.stderr)
        return 2

    try:
        _emit_gate_report(report.to_dict(), path=report_path, as_json=args.json)
    except (OSError, UnicodeError, ValueError) as error:
        del error
        print("خطأ في كتابة تقرير بوابة CI [report_write_failed]", file=sys.stderr)
        return 2
    return 0 if report.passed else 1


def _validate_gate_report_target(
    report_path: Path | None,
    *,
    policy_path: Path | None,
    error_path: Path | None,
) -> Path | None:
    if report_path is None:
        return None
    absolute = Path(os.path.abspath(report_path.expanduser()))
    if absolute.suffix.lower() != ".json":
        raise ValueError("report path must end with .json")
    if os.path.lexists(absolute):
        raise ValueError("report path must not already exist")
    for parent in absolute.parents:
        if parent.is_symlink() or is_reparse_path(parent):
            raise ValueError("report path must not pass through a symlink or junction")
    if not absolute.parent.is_dir():
        raise ValueError("report parent directory does not exist")
    protected = tuple(
        Path(os.path.abspath(path.expanduser()))
        for path in (policy_path, error_path)
        if path is not None
    )
    if absolute in protected:
        raise ValueError("report path must not overwrite the policy or error file")
    return absolute


def _emit_gate_report(payload: dict[str, object], *, path: Path | None, as_json: bool) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path is not None:
        if os.path.lexists(path):
            raise ValueError("report path must not already exist")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".burhan-gate-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError as error:
                raise ValueError("report path appeared during report generation") from error
        finally:
            temporary_path.unlink(missing_ok=True)
    if as_json:
        print(encoded, end="")
        return
    print(f"قرار بوابة CI: {str(payload['decision']).upper()}")
    print(f"القضية: {_terminal_text(str(payload['case_id']))}")
    violations = payload.get("violations", [])
    if isinstance(violations, list):
        for item in violations:
            if isinstance(item, dict):
                print(f"- {_terminal_text(str(item.get('code', 'policy_denied')))}")


def _resolve_proof_docker_image(*, test_program: str, backend: str, docker_image: str) -> str:
    if backend != "docker":
        return docker_image
    selected_image = docker_image
    if test_program == "pytest" and docker_image == DEFAULT_DOCKER_IMAGE:
        selected_image = PYTEST_DOCKER_IMAGE
    if test_program == "pytest" and (
        not selected_image
        or selected_image == DEFAULT_DOCKER_IMAGE
        or selected_image.endswith("@sha256:" + ("0" * 64))
    ):
        raise ValueError(
            "اختبارات pytest داخل Docker تتطلب صورة pytest مثبتة ببصمة sha256 صالحة. "
            "مرر --docker-image بصورة pytest مثبتة أو حدّث PYTEST_DOCKER_IMAGE."
        )
    return selected_image


def _memory_search(args: argparse.Namespace) -> int:
    try:
        error_text = _read_error(args.error, args.error_file)
        matches = RepairMemory(args.database).search(
            MemoryQuery(
                error_text=error_text,
                language=args.language,
                test_framework=args.framework,
                dependencies=tuple(args.dependency),
            ),
            limit=args.limit,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"matches": [item.to_dict() for item in matches]}, ensure_ascii=False, indent=2))
    elif not matches:
        print("لا توجد حالة موثقة مشابهة ضمن نطاق الذاكرة الحالي.")
    else:
        for index, match in enumerate(matches, start=1):
            print(
                f"{index}. {_terminal_text(match.episode.title)} | "
                f"التشابه: {match.score:.0%}"
            )
            print(f"   السبب: {_terminal_text(match.episode.root_cause)}")
            print(
                f"   النمط: {_terminal_text(match.episode.patch_pattern.from_value)} -> "
                f"{_terminal_text(match.episode.patch_pattern.to_value)}"
            )
            print(f"   التحقق: {match.episode.verification.grade}")
    return 0


def _source_import_swebench(args: argparse.Namespace) -> int:
    try:
        source = SweBenchVerifiedSource()
        rows = (
            _read_swebench_rows(args.input)
            if args.input is not None
            else source.fetch(offset=args.offset, length=args.length)
        )
        store = SourceStore(args.database)
        stored = 0
        candidates = 0
        relevant = 0
        for row in rows:
            record = source.to_record(row)
            if record.error_text is None:
                continue
            relevant += 1
            if record.classification_status == "attribute_error_candidate":
                candidates += 1
            if store.add(record):
                stored += 1
        result = {
            "source": "SWE-bench_Verified",
            "fetched": len(rows),
            "attribute_error_records": relevant,
            "attribute_error_candidates": candidates,
            "stored_raw": stored,
            "source_records": store.count(),
            "source_versions": store.version_count(),
            "promoted": 0,
        }
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    _print_json_or_summary(args.json, result)
    return 0


def _source_import_bugsinpy(args: argparse.Namespace) -> int:
    try:
        source = BugsInPySource()
        if args.bundle is None:
            record = source.fetch(project=args.project, bug_id=args.bug)
        else:
            files = {
                name: _read_limited_text(args.bundle / name, limit=5_000_000)
                for name in ("bug.info", "bug_patch.txt", "run_test.sh")
            }
            record = source.to_record(
                project=args.project,
                bug_id=args.bug,
                files=files,
            )
        store = SourceStore(args.database)
        stored = int(store.add(record))
        result = {
            "source": "BugsInPy",
            "source_id": record.source_id,
            "classification_status": record.classification_status,
            "stored_raw": stored,
            "source_records": store.count(),
            "source_versions": store.version_count(),
            "promoted": 0,
        }
    except (OSError, UnicodeError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    _print_json_or_summary(args.json, result)
    return 0


def _source_import_github_pr(args: argparse.Namespace) -> int:
    try:
        source = GitHubPullRequestSource()
        if args.input is None:
            record = source.fetch(repository=args.repo, pull_number=args.pr)
        else:
            payload = json.loads(_read_limited_text(args.input, limit=20_000_000))
            if not isinstance(payload, dict):
                raise ValueError("GitHub PR input must be an object")
            issue = payload.get("issue")
            pull_request = payload.get("pull_request")
            files = payload.get("files")
            if (
                not isinstance(issue, dict)
                or not isinstance(pull_request, dict)
                or not isinstance(files, list)
                or not all(isinstance(item, dict) for item in files)
            ):
                raise ValueError("GitHub PR input has an unexpected schema")
            record = source.to_record(
                repository=args.repo,
                pull_number=args.pr,
                issue=issue,
                pull_request=pull_request,
                files=files,
            )
        store = SourceStore(args.database)
        stored = int(store.add(record))
        result = {
            "source": "GitHub PR",
            "source_id": record.source_id,
            "classification_status": record.classification_status,
            "stored_raw": stored,
            "source_records": store.count(),
            "source_versions": store.version_count(),
            "promoted": 0,
        }
    except (OSError, UnicodeError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    _print_json_or_summary(args.json, result)
    return 0


def _source_search(args: argparse.Namespace) -> int:
    try:
        error_text = _read_error(args.error, args.error_file)
        matches = SourceStore(args.database).search(error_text, limit=args.limit)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    result = {"matches": [match.to_dict() for match in matches]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not matches:
        print("لا توجد حالة مصدر مشابهة ضمن النطاق الحالي.")
    else:
        for index, match in enumerate(matches, start=1):
            record = match.record
            print(
                f"{index}. {_terminal_text(record.source_id)} | "
                f"التشابه: {match.score:.0%}"
            )
            print("   الحالة: مرشح من المصدر، لم يُتحقق منه محليًا")
            print(f"   حالة السبب: {record.root_cause_status}")
            print(f"   اختبار المصدر: {_terminal_text(record.test_command)}")
            print("   الرقعة الكاملة متاحة في حقل solution_patch عند استخدام --json")
    return 0


def _read_swebench_rows(path: Path) -> tuple[dict[str, object], ...]:
    payload = json.loads(_read_limited_text(path, limit=20_000_000))
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError("SWE-bench input must contain a rows array")
    rows: list[dict[str, object]] = []
    for item in payload["rows"]:
        row = item.get("row") if isinstance(item, dict) else None
        if not isinstance(row, dict):
            raise ValueError("each SWE-bench row must be an object")
        rows.append(row)
    return tuple(rows)


def _read_limited_text(path: Path, *, limit: int) -> str:
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"input file exceeds {limit} bytes")
    return payload.decode("utf-8")


def _terminal_text(value: str, *, multiline: bool = False) -> str:
    bidi_controls = {
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
    safe: list[str] = []
    for character in value:
        codepoint = ord(character)
        if multiline and character in {"\n", "\r", "\t"}:
            safe.append(character)
        elif codepoint < 32 or 127 <= codepoint <= 159 or codepoint in bidi_controls:
            width = 4 if codepoint <= 0xFFFF else 8
            safe.append(f"\\u{codepoint:0{width}x}")
        else:
            safe.append(character)
    return "".join(safe)


def _print_json_or_summary(as_json: bool, result: dict[str, object]) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"المصدر: {result['source']} | سجلات جديدة: {result['stored_raw']} | "
            f"إجمالي الخام: {result['source_records']} | "
            f"نسخ محفوظة: {result.get('source_versions', result['source_records'])} | "
            f"مرقاة: {result['promoted']}"
        )


def _print_analysis(result: object) -> None:
    primary = result.primary
    print(f"القضية: {_terminal_text(result.case_id)}")
    print(f"التشخيص: {_terminal_text(primary.explanation)}")
    print(f"الموقع: {_terminal_text(primary.location or 'غير محدد')}")
    print(f"الثقة: {primary.confidence:.0%} | الطاقة: {primary.energy:.3f}")
    print(
        "السياق: "
        f"{result.provenance.analyzed_files} ملف"
        f"{' (المسح غير مكتمل)' if result.provenance.scan_truncated else ''}"
    )
    if primary.suggested_replacement:
        print(
            f"التعديل المرشح: {_terminal_text(primary.target)} -> "
            f"{_terminal_text(primary.suggested_replacement)}"
        )
    print("الأدلة:")
    for evidence in primary.evidence:
        print(
            f"- [{_terminal_text(evidence.source)}] "
            f"{_terminal_text(evidence.summary)}"
        )
    for question in result.questions:
        print(f"سؤال مطلوب: {_terminal_text(question)}")
    for risk in result.residual_risks:
        print(f"مخاطرة متبقية: {_terminal_text(risk)}")
    print(f"زمن التحليل: {result.elapsed_ms:.3f} ms")


def _print_patch(patch: PatchResult) -> None:
    print(f"التحقق: {patch.verification.grade}")
    print(f"الحالة: {'طُبق' if patch.applied else 'معاينة فقط'}")
    print(_terminal_text(patch.diff, multiline=True))


def _print_proof(proof: ProofResult) -> None:
    print(f"درجة الإثبات: {proof.verification.grade}")
    print(f"البيئة: {_terminal_text(proof.backend)} | {_terminal_text(proof.runtime)}")
    print(f"قبل الرقعة: exit={proof.before.exit_code}")
    print(f"بعد الرقعة: exit={proof.after.exit_code}")
    print(f"المشروع الأصلي دون تغيير: {'نعم' if proof.original_unchanged else 'لا'}")
    print(_terminal_text(proof.patch.diff, multiline=True))


def _print_memory_matches(matches: object) -> None:
    print("حالات موثقة مشابهة:")
    for index, match in enumerate(matches, start=1):
        print(
            f"{index}. {_terminal_text(match.episode.title)} | "
            f"التشابه: {match.score:.0%}"
        )
        print(f"   السبب السابق: {_terminal_text(match.episode.root_cause)}")
        print(
            f"   نمط الإصلاح: {_terminal_text(match.episode.patch_pattern.from_value)} -> "
            f"{_terminal_text(match.episode.patch_pattern.to_value)}"
        )
        print(f"   درجة الدليل: {match.episode.verification.grade}")


def _read_error(error: str | None, error_file: Path | None) -> str:
    if error is not None:
        if len(error.encode("utf-8")) > 1_000_000:
            raise ValueError("error text exceeds 1000000 bytes")
        return error
    if error_file is None:
        raise ValueError("error input is required")
    return _read_limited_text(error_file, limit=1_000_000)


def _doctor(args: argparse.Namespace) -> int:
    """Check Docker availability, tool versions, and policy configuration."""
    import shutil

    checks: dict[str, object] = {}

    # Docker availability
    docker_path = shutil.which("docker")
    checks["docker_available"] = docker_path is not None
    checks["docker_path"] = docker_path or "غير موجود"

    # Python version
    import sys as _sys
    checks["python_version"] = _sys.version.split()[0]
    checks["python_path"] = _sys.executable

    # Burhan engine version
    checks["burhan_version"] = ENGINE_VERSION

    # Default Docker images
    checks["default_docker_image"] = DEFAULT_DOCKER_IMAGE
    checks["pytest_docker_image"] = PYTEST_DOCKER_IMAGE

    # Digest validation
    from .patcher import PINNED_DOCKER_IMAGE_PATTERN
    checks["default_image_pinned"] = bool(PINNED_DOCKER_IMAGE_PATTERN.fullmatch(DEFAULT_DOCKER_IMAGE))

    # Intelligence providers
    try:
        from .intelligence import LocalProvider
        local = LocalProvider()
        checks["local_intelligence_available"] = local.is_available()
    except Exception as exc:
        checks["local_intelligence_available"] = False
        checks["local_intelligence_error"] = str(exc)

    all_ok = all(
        v is True or (isinstance(v, bool) and v)
        for k, v in checks.items()
        if k.endswith("_available") or k.endswith("_pinned")
    )

    if getattr(args, "json", False):
        print(json.dumps({"status": "ok" if all_ok else "warning", "checks": checks}, ensure_ascii=False, indent=2))
    else:
        print(f"{'✓' if all_ok else '⚠'} فحص بُرهان Doctor")
        print(f"  الإصدار: {checks['burhan_version']}")
        print(f"  Python:  {checks['python_version']} ({checks['python_path']})")
        docker_ok = checks["docker_available"]
        print(f"  Docker:  {'متوفر ✓' if docker_ok else 'غير متوفر ✗'}")
        pinned = checks["default_image_pinned"]
        print(f"  الصورة الافتراضية مثبتة: {'نعم ✓' if pinned else 'لا ✗'}")
        intel_ok = checks.get("local_intelligence_available", False)
        print(f"  مزود الذكاء المحلي: {'جاهز ✓' if intel_ok else 'غير متاح ✗'}")
        if not all_ok:
            print("  تحذير: بعض المكونات غير جاهزة – تحقق من إعداد البيئة.")

    return 0 if all_ok else 1


def _print_explain(result: object, patch: object | None) -> None:
    """Print a structured Arabic explanation of the diagnosis and repair."""
    from .model import AnalysisResult
    from .patcher import PatchResult
    if not isinstance(result, AnalysisResult):
        return
    primary = result.primary
    print()
    print("=" * 60)
    print("  شرح التشخيص (--explain)")
    print("=" * 60)
    print(f"ماذا حدث؟\n  {primary.explanation}")
    print(f"\nالسبب المرجح:\n  نوع الخطأ: {primary.kind}  |  الهدف: {primary.target}")
    if primary.location:
        print(f"  الموقع: {primary.location}")
    print(f"\nمستوى الثقة: {primary.confidence:.0%}  |  طاقة الافتراض: {primary.energy:.2f}")
    if primary.evidence:
        print("\nالأدلة:")
        for ev in primary.evidence:
            prefix = "  [دعم]" if not ev.source.startswith("opposing:") else "  [معارض]"
            print(f"{prefix} {ev.source}: {ev.summary}")
    if primary.suggested_replacement:
        print(f"\nالاستبدال المقترح: '{primary.target}' → '{primary.suggested_replacement}'")
    if result.residual_risks:
        print("\nالمخاطر المتبقية:")
        for risk in result.residual_risks:
            print(f"  ⚠ {risk}")
    if result.questions:
        print("\nأسئلة للمتابعة:")
        for q in result.questions:
            print(f"  ? {q}")
    if isinstance(patch, PatchResult):
        print(f"\nالتغييرات: {len(patch.changed_files)} ملف")
        for f in patch.changed_files:
            print(f"  - {f}")
    else:
        print("\nما لم يُثبت بعد:\n  لم تُشغَّل اختبارات التحقق.")
    print("=" * 60)


def _code_tree(args: argparse.Namespace) -> int:
    try:
        snapshot = ProjectScanner().scan(args.project)
        tree = build_code_tree(snapshot)
    except (OSError, ValueError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    max_depth = args.depth if args.depth > 0 else None
    if args.json:
        print(json.dumps(_code_tree_to_dict(tree, max_depth, 0), ensure_ascii=False, indent=2))
    else:
        lines: list[str] = []
        _render_code_tree(tree, prefix="", is_last=True, lines=lines, depth=0, max_depth=max_depth)
        print("\n".join(lines))
    return 0


def _code_tree_to_dict(node: CodeTreeNode, max_depth: int | None, current: int) -> dict[str, object]:
    result: dict[str, object] = {"name": node.name, "kind": node.kind}
    if max_depth is None or current < max_depth:
        result["children"] = [
            _code_tree_to_dict(child, max_depth, current + 1)
            for child in node.children
        ]
    else:
        result["children"] = []
    return result


def _render_code_tree(
    node: CodeTreeNode,
    prefix: str,
    is_last: bool,
    lines: list[str],
    depth: int,
    max_depth: int | None,
) -> None:
    connector = "\\-- " if is_last else "|-- "
    kind_tag = f" [{node.kind}]" if node.kind not in ("directory", "file") else ""
    lines.append(f"{prefix}{connector}{_terminal_text(node.name)}{kind_tag}")
    if max_depth is not None and depth >= max_depth:
        return
    child_prefix = prefix + ("    " if is_last else "|   ")
    for index, child in enumerate(node.children):
        _render_code_tree(
            child,
            child_prefix,
            index == len(node.children) - 1,
            lines,
            depth + 1,
            max_depth,
        )


if __name__ == "__main__":
    raise SystemExit(main())
