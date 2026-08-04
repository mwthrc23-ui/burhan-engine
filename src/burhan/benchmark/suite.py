"""Benchmark suite for Burhan Engine.

Contains 50+ curated cases covering 10 error families across Python and
TypeScript.  Each case is a frozen dataclass so the suite is immutable and
safe to share across threads.

Error families covered
----------------------
1.  NameError
2.  UnboundLocalError
3.  AttributeError
4.  ImportError / ModuleNotFoundError
5.  TypeError (argument count, non-callable, bad operand)
6.  KeyError
7.  IndexError
8.  async / await errors
9.  TypeScript missing symbol / property
10. TypeScript type mismatch / param count

Design rules
------------
* No network calls; all cases are self-contained.
* ``expected_error_family`` must match a canonical family name.
* ``expected_top1_kind`` is the hypothesis ``kind`` the engine should
  produce as rank-1 for a *correct* diagnosis.
* ``curated`` flag marks cases with a known ground-truth repair;
  non-curated cases are still valid diagnostic targets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_FAMILIES = frozenset(
    {
        "name_error",
        "unbound_local_error",
        "attribute_error",
        "import_error",
        "type_error",
        "key_error",
        "index_error",
        "async_error",
        "typescript_missing_symbol",
        "typescript_type_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """A single benchmark case.

    Attributes
    ----------
    case_id:
        Unique string identifier.
    language:
        ``"python"`` or ``"typescript"``.
    error_family:
        One of ``VALID_FAMILIES``.
    error_text:
        Raw error / traceback text as it appears at runtime.
    source_snippet:
        Minimal source code that triggers the error (may be empty).
    expected_error_family:
        The canonical family the engine should classify this as.
    expected_top1_kind:
        The ``Hypothesis.kind`` expected at rank-1.
    curated:
        True when a known ground-truth repair exists.
    ground_truth_repair:
        Short description of the fix (empty string if not curated).
    notes:
        Optional free-text notes for reviewers.
    """

    case_id: str
    language: str
    error_family: str
    error_text: str
    source_snippet: str
    expected_error_family: str
    expected_top1_kind: str
    curated: bool
    ground_truth_repair: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.error_family not in VALID_FAMILIES:
            raise ValueError(
                f"case {self.case_id}: unknown error_family {self.error_family!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "language": self.language,
            "error_family": self.error_family,
            "error_text": self.error_text,
            "source_snippet": self.source_snippet,
            "expected_error_family": self.expected_error_family,
            "expected_top1_kind": self.expected_top1_kind,
            "curated": self.curated,
            "ground_truth_repair": self.ground_truth_repair,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    """Immutable collection of benchmark cases."""

    cases: tuple[BenchmarkCase, ...]

    def __len__(self) -> int:
        return len(self.cases)

    def by_family(self, family: str) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.error_family == family)

    def curated_only(self) -> tuple[BenchmarkCase, ...]:
        return tuple(c for c in self.cases if c.curated)

    def families(self) -> frozenset[str]:
        return frozenset(c.error_family for c in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {"total": len(self), "cases": [c.to_dict() for c in self.cases]}


# ---------------------------------------------------------------------------
# Case definitions — 50+ curated and inferred cases
# ---------------------------------------------------------------------------

_CASES: list[BenchmarkCase] = [
    # -----------------------------------------------------------------------
    # 1. NameError — 6 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-name-001",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "app.py", line 5, in <module>\n'
            '    result = calculat(10)\n'
            "NameError: name 'calculat' is not defined"
        ),
        source_snippet="def calculate(x):\n    return x * 2\nresult = calculat(10)\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Rename call from 'calculat' to 'calculate'",
    ),
    BenchmarkCase(
        case_id="py-name-002",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "main.py", line 3, in <module>\n'
            '    print(mesage)\n'
            "NameError: name 'mesage' is not defined"
        ),
        source_snippet="message = 'hello'\nprint(mesage)\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Rename 'mesage' to 'message'",
    ),
    BenchmarkCase(
        case_id="py-name-003",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "utils.py", line 8, in process\n'
            '    return Pathlib.Path(data)\n'
            "NameError: name 'Pathlib' is not defined"
        ),
        source_snippet="from pathlib import Path\ndef process(data):\n    return Pathlib.Path(data)\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Replace 'Pathlib' with 'Path' (already imported)",
    ),
    BenchmarkCase(
        case_id="py-name-004",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "server.py", line 12, in handler\n'
            '    resp = Respose(200)\n'
            "NameError: name 'Respose' is not defined"
        ),
        source_snippet="class Response:\n    def __init__(self, code): self.code = code\ndef handler():\n    resp = Respose(200)\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Rename 'Respose' to 'Response'",
    ),
    BenchmarkCase(
        case_id="py-name-005",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "config.py", line 4, in <module>\n'
            '    TIMEOUT = DEFALT_TIMEOUT\n'
            "NameError: name 'DEFALT_TIMEOUT' is not defined"
        ),
        source_snippet="DEFAULT_TIMEOUT = 30\nTIMEOUT = DEFALT_TIMEOUT\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Rename 'DEFALT_TIMEOUT' to 'DEFAULT_TIMEOUT'",
    ),
    BenchmarkCase(
        case_id="py-name-006",
        language="python",
        error_family="name_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "model.py", line 9, in serialize\n'
            '    return jsn.dumps(self.__dict__)\n'
            "NameError: name 'jsn' is not defined"
        ),
        source_snippet="import json\nclass Model:\n    def serialize(self):\n        return jsn.dumps(self.__dict__)\n",
        expected_error_family="name_error",
        expected_top1_kind="name_error",
        curated=True,
        ground_truth_repair="Replace 'jsn' with 'json' (already imported)",
    ),
    # -----------------------------------------------------------------------
    # 2. UnboundLocalError — 5 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-unbound-001",
        language="python",
        error_family="unbound_local_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "counter.py", line 7, in increment\n'
            '    count += 1\n'
            "UnboundLocalError: local variable 'count' referenced before assignment"
        ),
        source_snippet="count = 0\ndef increment():\n    count += 1\n    return count\n",
        expected_error_family="unbound_local_error",
        expected_top1_kind="unbound_local_error",
        curated=True,
        ground_truth_repair="Add 'global count' at top of increment()",
    ),
    BenchmarkCase(
        case_id="py-unbound-002",
        language="python",
        error_family="unbound_local_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "parser.py", line 15, in parse\n'
            '    result.append(token)\n'
            "UnboundLocalError: local variable 'result' referenced before assignment"
        ),
        source_snippet=(
            "def parse(tokens):\n    if tokens:\n        result = []\n"
            "    result.append(tokens[0])\n    return result\n"
        ),
        expected_error_family="unbound_local_error",
        expected_top1_kind="unbound_local_error",
        curated=True,
        ground_truth_repair="Move 'result = []' outside the conditional",
    ),
    BenchmarkCase(
        case_id="py-unbound-003",
        language="python",
        error_family="unbound_local_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "scope.py", line 6, in outer\n'
            "UnboundLocalError: cannot access local variable 'x' before assignment"
        ),
        source_snippet="x = 10\ndef outer():\n    print(x)\n    x = 20\n",
        expected_error_family="unbound_local_error",
        expected_top1_kind="unbound_local_error",
        curated=True,
        ground_truth_repair="Add 'nonlocal x' or rename local variable",
    ),
    BenchmarkCase(
        case_id="py-unbound-004",
        language="python",
        error_family="unbound_local_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "accumulate.py", line 4, in run\n'
            '    total += value\n'
            "UnboundLocalError: local variable 'total' referenced before assignment"
        ),
        source_snippet="def run(values):\n    for value in values:\n        total += value\n    return total\n",
        expected_error_family="unbound_local_error",
        expected_top1_kind="unbound_local_error",
        curated=True,
        ground_truth_repair="Initialize 'total = 0' before the loop",
    ),
    BenchmarkCase(
        case_id="py-unbound-005",
        language="python",
        error_family="unbound_local_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "builder.py", line 10, in build\n'
            '    parts.append(item)\n'
            "UnboundLocalError: local variable 'parts' referenced before assignment"
        ),
        source_snippet="def build(items, condition):\n    if condition:\n        parts = []\n    for item in items:\n        parts.append(item)\n",
        expected_error_family="unbound_local_error",
        expected_top1_kind="unbound_local_error",
        curated=True,
        ground_truth_repair="Initialize 'parts = []' unconditionally before loop",
    ),
    # -----------------------------------------------------------------------
    # 3. AttributeError — 6 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-attr-001",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "api.py", line 8, in fetch\n'
            '    data = response.json\n'
            "AttributeError: 'Response' object has no attribute 'json'"
        ),
        source_snippet="resp = Response()\ndata = resp.json\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Call resp.json() instead of resp.json",
    ),
    BenchmarkCase(
        case_id="py-attr-002",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "processor.py", line 14, in transform\n'
            '    return df.colums\n'
            "AttributeError: 'DataFrame' object has no attribute 'colums'"
        ),
        source_snippet="import pandas as pd\ndef transform(df):\n    return df.colums\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Rename 'colums' to 'columns'",
    ),
    BenchmarkCase(
        case_id="py-attr-003",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "node.py", line 6, in next_node\n'
            "AttributeError: 'NoneType' object has no attribute 'next'"
        ),
        source_snippet="class Node:\n    def __init__(self, val, nxt=None):\n        self.val = val\n        self.next = nxt\nnode = None\nprint(node.next)\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Add None guard before accessing .next",
    ),
    BenchmarkCase(
        case_id="py-attr-004",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "string_utils.py", line 3, in normalize\n'
            '    return text.strip().upercase()\n'
            "AttributeError: 'str' object has no attribute 'upercase'"
        ),
        source_snippet="def normalize(text):\n    return text.strip().upercase()\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Replace 'upercase' with 'upper' or 'uppercase'",
    ),
    BenchmarkCase(
        case_id="py-attr-005",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "config.py", line 5, in load\n'
            "AttributeError: 'dict' object has no attribute 'database'"
        ),
        source_snippet="cfg = {'database': 'sqlite'}\nprint(cfg.database)\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Use cfg['database'] or convert to dataclass",
    ),
    BenchmarkCase(
        case_id="py-attr-006",
        language="python",
        error_family="attribute_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "list_ops.py", line 4, in process\n'
            "AttributeError: 'list' object has no attribute 'push'"
        ),
        source_snippet="items = []\nitems.push('x')\n",
        expected_error_family="attribute_error",
        expected_top1_kind="attribute_error",
        curated=True,
        ground_truth_repair="Replace 'push' with 'append'",
    ),
    # -----------------------------------------------------------------------
    # 4. ImportError / ModuleNotFoundError — 6 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-import-001",
        language="python",
        error_family="import_error",
        error_text="ModuleNotFoundError: No module named 'requets'",
        source_snippet="import requets\nresponse = requets.get('https://example.com')\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=True,
        ground_truth_repair="Replace 'requets' with 'requests'",
    ),
    BenchmarkCase(
        case_id="py-import-002",
        language="python",
        error_family="import_error",
        error_text="ModuleNotFoundError: No module named 'numpy'",
        source_snippet="import numpy as np\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=True,
        ground_truth_repair="Run: pip install numpy",
    ),
    BenchmarkCase(
        case_id="py-import-003",
        language="python",
        error_family="import_error",
        error_text=(
            "ImportError: cannot import name 'urlparse' from 'urllib'"
        ),
        source_snippet="from urllib import urlparse\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=True,
        ground_truth_repair="Change to 'from urllib.parse import urlparse'",
    ),
    BenchmarkCase(
        case_id="py-import-004",
        language="python",
        error_family="import_error",
        error_text=(
            "ImportError: cannot import name 'OrderedDict' from 'collections.abc'"
        ),
        source_snippet="from collections.abc import OrderedDict\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=True,
        ground_truth_repair="Change to 'from collections import OrderedDict'",
    ),
    BenchmarkCase(
        case_id="py-import-005",
        language="python",
        error_family="import_error",
        error_text="ModuleNotFoundError: No module named 'mypackage.utils'",
        source_snippet="from mypackage.utils import helper\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=False,
        notes="Relative path or missing __init__.py",
    ),
    BenchmarkCase(
        case_id="py-import-006",
        language="python",
        error_family="import_error",
        error_text="ModuleNotFoundError: No module named 'sklearn'",
        source_snippet="from sklearn.linear_model import LinearRegression\n",
        expected_error_family="import_error",
        expected_top1_kind="import_error",
        curated=True,
        ground_truth_repair="Run: pip install scikit-learn",
    ),
    # -----------------------------------------------------------------------
    # 5. TypeError — 7 cases (arg count, non-callable, bad operand)
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-type-001",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: greet() takes 1 positional argument but 2 were given"
        ),
        source_snippet="def greet(name):\n    return f'Hello {name}'\ngreet('Alice', 'Bob')\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Remove extra argument or add parameter to greet()",
    ),
    BenchmarkCase(
        case_id="py-type-002",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: 'int' object is not callable"
        ),
        source_snippet="result = 42\nvalue = result(10)\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="result is an int; remove call or replace with a function",
    ),
    BenchmarkCase(
        case_id="py-type-003",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        ),
        source_snippet="age = 25\nmsg = 'Age: ' + age\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Convert age to str: 'Age: ' + str(age)",
    ),
    BenchmarkCase(
        case_id="py-type-004",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: compute() takes 0 positional arguments but 1 was given"
        ),
        source_snippet="class Calculator:\n    def compute():\n        return 0\nc = Calculator()\nc.compute()\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Add 'self' parameter to compute(self)",
    ),
    BenchmarkCase(
        case_id="py-type-005",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: 'NoneType' object is not callable"
        ),
        source_snippet="callback = None\ncallback('event')\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Guard with 'if callback is not None:'",
    ),
    BenchmarkCase(
        case_id="py-type-006",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: unsupported operand type(s) for *: 'str' and 'float'"
        ),
        source_snippet="price = '9.99'\ntotal = price * 1.1\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Convert price to float: float(price) * 1.1",
    ),
    BenchmarkCase(
        case_id="py-type-007",
        language="python",
        error_family="type_error",
        error_text=(
            "TypeError: connect() takes 2 positional arguments but 3 were given"
        ),
        source_snippet="def connect(host, port):\n    pass\nconnect('localhost', 8080, True)\n",
        expected_error_family="type_error",
        expected_top1_kind="type_error",
        curated=True,
        ground_truth_repair="Remove extra argument or add ssl parameter to connect()",
    ),
    # -----------------------------------------------------------------------
    # 6. KeyError — 5 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-key-001",
        language="python",
        error_family="key_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "config.py", line 3\n'
            "KeyError: 'timeout'"
        ),
        source_snippet="cfg = {'host': 'localhost'}\nval = cfg['timeout']\n",
        expected_error_family="key_error",
        expected_top1_kind="key_error",
        curated=True,
        ground_truth_repair="Use cfg.get('timeout', default) or add 'timeout' key",
    ),
    BenchmarkCase(
        case_id="py-key-002",
        language="python",
        error_family="key_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "store.py", line 8\n'
            "KeyError: 'user_id'"
        ),
        source_snippet="data = {'username': 'alice'}\nuid = data['user_id']\n",
        expected_error_family="key_error",
        expected_top1_kind="key_error",
        curated=True,
        ground_truth_repair="Use data.get('user_id') or check 'user_id' in data",
    ),
    BenchmarkCase(
        case_id="py-key-003",
        language="python",
        error_family="key_error",
        error_text="KeyError: 'results'",
        source_snippet="response = {'data': []}\nitems = response['results']\n",
        expected_error_family="key_error",
        expected_top1_kind="key_error",
        curated=True,
        ground_truth_repair="Replace 'results' with 'data' or add .get() guard",
    ),
    BenchmarkCase(
        case_id="py-key-004",
        language="python",
        error_family="key_error",
        error_text="KeyError: 0",
        source_snippet="d = {'a': 1}\nval = d[0]\n",
        expected_error_family="key_error",
        expected_top1_kind="key_error",
        curated=True,
        ground_truth_repair="Use d.get(0) or fix the key type",
    ),
    BenchmarkCase(
        case_id="py-key-005",
        language="python",
        error_family="key_error",
        error_text="KeyError: 'name'",
        source_snippet="env = {}\nname = env['name']\n",
        expected_error_family="key_error",
        expected_top1_kind="key_error",
        curated=True,
        ground_truth_repair="Use os.environ.get('name') or provide default",
    ),
    # -----------------------------------------------------------------------
    # 7. IndexError — 5 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-index-001",
        language="python",
        error_family="index_error",
        error_text=(
            'Traceback (most recent call last):\n'
            '  File "list_ops.py", line 4\n'
            "IndexError: list index out of range"
        ),
        source_snippet="items = [1, 2, 3]\nval = items[5]\n",
        expected_error_family="index_error",
        expected_top1_kind="index_error",
        curated=True,
        ground_truth_repair="Add bounds check: if 5 < len(items)",
    ),
    BenchmarkCase(
        case_id="py-index-002",
        language="python",
        error_family="index_error",
        error_text="IndexError: list index out of range",
        source_snippet="rows = []\nfirst = rows[0]\n",
        expected_error_family="index_error",
        expected_top1_kind="index_error",
        curated=True,
        ground_truth_repair="Check 'if rows:' before accessing rows[0]",
    ),
    BenchmarkCase(
        case_id="py-index-003",
        language="python",
        error_family="index_error",
        error_text="IndexError: string index out of range",
        source_snippet="s = ''\nchar = s[0]\n",
        expected_error_family="index_error",
        expected_top1_kind="index_error",
        curated=True,
        ground_truth_repair="Check 'if s:' before accessing s[0]",
    ),
    BenchmarkCase(
        case_id="py-index-004",
        language="python",
        error_family="index_error",
        error_text="IndexError: tuple index out of range",
        source_snippet="coords = (1, 2)\nz = coords[2]\n",
        expected_error_family="index_error",
        expected_top1_kind="index_error",
        curated=True,
        ground_truth_repair="Extend tuple to (1, 2, 0) or check len(coords) > 2",
    ),
    BenchmarkCase(
        case_id="py-index-005",
        language="python",
        error_family="index_error",
        error_text="IndexError: list index out of range",
        source_snippet="matrix = [[1, 2], [3, 4]]\nval = matrix[2][0]\n",
        expected_error_family="index_error",
        expected_top1_kind="index_error",
        curated=True,
        ground_truth_repair="Add bounds check: if 2 < len(matrix)",
    ),
    # -----------------------------------------------------------------------
    # 8. async / await errors — 5 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="py-async-001",
        language="python",
        error_family="async_error",
        error_text=(
            'sys:1: RuntimeWarning: coroutine \'fetch\' was never awaited\n'
            'RuntimeWarning: Enable tracemalloc to get the object allocation traceback'
        ),
        source_snippet="import asyncio\nasync def fetch(): return 'data'\nresult = fetch()\n",
        expected_error_family="async_error",
        expected_top1_kind="async_error",
        curated=True,
        ground_truth_repair="Await the coroutine: result = await fetch() or asyncio.run(fetch())",
    ),
    BenchmarkCase(
        case_id="py-async-002",
        language="python",
        error_family="async_error",
        error_text="SyntaxError: 'await' outside async function",
        source_snippet="import asyncio\ndef sync_fn():\n    result = await some_coro()\n",
        expected_error_family="async_error",
        expected_top1_kind="async_error",
        curated=True,
        ground_truth_repair="Change def to async def, or remove await and call synchronously",
    ),
    BenchmarkCase(
        case_id="py-async-003",
        language="python",
        error_family="async_error",
        error_text=(
            "TypeError: object coroutine can't be used in 'await' expression"
        ),
        source_snippet="async def run():\n    coro = some_sync_fn()\n    result = await coro\n",
        expected_error_family="async_error",
        expected_top1_kind="async_error",
        curated=True,
        ground_truth_repair="some_sync_fn is not async; remove await or make it async",
    ),
    BenchmarkCase(
        case_id="py-async-004",
        language="python",
        error_family="async_error",
        error_text=(
            "RuntimeError: This event loop is already running"
        ),
        source_snippet="import asyncio\nasync def main(): pass\nasyncio.run(main())\nasyncio.run(main())\n",
        expected_error_family="async_error",
        expected_top1_kind="async_error",
        curated=True,
        ground_truth_repair="Don't call asyncio.run() inside a running event loop",
    ),
    BenchmarkCase(
        case_id="py-async-005",
        language="python",
        error_family="async_error",
        error_text=(
            'sys:1: RuntimeWarning: coroutine \'save\' was never awaited'
        ),
        source_snippet="async def save(data): ...\nasync def process(data):\n    save(data)\n",
        expected_error_family="async_error",
        expected_top1_kind="async_error",
        curated=True,
        ground_truth_repair="Add await: await save(data)",
    ),
    # -----------------------------------------------------------------------
    # 9. TypeScript missing symbol / property — 6 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="ts-missing-001",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2304: Cannot find name 'fetchData'.\n"
            "  src/api.ts(12,5): error TS2304"
        ),
        source_snippet="const result = fetchData('/endpoint');\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Import fetchData or define it before use",
    ),
    BenchmarkCase(
        case_id="ts-missing-002",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2339: Property 'toUppercase' does not exist on type 'string'."
        ),
        source_snippet="const s: string = 'hello';\nconsole.log(s.toUppercase());\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Replace 'toUppercase' with 'toUpperCase'",
    ),
    BenchmarkCase(
        case_id="ts-missing-003",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2304: Cannot find name 'Compoent'.\n"
            "  src/Button.tsx(3,1)"
        ),
        source_snippet="import React from 'react';\nconst Button = () => <Compoent />;\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Replace 'Compoent' with 'Component' or import the correct component",
    ),
    BenchmarkCase(
        case_id="ts-missing-004",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2339: Property 'lenght' does not exist on type 'string[]'."
        ),
        source_snippet="const arr: string[] = [];\nconsole.log(arr.lenght);\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Replace 'lenght' with 'length'",
    ),
    BenchmarkCase(
        case_id="ts-missing-005",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2304: Cannot find name 'useState'.\n"
            "  src/Counter.tsx(2,18)"
        ),
        source_snippet="const [count, setCount] = useState(0);\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Add: import { useState } from 'react'",
    ),
    BenchmarkCase(
        case_id="ts-missing-006",
        language="typescript",
        error_family="typescript_missing_symbol",
        error_text=(
            "error TS2339: Property 'data' does not exist on type 'Error'."
        ),
        source_snippet="catch (e: Error) { console.log(e.data); }\n",
        expected_error_family="typescript_missing_symbol",
        expected_top1_kind="typescript_missing_symbol",
        curated=True,
        ground_truth_repair="Cast e to custom error type or use (e as any).data",
    ),
    # -----------------------------------------------------------------------
    # 10. TypeScript type mismatch / param count — 5 cases
    # -----------------------------------------------------------------------
    BenchmarkCase(
        case_id="ts-type-001",
        language="typescript",
        error_family="typescript_type_mismatch",
        error_text=(
            "error TS2345: Argument of type 'string' is not assignable to "
            "parameter of type 'number'."
        ),
        source_snippet="function add(a: number, b: number): number { return a + b; }\nadd(1, '2');\n",
        expected_error_family="typescript_type_mismatch",
        expected_top1_kind="typescript_type_mismatch",
        curated=True,
        ground_truth_repair="Change '2' to 2 or add Number() conversion",
    ),
    BenchmarkCase(
        case_id="ts-type-002",
        language="typescript",
        error_family="typescript_type_mismatch",
        error_text=(
            "error TS2554: Expected 2 arguments, but got 3."
        ),
        source_snippet="function greet(name: string, greeting: string) {}\ngreet('Alice', 'Hello', 'extra');\n",
        expected_error_family="typescript_type_mismatch",
        expected_top1_kind="typescript_type_mismatch",
        curated=True,
        ground_truth_repair="Remove extra argument or add parameter to greet()",
    ),
    BenchmarkCase(
        case_id="ts-type-003",
        language="typescript",
        error_family="typescript_type_mismatch",
        error_text=(
            "error TS2322: Type 'null' is not assignable to type 'string'."
        ),
        source_snippet="const name: string = null;\n",
        expected_error_family="typescript_type_mismatch",
        expected_top1_kind="typescript_type_mismatch",
        curated=True,
        ground_truth_repair="Change type to 'string | null' or use non-null assertion",
    ),
    BenchmarkCase(
        case_id="ts-type-004",
        language="typescript",
        error_family="typescript_type_mismatch",
        error_text=(
            "error TS2554: Expected 1 arguments, but got 0."
        ),
        source_snippet="function init(config: object) {}\ninit();\n",
        expected_error_family="typescript_type_mismatch",
        expected_top1_kind="typescript_type_mismatch",
        curated=True,
        ground_truth_repair="Pass a config object or make the parameter optional",
    ),
    BenchmarkCase(
        case_id="ts-type-005",
        language="typescript",
        error_family="typescript_type_mismatch",
        error_text=(
            "error TS2345: Argument of type 'number[]' is not assignable to "
            "parameter of type 'string[]'."
        ),
        source_snippet="function join(arr: string[]): string { return arr.join(','); }\njoin([1, 2, 3]);\n",
        expected_error_family="typescript_type_mismatch",
        expected_top1_kind="typescript_type_mismatch",
        curated=True,
        ground_truth_repair="Map to strings: join([1,2,3].map(String))",
    ),
]


def load_suite() -> BenchmarkSuite:
    """Return the immutable benchmark suite."""
    return BenchmarkSuite(cases=tuple(_CASES))
