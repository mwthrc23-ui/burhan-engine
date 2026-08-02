from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .analyzer import ENGINE_VERSION, BurhanAnalyzer
from .memory import MemoryQuery, RepairEpisode, RepairMemory
from .patcher import DEFAULT_DOCKER_IMAGE, PatchEngine, PatchResult, ProofResult, ProofRunner, inject_test_evidence
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
    repair = subcommands.add_parser("repair", help="شخّص وأنشئ patch آمنًا لمعاينته")
    _add_case_arguments(repair)
    repair.add_argument("--apply", action="store_true", help="طبّق patch بعد اجتياز تحقق V0")
    repair.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    proof = subcommands.add_parser(
        "repair-proof",
        help="أثبت انتقال اختبار موثوق من الفشل إلى النجاح دون تعديل الأصل",
    )
    _add_case_arguments(proof)
    proof.add_argument(
        "--trust-local-tests",
        action="store_true",
        help="أقر بأن أمر الاختبار من مشروع محلي موثوق",
    )
    proof.add_argument("--test-program", choices=("python", "pytest"), default="python")
    proof.add_argument("--test-arg", action="append", default=[])
    proof.add_argument("--timeout", type=float, default=30.0)
    proof.add_argument("--backend", choices=("local", "docker"), default="local")
    proof.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    proof.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    memory_add = subcommands.add_parser("memory-add", help="أضف حالة إصلاح موثقة إلى الذاكرة")
    memory_add.add_argument("--database", type=Path, required=True, help="مسار قاعدة ذاكرة SQLite")
    memory_add.add_argument("--episode", type=Path, required=True, help="ملف RepairEpisode بصيغة JSON")
    memory_add.add_argument("--json", action="store_true", help="أخرج النتيجة بصيغة JSON")
    memory_promote = subcommands.add_parser(
        "memory-promote",
        help=(
            "بوابة ترقية: أضف حالة إصلاح إلى الذاكرة فقط إذا كان إثباتها V2 "
            "وتضمّنت مراجعة بشرية موثّقة"
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
    return parser


def _add_case_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--project", type=Path, required=True, help="مسار المشروع")
    command.add_argument("--goal", required=True, help="هدف المستخدم والقيود المهمة")
    error_group = command.add_mutually_exclusive_group(required=True)
    error_group.add_argument("--error", help="نص الخطأ مباشرة")
    error_group.add_argument("--error-file", type=Path, help="ملف UTF-8 يحتوي رسالة الخطأ")


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
    if args.command == "repair-proof":
        return _repair_proof(args)
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
        if memory_matches:
            _print_memory_matches(memory_matches)
        if patch is not None:
            _print_patch(patch)
    return 0


def _memory_add(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(_read_limited_text(args.episode, limit=1_000_000))
        if not isinstance(payload, dict):
            raise ValueError("episode JSON must contain an object")
        episode = RepairEpisode.from_dict(payload)
        memory = RepairMemory(args.database)
        memory.add(episode)
        result = {"stored": episode.id, "episodes": memory.count()}
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"خطأ: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"أضيفت الحالة: {_terminal_text(episode.id)} | "
            f"إجمالي الحالات: {result['episodes']}"
        )
    return 0


def _memory_promote(args: argparse.Namespace) -> int:
    """بوابة الترقية: تقبل الحالة فقط إذا كان الإثبات V2 وتوجد مراجعة بشرية."""
    review_note = (args.human_review_note or "").strip()
    if not review_note:
        print("خطأ: --human-review-note يجب أن يحتوي على نص غير فارغ", file=sys.stderr)
        return 2
    try:
        episode_payload = json.loads(_read_limited_text(args.episode, limit=1_000_000))
        if not isinstance(episode_payload, dict):
            raise ValueError("episode JSON must contain an object")
        episode = RepairEpisode.from_dict(episode_payload)

        proof_payload = json.loads(_read_limited_text(args.proof, limit=1_000_000))
        if not isinstance(proof_payload, dict):
            raise ValueError("proof JSON must contain an object")

        # الإثبات قد يكون مغلّفاً داخل {"analysis": ..., "proof": ...}
        proof_data = proof_payload.get("proof", proof_payload)

        verified = proof_data.get("verified")
        grade = (proof_data.get("verification") or {}).get("grade", "")

        if not verified:
            raise ValueError(
                f"الإثبات غير مكتمل (verified=false) — "
                "يتطلب memory-promote إثباتًا ناجحًا"
            )
        if grade != "V2":
            raise ValueError(
                f"درجة الإثبات هي '{grade}' لكن بوابة الترقية تتطلب V2. "
                "شغّل repair-proof --backend docker للحصول على V2."
            )

        memory = RepairMemory(args.database)
        memory.add(episode)
        result = {
            "promoted": episode.id,
            "grade": grade,
            "human_review_note": review_note,
            "episodes": memory.count(),
        }
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"خطأ في بوابة الترقية: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"✓ رُقّيت الحالة: {_terminal_text(episode.id)} | "
            f"الدرجة: {grade} | "
            f"إجمالي الحالات: {result['episodes']}"
        )
        print(f"  ملاحظة المراجع: {_terminal_text(review_note)}")
    return 0


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
        proof = ProofRunner().prove(
            args.project,
            analysis.primary,
            test_program=args.test_program,
            test_args=tuple(args.test_arg) or default_args,
            timeout_seconds=args.timeout,
            backend=args.backend,
            docker_image=args.docker_image,
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
        return error
    if error_file is None:
        raise ValueError("error input is required")
    return _read_limited_text(error_file, limit=1_000_000)


if __name__ == "__main__":
    raise SystemExit(main())
