# بُرهان

بُرهان محرك تجريبي يحوّل هدف المستخدم والكود ورسالة الخطأ إلى حالة BIR مترابطة، ثم يرتب فرضيات السبب الجذري باستخدام الأدلة ودالة طاقة.

هذه النسخة `0.6.0` هي إثبات فكرة قابل للتجربة: التحليل يعمل محليًا ولا يرسل ملفات مشروعك إلى أي خدمة، وجامع المصادر يتصل فقط بمضيفي SWE-bench وGitHub المسموحين صراحة ولا يحتاج مفاتيح API. أضيف إثبات إصلاح سلوكي داخل Docker مع بقاء المشروع الأصلي دون تغيير.

## البدء السريع (أسهل طريق)

### 1) تثبيت الأداة محليًا (PyPI/Editable)

```bash
python -m pip install burhan-engine
burhan --help
```

وللتطوير المحلي من المستودع:

```bash
python -m pip install -e .
burhan --version
```

### 2) تشغيل موحد بالأمر `burhan`

تحليل فقط:

```bash
burhan analyze --project PATH_TO_PROJECT --goal "شخّص الخطأ" --error-file PATH_TO_ERROR.txt
```

معاينة إصلاح:

```bash
burhan repair --project PATH_TO_PROJECT --goal "أصلح الخطأ بأقل تعديل" --error-file PATH_TO_ERROR.txt
```

إثبات الإصلاح:

```bash
burhan repair-proof --project PATH_TO_PROJECT --goal "أثبت الإصلاح" --error-file PATH_TO_ERROR.txt --trust-local-tests
```

### 3) تشغيل عبر Docker بدون إعداد Python محلي

ابنِ الصورة:

```bash
docker build -t burhan-engine:local .
```

