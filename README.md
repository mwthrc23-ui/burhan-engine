# بُرهان

بُرهان محرك تجريبي يحوّل هدف المستخدم والكود ورسالة الخطأ إلى حالة BIR مترابطة، ثم يرتب فرضيات السبب الجذري باستخدام الأدلة ودالة طاقة.

هذه النسخة `0.4.0` هي إثبات فكرة قابل للتجربة: التحليل يعمل محليًا ولا يرسل ملفات مشروعك إلى أي خدمة، وجامع المصادر يتصل فقط بمضيفي SWE-bench وGitHub المسموحين صراحة ولا يحتاج مفاتيح API. النطاق الأول هو Python + pytest + AttributeError.

## تجربة كاملة بنقرة واحدة

يشغّل الأمر التالي التجربة على نسخة مؤقتة: تحليل الخطأ، معاينة patch، تطبيقه، ثم تشغيل الكود المصحح. لا يغيّر ملف المثال الأصلي.

```powershell
cd C:\Users\tkssy\OneDrive\ドキュメント\Burhan
powershell -ExecutionPolicy Bypass -File scripts\try-burhan.ps1
```

ولتجربة ذاكرة الإصلاح:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\try-repair-memory.ps1
```

## ما يعمل الآن

- فهرسة آمنة ومحدودة لملفات Python وTypeScript/JavaScript وبعض ملفات الإعداد والتوثيق.
- استبعاد `.env` والمفاتيح الخاصة ومجلدات `.git` و`node_modules` والبناء والتغطية.
- استخراج رموز Python عبر AST، واستخراج أولي لرموز TypeScript/JavaScript.
- تشخيص `NameError` و`ModuleNotFoundError` و`SyntaxError` في Python.
- تشخيص أخطاء TypeScript القياسية، ومنها `TS2304` و`TS2322`.
- اقتراح رمز قريب عند وجود خطأ إملائي في اسم Python.
- إخراج بشري عربي أو JSON يتضمن BIR والأدلة والثقة والطاقة وزمن التحليل.
- رقم قضية ثابت وبصمة SHA-256 ومدى اكتمال المسح والمخاطر المتبقية.
- استخراج القيود الصريحة من الهدف، مثل «لا تغيّر الواجهة».
- أمر `repair` ينشئ unified diff ويمنحه درجة `V0` بعد فحص النطاق والسطر وصياغة Python.
- المعاينة هي الوضع الافتراضي؛ الكتابة تحتاج الخيار الصريح `--apply`.
- التوقف وطلب دليل إضافي بدل اختراع سبب غير مدعوم.
- تخزين حالات `RepairEpisode` في SQLite مع السبب ونمط الإصلاح والاختبار والمصدر.
- البحث عن حالات `AttributeError` مشابهة حسب الخاصية واللغة والإطار والاعتماديات.
- ربط نتائج ذاكرة الإصلاح بالتشخيص الجديد عبر `analyze --memory`.
- جمع حالات حقيقية محدودة من SWE-bench Verified مع الوصف والرقعة ورقعة الاختبار و`FAIL_TO_PASS`.
- جمع حزمة BugsInPy المحددة مع commit والرقعة وأمر الاختبار، من دون تنفيذ أي كود وارد من المصدر.
- جمع Pull Request محدد من GitHub مع الوصف والرقع كـ`source_record` خام غير مرقى.
- فصل `source_records` الخام عن `repair_episodes` الموثقة، ومنع الحالة غير المصنفة من الظهور كإصلاح موثوق.
- حفظ تغيرات المصدر كسجل نسخ append-only يعتمد على SHA-256 بدل استبدال الدليل السابق بصمت.
- استرجاع رقعة واختبار مشابهين عبر `source-search` مع وسم صريح بأنها مرشحة لم تُختبر محليًا.

## ذاكرة الإصلاح

النطاق الأول مقيد عمدًا إلى:

```text
Python + pytest + AttributeError
```

أضف حالة موثقة:

```powershell
python -m burhan memory-add `
  --database repair-memory.sqlite3 `
  --episode examples\repair-memory\episode-send-api.json
```

ابحث عن حالة مشابهة:

```powershell
python -m burhan memory-search `
  --database repair-memory.sqlite3 `
  --error-file examples\repair-memory\error.txt `
  --language python `
  --framework pytest `
  --dependency demo-client
```

اربط الذاكرة بتحليل مشروع جديد:

```powershell
python -m burhan analyze `
  --project examples\repair-memory\project `
  --goal "شخّص الخطأ باستخدام ذاكرة الإصلاح" `
  --error-file examples\repair-memory\error.txt `
  --memory repair-memory.sqlite3 `
  --dependency demo-client
```

الحالة المرفقة اصطناعية وموسومة `source_type=synthetic`، وليست منسوبة إلى GitHub.

## جمع الأخطاء الحقيقية

اجمع أول 100 سجل من SWE-bench Verified. لا يُحفظ منها في النطاق الحالي إلا ما يحتوي رسالة `AttributeError`، وتبقى الحالات في جدول خام حتى مراجعة السبب وإعادة التحقق:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m burhan source-import-swebench `
  --database data\repair-memory.sqlite3 `
  --offset 0 `
  --length 100
```

اجمع حالة BugsInPy محددة. تُحفظ الرقعة وأمر الاختبار كنص ولا يشغّل بُرهان `run_test.sh`:

```powershell
python -m burhan source-import-bugsinpy `
  --database data\repair-memory.sqlite3 `
  --project PySnooper `
  --bug 1
```

