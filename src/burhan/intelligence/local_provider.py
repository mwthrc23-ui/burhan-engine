"""Local intelligence provider – heuristic-only, no external calls.

This provider works entirely offline using simple pattern matching and
static rules.  It is always available and serves as the fallback when
no LLM provider is configured or available.

Output is always classified as ``ConfidenceLevel.ASSUMED``.
"""
from __future__ import annotations

from .provider_base import IntelligenceProvider, IntelligenceRequest, IntelligenceResponse

_VERSION = "1.0.0"

# Simple heuristic hints keyed by error kind
_HINTS: dict[str, str] = {
    "undefined_name": "تحقق من تهجئة الاسم أو استيراد الوحدة التي تعرّفه.",
    "missing_attribute": "تحقق من اسم الخاصية أو الإصدار المستخدم من المكتبة.",
    "syntax_error": "راجع التركيب النحوي في السطر المشار إليه.",
    "wrong_argument_count": "راجع توقيع الدالة وتأكد من عدد الوسطاء.",
    "type_mismatch": "تحقق من أنواع الوسطاء وتوافقها مع توقيع الدالة.",
    "missing_key": "استخدم dict.get() أو تحقق من وجود المفتاح.",
    "index_out_of_range": "تحقق من طول القائمة قبل الوصول إلى عنصر بفهرس.",
    "zero_division": "أضف تحقق من القيمة صفر قبل عملية القسمة.",
    "infinite_recursion": "أضف حالة أساسية (base case) في الدالة المتكررة.",
    "file_not_found": "تحقق من مسار الملف ومجلد العمل الحالي.",
    "missing_import_name": "راجع توثيق الحزمة وتحقق من الإصدار المثبت.",
    "unbound_local_variable": "تأكد من إسناد قيمة للمتغير في جميع مسارات التنفيذ.",
}

_DEFAULT_HINT = "راجع رسالة الخطأ والتتبع الكامل للتعرف على السبب."


class LocalProvider(IntelligenceProvider):
    """Heuristic-only local intelligence provider.

    Always available.  Never contacts external services.
    """

    name = "local_heuristic"
    version = _VERSION

    def is_available(self) -> bool:
        return True

    def provide(self, request: IntelligenceRequest) -> IntelligenceResponse:
        hint = _HINTS.get(request.error_kind, _DEFAULT_HINT)
        return IntelligenceResponse(
            provider_name=self.name,
            provider_version=self.version,
            suggestion=hint,
            confidence_hint=0.4,
            request_fingerprint=request.fingerprint,
            used_external=False,
        )