ثم شغّل أي أمر `burhan` داخلها (مع mount للمشروع):

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace burhan-engine:local analyze --project examples/python-name-error --goal "شخّص الخطأ" --error-file examples/python-name-error/error.txt
```

## تجربة كاملة بنقرة واحدة

يشغّل الأمر التالي التجربة على نسخة مؤقتة: تحليل الخطأ، معاينة patch، تطبيقه، ثم تشغيل الكود المصحح. لا يغيّر ملف المثال الأصلي.

```powershell
cd C:\Users\tkssy\OneDrive\ドキュメント\Burhan
powershell -ExecutionPolicy Bypass -File scripts\try-burhan.ps1
```

## ما يعمل الآن

- فهرسة آمنة ومحدودة لملفات Python وTypeScript/JavaScript وبعض ملفات الإعداد والتوثيق.
- استبعاد `.env` والمفاتيح الخاصة ومجلدات `.git` و`node_modules` والبناء والتغطية.
- استخراج رموز Python عبر AST، واستخراج أولي لرموز TypeScript/JavaScript.
- تشخيص أخطاء Python: `NameError` و`UnboundLocalError` و`AttributeError` و`ModuleNotFoundError` / `ImportError` و`SyntaxError` و`TypeError` (عدد وسطاء غلط، كائن غير قابل للاستدعاء، عملية غير مدعومة، عام) و`ValueError` و`IndexError` و`KeyError` و`ZeroDivisionError` و`RecursionError` و`FileNotFoundError` / `OSError`.
- تشخيص أخطاء TypeScript: `TS2304` (اسم غير معروف) و`TS2322` (تعارض نوع) و`TS2339` (خاصية مفقودة) و`TS2345` (نوع وسيط غير متوافق) و`TS2554` (عدد وسطاء غلط).
- اقتراح رمز قريب عند وجود خطأ إملائي في اسم Python أو TypeScript.
- إخراج بشري عربي أو JSON يتضمن BIR والأدلة والثقة والطاقة وزمن التحليل.
- رقم قضية ثابت وبصمة SHA-256 ومدى اكتمال المسح والمخاطر المتبقية.
- استخراج القيود الصريحة من الهدف، مثل «لا تغيّر الواجهة».
- أمر `repair` ينشئ unified diff ويمنحه درجة `V0` بعد فحص النطاق والسطر وصياغة Python؛ يدعم إصلاح `NameError` و`UnboundLocalError` عند توفر رمز بديل قريب.
- أمر `repair-proof` يعيد الاختبار نفسه قبل الرقعة وبعدها داخل نسخة مؤقتة.
- يمنح `V2` عند تحقق fail-to-pass داخل Docker بشبكة معطلة وحدود موارد وملفات للقراءة فقط.
- المعاينة هي الوضع الافتراضي؛ الكتابة تحتاج الخيار الصريح `--apply`.
- التوقف وطلب دليل إضافي بدل اختراع سبب غير مدعوم.
- قراءة حالات `RepairEpisode` الموثقة مسبقًا من SQLite؛ إدخال ملفات JSON عبر CLI معطل مؤقتًا حتى يمكن إعادة إثباتها وربطها بالحالة والرقعة.
- البحث عن حالات مشابهة حسب النوع والرمز لأكثر من 11 نوع خطأ مصنّف.
- ربط نتائج ذاكرة الإصلاح بالتشخيص الجديد عبر `analyze --memory`.
- جمع حالات حقيقية محدودة من SWE-bench Verified مع الوصف والرقعة ورقعة الاختبار و`FAIL_TO_PASS`، مع تصنيف تلقائي لأنواع الأخطاء (`attribute_error_candidate`، `name_error_candidate`، `module_error_candidate`، إلخ).
- جمع حزمة BugsInPy المحددة مع commit والرقعة وأمر الاختبار، من دون تنفيذ أي كود وارد من المصدر.
- جمع Pull Request محدد من GitHub مع الوصف والرقع كـ`source_record` خام غير مرقى، مع كشف تلقائي لنوع الخطأ من وصف PR.
- فصل `source_records` الخام عن `repair_episodes` الموثقة، ومنع الحالة غير المصنفة من الظهور كإصلاح موثوق.
- حفظ تغيرات المصدر كسجل نسخ append-only يعتمد على SHA-256 بدل استبدال الدليل السابق بصمت.
- استرجاع رقعة واختبار مشابهين عبر `source-search` مع وسم صريح بأنها مرشحة لم تُختبر محليًا.
- **شجرة الكود** `code-tree`: أمر يعرض هيكل المشروع الهرمي (مجلدات وملفات ورموز) نصًا أو JSON، مع تضمين دوال الأصناف بشكل متداخل داخل عقدة الصنف.
- **تضمين شجرة الكود في التحليل**: يُرفق أمر `analyze` شجرة الكود تلقائيًا بنتيجة التحليل؛ استخدم `--code-tree` لعرضها في المخرجات البشرية.

## شجرة الكود

تُبنى شجرة الكود عند كل تحليل وتُضمَّن في نتيجة `analyze`. يمكن عرضها مستقلة بأمر `code-tree`، أو مضمّنة مع التحليل باستخدام `--code-tree`.

### عرض الشجرة المستقلة

```bash
burhan code-tree --project PATH_TO_PROJECT
```

### تضمينها مع أمر التحليل

```bash
burhan analyze --project PATH_TO_PROJECT --goal "شخّص الخطأ" --error-file error.txt --code-tree
```

مثال على الإخراج (الدوال متداخلة داخل الأصناف):

```
\-- my-project [directory]
    |-- src [directory]
    |   \-- app.py
    |       |-- MyClass [class]
    |       |   |-- __init__ [function]
    |       |   \-- process [function]
    |       \-- helper [function]
    \-- tests [directory]
        \-- test_app.py
```

لتحديد أقصى عمق للشجرة:

```bash
burhan code-tree --project PATH_TO_PROJECT --depth 2
```

لإخراج JSON قابل للمعالجة:

```bash
burhan code-tree --project PATH_TO_PROJECT --json
```

## ذاكرة الإصلاح

النطاق الأول مقيد عمدًا إلى:

```text
Python + pytest + AttributeError
```

أمرا `memory-add` و`memory-promote` معطلان مؤقتًا. لا يقبل CLI ملفات
`RepairEpisode` أو `ProofResult` المقدمة من المستخدم بوصفها دليلًا موثوقًا حتى
يستطيع `ProofRunner` إعادة تنفيذ اختبار `AttributeError` وربط النتيجة بالحالة
والرقعة نفسها. يبقى البحث في قاعدة موثقة مسبقًا متاحًا. يجب أن يشير
`--database` إلى قاعدة موجودة ومعبأة من مسار موثوق؛ لا ينشئ المثال بيانات:

ابحث عن حالة مشابهة:

```powershell
python -m burhan memory-search `
  --database C:\path\to\preverified-memory.sqlite3 `
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
  --memory C:\path\to\preverified-memory.sqlite3 `
  --dependency demo-client
```

