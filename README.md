# بُرهان | Burhan Engine

[![CI](https://github.com/mwthrc23-ui/burhan-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/mwthrc23-ui/burhan-engine/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/burhan-engine.svg)](https://pypi.org/project/burhan-engine/)
[![Python](https://img.shields.io/pypi/pyversions/burhan-engine.svg)](https://pypi.org/project/burhan-engine/)
[![License: Custom Attribution](https://img.shields.io/badge/License-Custom_Attribution-orange.svg)](LICENSE)
[![Container](https://img.shields.io/badge/GHCR-burhan--engine-2496ED?logo=docker&logoColor=white)](https://github.com/users/mwthrc23-ui/packages/container/package/burhan-engine)

**Evidence-first software diagnosis and repair verification for Python and TypeScript.**

بُرهان محرك تشخيص وإثبات إصلاح يحوّل هدف المستخدم والكود ورسالة الخطأ إلى حالة BIR مترابطة، ثم يرتب فرضيات السبب الجذري باستخدام الأدلة بدل التخمين.

```bash
python -m pip install --upgrade "burhan-engine==0.10.0"
burhan --version
burhan doctor
```

- يشخّص الأخطاء ويعرض الأدلة والمخاطر المتبقية.
- ينشئ معاينة إصلاح صغيرة من دون تغيير الأصل افتراضيًا.
- يثبت انتقال الاختبار من الفشل إلى النجاح داخل Docker مع عزل وحدود موارد.

[PyPI 0.10.0](https://pypi.org/project/burhan-engine/0.10.0/) · [Docker image](https://github.com/users/mwthrc23-ui/packages/container/package/burhan-engine) · [Release v0.10.0](https://github.com/mwthrc23-ui/burhan-engine/releases/tag/v0.10.0) · [License](LICENSE) · [Security policy](SECURITY.md)

تضيف النسخة `0.10.0` تحققًا آمنًا من رقع unified diff الخارجية، وإصلاحات محافظة لعائلات إضافية، ومقاييس فعلية لنجاح الرقع والـfalse-positive مع negative controls، وصورة pytest منشورة مثبتة بـdigest. التحليل يعمل محليًا ولا يرسل ملفات مشروعك إلى أي خدمة، ومزود LLM الاختياري ما زال stub معطلًا افتراضيًا.

الإصدار `0.10.0` متاح وفق `LicenseRef-Custom-Attribution`: العرض والاستخدام يتطلبان الإسناد الواضح — مشغّل بواسطة mwthrc23-ui أو مبني على عمل mwthrc23-ui — والاستخدام التجاري يتطلب إذناً كتابياً. الإصدارات المنشورة سابقًا تبقى خاضعة للترخيص المعلن مع كل إصدار؛ لا يغيّر هذا الإصدار شروطها بأثر رجعي. راجع ملف [LICENSE](LICENSE) للنص الحاكم.

حقوق النشر © ٢٠٢٦ mwthrc23-ui. جميع الحقوق محفوظة.

## البدء السريع (أسهل طريق)

### 1) تثبيت الأداة محليًا (PyPI/Editable)

```bash
python -m pip install --upgrade "burhan-engine==0.10.0"
burhan --version
burhan doctor
```

يجب أن يعرض `burhan --version` القيمة `burhan 0.10.0`. يعرض `doctor` نسخة Python، ووجود Docker CLI، وصيغة تثبيت صورة الإثبات، وحالة المزود المحلي؛ لكنه لا يثبت أن Docker daemon يعمل أو أن الصورة قابلة للسحب. استخدم `docker info` و`docker manifest inspect IMAGE` للتحقق التشغيلي. وللتحقق من الحزمة المنشورة بعيدًا عن ملفات المستودع:

```bash
uvx --refresh --from "burhan-engine==0.10.0" burhan --version
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

التحقق من رقعة unified diff جاءت من Aider أو OpenHands أو Copilot أو أي مولد آخر لا يثق باسم الأداة؛ يفحص المسارات والسياق ثم يطبق الرقعة داخل نسخة مؤقتة فقط، ويشترط انتقال الاختبار نفسه من الفشل إلى النجاح:

```bash
burhan verify-patch --project PATH_TO_PROJECT --patch-file candidate.diff \
  --trust-local-tests --test-program pytest --test-arg=-q --backend docker --json
```

يدعم `--test-program` القيم `python` و`pytest` و`tsc`. تشغيل `tsc` محليًا يتطلب compiler مثبتًا مسبقًا، وتشغيله داخل Docker يتطلب صورة Node/TypeScript مثبتة بـdigest يمررها المستخدم؛ لا ينزل بُرهان compiler أو حزمًا تلقائيًا.

### 3) تشغيل عبر Docker بدون إعداد Python محلي

اسحب الصورة المنشورة:

```bash
docker pull ghcr.io/mwthrc23-ui/burhan-engine:0.10.0
docker run --rm ghcr.io/mwthrc23-ui/burhan-engine:0.10.0 --version
```

صورة pytest المخصصة لإثبات V2 منشورة وجرى التحقق منها بالمرجع غير القابل للتبدل:

```text
ghcr.io/mwthrc23-ui/burhan-pytest@sha256:45181883b866f80ef1f3d1dc00661148eaf6b2e3715d0b7592d8905fd5221280
```

استخدم مرجع البصمة بدل الوسم في البيئات التي تتطلب صورة غير قابلة للتبدل.

ثم شغّل أي أمر `burhan` داخلها (مع mount للمشروع):

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace ghcr.io/mwthrc23-ui/burhan-engine:0.10.0 analyze --project examples/python-name-error --goal "شخّص الخطأ" --error-file examples/python-name-error/error.txt
```

## Burhan Evidence Gate للفرق وCI

يشغّل `ci-gate` التحليل والإثبات بنفسه؛ لا يقبل ملف `ProofResult` يقدمه المستخدم. ثم يطبق سياسة JSON صارمة ويكتب تقريرًا لا يحتوي الكود أو رسالة الخطأ الخام أو فرق الرقعة أو أمر الاختبار أو `stdout/stderr`.

```bash
burhan ci-gate \
  --project . \
  --goal "أثبت الإصلاح دون تغيير الأصل" \
  --error-file failure.txt \
  --policy examples/ci-policy-v2.json \
  --report burhan-gate-report.json \
  --trust-local-tests \
  --test-program python \
  --test-arg app.py \
  --backend docker \
  --json
```

عقد رموز الخروج:

- `0`: الإثبات اجتاز جميع قواعد السياسة.
- `1`: التنفيذ صالح لكن الإثبات أو السياسة رفضا الإصلاح؛ يُكتب تقرير رفض عند تحديد `--report`.
- `2`: إعداد أو بنية تحتية أو مسار تقرير غير صالح.

سياسة البوابة محدودة إلى 64 KiB، ترفض الحقول والقيم المكررة أو المجهولة، ولا تسمح بتعطيل اكتمال المسح أو ثبات المشروع الأصلي. تقرير JSON يحمل بصمة للسياسة وبصمة ذاتية للمحتوى، ويربط بصمات منقحة لأمر الاختبار وبيئة التشغيل وmanifest كامل لمدخلات المشروع دون كشف قيمها الخام. يمكن للسياسة تثبيت الأمر والبيئة عبر `allowed_command_fingerprints` و`allowed_runtime_fingerprints` لمنع استبدال الاختبار باختبار أضيق أو تغيير Docker digest. هذه البصمات checksums لكشف التغيير وليست توقيعًا رقميًا أو إثباتًا لهوية المنشئ. مسار `--report` يجب أن يكون ملفًا جديدًا بلاحقة `.json`، ولا يستبدل ملفًا أو رابطًا رمزيًا موجودًا.

سياسة المثال مثبتة للأمر `python app.py` ولصورة Python المبينة أدناه. إذا تغير الأمر أو digest، احسب القيم الجديدة من الدالتين `burhan.policy.fingerprint_command` و`fingerprint_runtime` وحدّث السياسة المحمية. كما يبني الإثبات manifest مؤطرًا قبل التحليل وبعده (حتى 15,000 عنصر و10,000 ملف و250 MB)، ويرفض التشغيل إذا تغير المشروع بين التحليل والإثبات أو تغير المحتوى أو البنية أو الصلاحيات أثناء الاختبار. ملفات الأسرار لا تُقرأ؛ يسمح Docker/V2 بمراقبة metadata لها لأن المشروع الأصلي غير مركب داخل الحاوية، بينما يرفض `local/V1` المشروع إذا اكتشف ملف أسرار لأنه لا يستطيع تقديم ضمان قوي دون قراءته. ويُرفض أي هدف داخل مجلد مستبعد من المسح قبل تشغيل الاختبار، كما تقبل صورة Docker صيغة OCI آمنة مثبتة فقط ولا يمكن أن تبدأ كخيار CLI.

> **مهم في Pull Requests غير الموثوقة:** لا تحمل السياسة من فرع المساهم. انسخها من فرع محمي أو من `RUNNER_TEMP` بعد التحقق من بصمتها، ثم ارفع التقرير كـartifact. اجعل workflow نفسه فحصًا إلزاميًا ومحميًا عبر GitHub Ruleset حتى لا يستطيع PR تعديل خطوة البوابة وتجاوزها. الإعداد الافتراضي يتطلب `V2` وDocker؛ تشغيل `local/V1` مخصص فقط لمشروع واختبارات موثوقة لأنه ليس عزلًا أمنيًا.

يجب كذلك فصل **أداة بُرهان الموثوقة** عن **المستودع الجاري تحليله**: في workflow المحمي ثبّت نسخة منشورة ومثبتة من `burhan-engine` أو wheel من بناء محمي، ولا تثبّت بُرهان من checkout الخاص بالـPR. في البيئات عالية الحساسية استخدم `--require-hashes` مع بصمة wheel المنشورة.

لا تحتوي حزمة `0.10.0` اعتماديات تشغيل خارجية، لذلك يستخدم المثال التالي `--no-deps`. أعد فحص metadata قبل نسخ هذا الخيار إلى إصدار لاحق؛ لا تستخدمه إذا أضيفت اعتماديات مطلوبة.

مثال GitHub Actions مختصر:

```yaml
- name: Install Burhan and preload pinned proof image
  env:
    BURHAN_DOCKER_IMAGE: python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
  run: |
    python -m pip install --only-binary=:all: --no-deps "burhan-engine==0.10.0"
    docker pull "$BURHAN_DOCKER_IMAGE"
- name: Load protected Burhan policy
  env:
    BASE_SHA: ${{ github.event.pull_request.base.sha }}
    EXPECTED_POLICY_SHA256: ${{ vars.BURHAN_POLICY_SHA256 }}
  run: |
    git fetch origin "$BASE_SHA" --depth=1
    git show "$BASE_SHA:examples/ci-policy-v2.json" > "$RUNNER_TEMP/burhan-policy.json"
    actual="$(sha256sum "$RUNNER_TEMP/burhan-policy.json" | cut -d ' ' -f 1)"
    test -n "$EXPECTED_POLICY_SHA256" && test "$actual" = "$EXPECTED_POLICY_SHA256"
- name: Run Burhan Evidence Gate
  env:
    BURHAN_DOCKER_IMAGE: python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
  run: |
    burhan ci-gate --project . --goal "أثبت الإصلاح" \
      --error-file "$RUNNER_TEMP/failure.txt" \
      --policy "$RUNNER_TEMP/burhan-policy.json" \
      --report "$RUNNER_TEMP/burhan-report.json" \
      --trust-local-tests --test-program python --test-arg app.py \
      --backend docker --docker-image "$BURHAN_DOCKER_IMAGE" --json
- name: Upload Burhan report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: burhan-evidence-report
    path: ${{ runner.temp }}/burhan-report.json
```

## تجربة كاملة بنقرة واحدة

يشغّل الأمر التالي التجربة على نسخة مؤقتة: تحليل الخطأ، معاينة patch، تطبيقه، ثم تشغيل الكود المصحح. لا يغيّر ملف المثال الأصلي.

```powershell
git clone https://github.com/mwthrc23-ui/burhan-engine.git
cd burhan-engine
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

هناك مساران منفصلان يجب عدم الخلط بينهما:

- `source-search` يفهرس سجلات خامًا ومرشحين عبر أكثر من 11 تصنيف خطأ، لكنها ليست إصلاحات موثقة على مشروع المستخدم.
- ذاكرة `RepairEpisode` الموثقة وترقيتها مقيدة حاليًا إلى:

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

التجربة المستقلة القابلة لإعادة التشغيل موجودة في [`experiments/bugsinpy`](experiments/bugsinpy/README.md). تستخدم أربع حالات مثبتة من BugsInPy، وتتحقق من hashes ولا تنفذ `run_test.sh` الخام. النتيجة المنشورة: نجاح خارجي V2 بمعدل 1/1 على اختبار انحدار upstream مثبت، و0/3 false positives على الضوابط الخارجية.

## تحليل فقط

يتطلب Python 3.11 أو أحدث.

```powershell
git clone https://github.com/mwthrc23-ui/burhan-engine.git
cd burhan-engine
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
python -m pytest -q
python -m coverage run -m pytest -q
python -m coverage report
```

خط الأساس الموثق للإصدار `0.10.0`: **641 اختبارًا ناجحًا** إضافةً إلى **25 subtest**، وتغطية مصدرية **89%**. يشغّل CI المجموعة الكاملة عبر pytest ويفرض تغطية لا تقل عن 80%، ثم يشغّل بوابة البنشمارك.

مجموعة البنشمارك الحالية تضم 65 حالة، بينها 9 negative controls. تقيس البوابة التشخيص والإصلاح منفصلين: false-positive تشخيصي، ونجاح الرقعة بمطابقة المصدر المصحح كاملًا مع ground truth بعد التطبيق داخل مشروع مؤقت، وpatch false-positive مستقل. خط الإصدار الحالي يحقق 4/4 نجاح رقع، و0/9 patch false positives، و0% diagnostic false-positive؛ المطابقة المحلية لا تستبدل إثبات V1/V2 السلوكي.

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

- الإصلاحات الجديدة typo-only ومحافظة: AttributeError، وimports المحلية، وKeyError الحرفي، وأسماء TypeScript/JavaScript غير المعرفة. الحالات الغامضة تُرفض بدل التخمين.
- تحليل TypeScript لا يستخدم Tree-sitter؛ إثباته السلوكي يتطلب `tsc --noEmit` مثبتًا مسبقًا أو صورة Docker مخصصة مثبتة بـdigest.
- صورة pytest المنشورة مثبتة بـdigest وتدعم pytest فقط؛ لا تدّعي احتواء Node أو TypeScript.
- بيانات SWE-bench Verified تحمل رقعة واختبارات معيارية، لكن استيرادها لا يعني أنها نجحت على كود المستخدم الحالي.
- سجلات BugsInPy غالبًا لا تتضمن وصفًا صريحًا للخطأ أو السبب، ولذلك تبقى `unclassified` حتى المراجعة.
- مزود LLM ما زال stub اختياريًا معطلًا افتراضيًا؛ لا يوجد تكامل نموذج لغوي مكتمل أو QUBO/Ising backend في هذه النسخة.
- هدف أقل من ثانية يخص أول تشخيص في مشروع ضمن حدود المسح، وليس ضمان حل نهائي لكل مشكلة.
- بصمات Evidence Gate checksums لكشف التغيير وليست توقيعًا رقميًا أو إثباتًا لهوية المنشئ؛ حماية workflow والسياسة في الفرع الأساسي جزء من نموذج الثقة.
- `burhan doctor` يتحقق من وجود Docker CLI وصيغة الصورة المثبتة، لكنه لا يفحص جاهزية daemon أو يسحب الصورة.

## المرحلة التالية

1. استعادة بوابة ترقية الذاكرة بإعادة الإثبات وربط `ProofResult` بالحالة والرقعة والمراجعة البشرية.
2. توسيع عينة BugsInPy إلى حالات fail-before صالحة في runtime مثبت بدل تحويل الضوابط السلبية إلى ادعاء نجاح.
3. إضافة فهرسة TypeScript أدق بـTree-sitter أو Language Service دون إدخالها كاعتمادية تشغيل افتراضية.

## ما الجديد في 0.10.0

- أمر `verify-patch` لرقع unified diff القادمة من Aider/OpenHands/Copilot وغيرها، مع parser محدود fail-closed وتطبيق ذري داخل نسخة الإثبات فقط.
- إصلاحات typo-only محافظة لـAttributeError وimports المحلية وKeyError وTypeScript/JavaScript، مع رفض الالتباس والمسارات غير الآمنة.
- 9 negative controls محلية ومقاييس منفصلة للتشخيص وpatch success وpatch false-positive؛ البوابة الحالية 4/4 exact-match للرقع المؤهلة و0/9 patch false positives.
- صورة pytest حقيقية منشورة من متطلبات ذات hashes ومثبتة بالـdigest `45181883b866f80ef1f3d1dc00661148eaf6b2e3715d0b7592d8905fd5221280`.
- تجربة BugsInPy مستقلة بأربع حالات خارجية مثبتة: 1/1 V2 fail-to-pass للرقعة الرسمية و0/3 false positives للضوابط السلبية.
- حسم metadata الإصدار الجديد على `LicenseRef-Custom-Attribution`؛ شروط الإصدارات المنشورة سابقًا لا تتغير بأثر رجعي.

## ما الجديد في 0.9.0

- بنشمارك محلي ثابت يضم 56 حالة عبر 10 عائلات أخطاء، مع أمر `burhan benchmark` وبوابة CI. يقيس Top-1 وTop-3 **للتشخيص**، ولا يدّعي إثبات نجاح الرقعة أو تشغيل sandbox. لا تحتوي suite الحالية negative controls، لذلك يعرض `False+` القيمة `n/a` بدل نسبة غير مقاسة.
- ربط معالجات KeyError وasync ومخرجات TypeScript القياسية بمسار `BurhanAnalyzer`، بما في ذلك TS2304 وTS2322 وTS2339 وTS2345 وTS2554.
- فهرسة متزايدة بذاكرة LRU، وفهرسة Python أوسع للوراثة ومواضع الاستخدام ورموز الاختبارات.
- نماذج `ProofCertificateV3` وبصمة مشروع SHA-256 محمية من الروابط الرمزية، مع اختبارات تمنع الترقية الزائفة وتغير المشروع. هذه واجهات برمجية في 0.9.0 وليست مسار CLI مكتملًا بعد.
- تنقيح الأسرار ومسارات الملفات قبل تمرير السياق إلى مزود LLM الاختياري؛ المزود يظل stub معطلًا افتراضيًا.
- بوابات إصدار أشد: Ruff، و550 اختبارًا، وتغطية مصدرية 89%، وبنشمارك تشخيصي 56/56 قبل بناء الحزمة أو نشرها.

## ما الجديد في 0.8.1

- تصحيح `UnicodeEncodeError` في مخرجات `burhan doctor` و`--explain` على طرفيات Windows ذات ترميز `cp1256`.
- استبدال رموز الحالة الزخرفية بصيغ نصية محمولة مثل `[OK]` و`[WARN]` و`[FAIL]`، واستخدام `->` للاقتراحات.
- إضافة اختباري انحدار يمران عبر مخرج `cp1256` صارم ويغطيان المسارين المتأثرين.

## ما الجديد في 0.8.0

### Evidence Graph V2

رسم بياني ثابت للأدلة (`EvidenceGraph`) يصنّف كل حقيقة إلى إحدى ثلاث فئات: `CONFIRMED` (ملاحظ مباشر)، أو `INFERRED` (مستنتج من الكود)، أو `ASSUMED` (محتمل لم يتحقق). للعُقد والحواف والحقائق بصمات SHA-256 وschema ثابت.

### الفهرسة الدلالية

- `index/python_indexer.py`: فهرسة كاملة لملفات Python.
- `index/typescript_indexer.py`: فهرسة TypeScript بوضع Regex مخفَّض (بدون Tree-sitter).
- واجهة موحدة في `index/base.py`.

### فرضيات متعددة المرشحين

`diagnosis/hypothesis_engine.py` يولّد فرضيات متعددة للسبب الجذري ويرتبها حسب الأدلة.

### مرشحو الإصلاح

`candidates/repair_candidates.py` يولّد مرشحين للإصلاح ويختار أصغر تغيير ناجح.

### البيئة التجريبية وحلقة الإصلاح

- `sandbox/sandbox_runner.py` يرفض صور Docker غير المثبتة بـdigest ويكشف تغيّر المشروع بين التحليل والإثبات.
- `verification/repair_loop.py` يشغّل حلقة إصلاح محدودة (لا تتجاوز حدًا مضبوطًا من المحاولات).

### تقارير SARIF 2.1.0

`reports/sarif_reporter.py` يصدر SARIF 2.1.0 مع منع الكتابة فوق ملف موجود أو عبر symlink.

### مزود المعلومات الاستخباراتية

- `intelligence/local_provider.py`: مزود محلي لا يحتاج اتصالاً خارجيًا.
- `intelligence/llm_provider.py`: stub اختياري لمزود LLM — **معطل افتراضيًا وغير مكتمل التكامل**.
- المحرك يستمر بشكل طبيعي عند تعطل أي مزود.

### مستويات الثقة (TrustLevel)

أربع مراتب: `raw_source` / `unverified_local` / `locally_proven` / `human_reviewed`.

### أمر `burhan doctor`

يفحص توفر Python وDocker والصور والإصدارات ويعرض النتيجة نصًا أو JSON.

### الاختبارات والتغطية

370 اختبارًا ناجحًا (مع 18 subtest)، وتغطية إجمالية 93%.

### ملاحظة

أمر `memory-promote` ما زال معطلًا في هذا الإصدار. مزود LLM stub ليس تكاملًا مكتملًا.

## الأوامر الجديدة (v0.8.0)

### `burhan doctor`

يفحص توفر Docker والأدوات والصور والسياسات:

```bash
burhan doctor
burhan doctor --json
```

### `--explain`

يضيف شرحًا مفصلًا بالعربية لأوامر `analyze` و`repair`:

```bash
burhan analyze --project . --goal "شخّص" --error-file err.txt --explain
burhan repair  --project . --goal "أصلح" --error-file err.txt --explain
```

يعرض الملخص النهائي: ماذا حدث؟ / السبب / الدليل / التغييرات / الاختبارات / ما لم يُثبت.

## البنية المعمارية (v0.8.0)

```text
src/burhan/
├── model.py              نماذج BIR الأساسية
├── scanner.py            مسح المشروع الآمن والمحدود
├── analyzer.py           تشخيص الأخطاء
├── patcher.py            إنشاء الرقعة وإثباتها
├── policy.py             سياسات CI Gate
├── memory.py             ذاكرة الإصلاح + TrustLevel
├── cli.py                واجهة سطر الأوامر
├── evidence.py           رسم الأدلة V2 (EvidenceGraph)
├── benchmark/            بنشمارك التشخيص المحلي
├── index/                فهرسة دلالية (Python + TypeScript)
├── diagnosis/            محرك فرضيات متعدد المرشحين
├── candidates/           توليد مرشحي الإصلاح وترتيبهم
├── sandbox/              التحقق من قيود Docker
├── verification/         حلقة الإصلاح المحدودة
├── intelligence/         مزود ذكاء اختياري (محلي + LLM stub)
├── model_v3.py           نموذج شهادة الإثبات V3 (واجهة برمجية)
└── reports/              تقارير SARIF
```

## مستويات الثقة في ذاكرة الإصلاح

| المستوى | المعنى |
|---------|--------|
| `raw_source` | بيانات مستوردة من مصدر خارجي، غير محققة محليًا |
| `unverified_local` | مرشح اقترحه المحرك ولم يُثبت بعد |
| `locally_proven` | أُثبت بانتقال حقيقي (فشل→نجاح) داخل Docker |
| `human_reviewed` | خضع إضافةً إلى ذلك لمراجعة بشرية |

لا تظهر في نتائج البحث إلا الحالات ذات مصدر موثوق (`curated` أو `source_asserted`).