اجمع Pull Request محددًا من GitHub. هذا يحفظ الوصف ورقع الملفات ورقع الاختبارات إن وجدت، لكنه يبقى `unclassified` لأن GitHub وحده لا يثبت سبب الخطأ ولا نجاح الاختبار:

```powershell
python -m burhan source-import-github-pr `
  --database data\repair-memory.sqlite3 `
  --repo astropy/astropy `
  --pr 7336
```

استرجع نمطًا مشابهًا لخطأ جديد:

```powershell
python -m burhan source-search `
  --database data\repair-memory.sqlite3 `
  --error "AttributeError: 'NoneType' object has no attribute 'to'" `
  --json
```

يعيد JSON الوصف و`solution_patch` و`test_patch` وأمر الاختبار والمصدر. الحقل `proposal_status=source_candidate_not_locally_verified` يمنع الخلط بين رقعة مصدر سابقة وإصلاح ثبت نجاحه على مشروعك الحالي. ويُخزن `root_cause_status=unknown` عندما لا يصرح المصدر بالسبب بدل اختلاقه. دليل SWE-bench المستورد موسوم `SOURCE_ATTESTED` وليس `V2` محليًا.

للتجربة المتسلسلة:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\try-source-memory.ps1
```

ترخيص مجموعة البيانات وترخيص المشروع الأصلي حقلا مصدر منفصلان؛ لا يفترض بُرهان أن ترخيص SWE-bench ينطبق على رقعة المستودع الأصلي.

## تحليل فقط

يتطلب Python 3.11 أو أحدث.

```powershell
cd C:\Users\tkssy\OneDrive\ドキュメント\Burhan
$env:PYTHONPATH = "$PWD\src"
python -m burhan analyze `
  --project examples\python-name-error `
  --goal "أصلح الخطأ بأقل تعديل" `
  --error-file examples\python-name-error\error.txt
```

لإخراج JSON أضف `--json`.

## معاينة إصلاح

```powershell
python -m burhan repair `
  --project examples\python-name-error `
  --goal "أصلح الخطأ بأقل تعديل ولا تغيّر الواجهة" `
  --error-file examples\python-name-error\error.txt
```

لا يكتب هذا الأمر شيئًا. لتطبيق التعديل بعد مراجعة الفرق:

```powershell
python -m burhan repair `
  --project PATH_TO_PROJECT `
  --goal "أصلح الخطأ بأقل تعديل" `
  --error-file PATH_TO_ERROR.txt `
  --apply
```

ويمكن تثبيت الأمر محليًا في بيئة افتراضية:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
burhan analyze --help
```

## الاختبارات

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

## نموذج BIR الحالي

تتكون الحالة من عقد وحواف غير قابلة للتغيير:

- عقد: هدف، قيد، ملف، رمز، حدث خطأ، فرضية، ودليل.
- حواف: يعرّف، يستجيب إلى، يتطلب، ويدعم.
- كل قضية تحمل بصمة للمدخلات ومصدرًا يوضح إصدار المحرك وعدد الملفات وحالة اكتمال المسح.
- كل فرضية تحمل طاقة أقل كلما زادت الأدلة وقل حجم التغيير وعدم اليقين.

هذه دالة ترتيب هندسية مستوحاة من نموذج أقل طاقة؛ ليست تنفيذًا كموميًا بعد.

## معنى V0

شهادة `V0` الحالية تعني أن:

- الملف المستهدف داخل المشروع.
- الرمز يظهر مرة واحدة فقط في السطر الذي حدده التتبع.
- التعديل يغير هذا الرمز وحده.
- ملف Python الناتج ينجح في تحليل AST.

لا تعني `V0` أن السلوك صحيح أو أن اختبارات المشروع نجحت.

## الحدود الحالية

- يطبق الإصلاح حاليًا فقط لخطأ اسم Python عندما يوجد رمز بديل قريب وموقع سطر واضح.
- تحليل TypeScript للرموز أولي ولا يستخدم Tree-sitter أو TypeScript compiler بعد.
- لا ينفذ اختبارات المشروع تلقائيًا بعد؛ هذه هي خطوة `V1/V2` التالية.
- بيانات SWE-bench Verified تحمل رقعة واختبارات معيارية، لكن استيرادها لا يعني أنها نجحت على كود المستخدم الحالي.
- سجلات BugsInPy غالبًا لا تتضمن وصفًا صريحًا للخطأ أو السبب، ولذلك تبقى `unclassified` حتى المراجعة.
- لا يوجد نموذج لغوي أو QUBO/Ising backend في هذه النسخة.
- هدف أقل من ثانية يخص أول تشخيص في مشروع ضمن حدود المسح، وليس ضمان حل نهائي لكل مشكلة.

## المرحلة التالية

1. إضافة بوابة ترقية تتطلب سببًا من المصدر أو مراجعة بشرية موثقة.
2. تشغيل الاختبار الأصغر في حاوية معزولة ومحدودة الزمن.
3. رفع شهادة الإصلاح من `V0` إلى `V1/V2` عند نجاح البناء والاختبارات محليًا.
4. إعادة نتيجة الاختبار إلى BIR كدليل جديد.
5. إضافة فهرسة متزايدة بـTree-sitter وLSP.