ملفات المثال المرفقة اصطناعية وموسومة `source_type=synthetic`، وليست منسوبة
إلى GitHub ولا تُحمّل تلقائيًا في الذاكرة الموثوقة.

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

## إثبات الإصلاح داخل Docker

شغّل التجربة الجاهزة:

```powershell
docker pull "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
powershell -ExecutionPolicy Bypass -File scripts\try-repair-proof.ps1
```

أو شغّل الأمر مباشرة:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m burhan repair-proof `
  --project examples\python-name-error `
  --goal "أثبت الإصلاح دون تغيير الأصل" `
  --error-file examples\python-name-error\error.txt `
  --trust-local-tests `
  --backend docker `
  --docker-image "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de" `
  --test-program python `
  --test-arg app.py `
  --json
```

يشترط `V2` أن يطابق فشل البداية الخطأ والرمز والموقع اللذين شخّصهما بُرهان، ثم ينجح الاختبار نفسه بعد الرقعة داخل الحاوية، وأن تبقى بصمة الملف الأصلي كما هي. لا يطبق الأمر الرقعة على مشروع المستخدم، ولا ينسخ ملفات الأسرار الشائعة إلى مساحة الإثبات. صورة Docker المثبتة بـ`sha256` إلزامية، ويستخدم التشغيل `--pull never` لمنع تبدّلها أثناء الإثبات. خيار `--trust-local-tests` إلزامي لأن الاختبار نفسه كود تنفيذي، ولا يشغّل بُرهان اختبارات السجلات الخارجية تلقائيًا.

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

## درجات الإثبات

شهادة `V0` الحالية تعني أن:

- الملف المستهدف داخل المشروع.
- الرمز يظهر مرة واحدة فقط في السطر الذي حدده التتبع.
- التعديل يغير هذا الرمز وحده.
- ملف Python الناتج ينجح في تحليل AST.

لا تعني `V0` أن السلوك صحيح أو أن اختبارات المشروع نجحت.

- `V1`: فشل مطابق للتشخيص انتقل إلى النجاح في نسخة محلية مؤقتة موثوقة.
- `V2`: الانتقال نفسه تحقق داخل Docker بصورة مثبتة ببصمة، مع `--network none` وقيود موارد وإسقاط capabilities وmount للقراءة فقط.
- لا تثبت `V1/V2` السبب الجذري وحده؛ بل تثبت أن الرقعة غيّرت نتيجة الاختبار المحدد من الفشل إلى النجاح.

## الحدود الحالية

- يطبق الإصلاح حاليًا فقط لخطأ اسم Python عندما يوجد رمز بديل قريب وموقع سطر واضح.
- تحليل TypeScript للرموز أولي ولا يستخدم Tree-sitter أو TypeScript compiler بعد.
- إثبات Docker الحالي يحتاج صورة تحتوي أداة الاختبار المطلوبة؛ صورة Python الافتراضية مناسبة لاختبارات `python` المباشرة، بينما يحتاج pytest إلى صورة تتضمن pytest.
- بيانات SWE-bench Verified تحمل رقعة واختبارات معيارية، لكن استيرادها لا يعني أنها نجحت على كود المستخدم الحالي.
- سجلات BugsInPy غالبًا لا تتضمن وصفًا صريحًا للخطأ أو السبب، ولذلك تبقى `unclassified` حتى المراجعة.
- لا يوجد نموذج لغوي أو QUBO/Ising backend في هذه النسخة.
- هدف أقل من ثانية يخص أول تشخيص في مشروع ضمن حدود المسح، وليس ضمان حل نهائي لكل مشكلة.

## المرحلة التالية

1. استعادة بوابة الترقية بإعادة إثبات `AttributeError` وربط `ProofResult` بالحالة والرقعة، مع سبب من المصدر أو مراجعة بشرية موثقة وإثبات `V2`.
2. إعادة نتيجة الاختبار إلى BIR كدليل جديد قابل للاسترجاع.
3. بناء صورة تحقق pytest مثبتة بالاعتماديات وdigest.
4. إضافة فهرسة متزايدة بـTree-sitter وLSP.
