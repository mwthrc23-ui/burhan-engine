"""async/await error family handler.

Handles four common async/await mistakes in Python:

1. Coroutine never awaited  (RuntimeWarning)
2. ``await`` outside async function  (SyntaxError)
3. Non-coroutine passed to ``await``  (TypeError)
4. ``asyncio.run()`` called inside a running loop  (RuntimeError)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_CORO_NEVER_AWAITED = re.compile(
    r"RuntimeWarning:\s+coroutine\s+['\"](?P<name>[^'\"]+)['\"]?\s+was never awaited"
)
_AWAIT_OUTSIDE = re.compile(
    r"SyntaxError:\s+'await' outside (?:async )?function"
)
_LOOP_RUNNING = re.compile(
    r"RuntimeError:\s+This event loop is already running"
)
_TYPE_AWAIT = re.compile(
    r"TypeError:.*coroutine.*can't be used in 'await' expression"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AsyncCandidate:
    rank: int
    description: str
    code_template: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "description": self.description,
            "code_template": self.code_template,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class AsyncHypothesis:
    kind: str
    sub_kind: str
    explanation: str
    confidence: float
    supporting: tuple[str, ...]
    opposing: tuple[str, ...]
    candidates: tuple[AsyncCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "sub_kind": self.sub_kind,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "supporting": list(self.supporting),
            "opposing": list(self.opposing),
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class AsyncErrorHandler:
    """Diagnose async/await errors and return ranked hypotheses."""

    family = "async_error"

    def diagnose(self, error_text: str) -> tuple[AsyncHypothesis, ...]:
        hypotheses: list[AsyncHypothesis] = []

        # 1. Coroutine never awaited
        m = _CORO_NEVER_AWAITED.search(error_text)
        if m:
            name = m.group("name")
            hypotheses.extend(self._coroutine_never_awaited(name))

        # 2. await outside async function
        if _AWAIT_OUTSIDE.search(error_text):
            hypotheses.extend(self._await_outside_async())

        # 3. Coroutine in await expression (not actually a coroutine)
        if _TYPE_AWAIT.search(error_text):
            hypotheses.extend(self._wrong_await_target())

        # 4. Event loop already running
        if _LOOP_RUNNING.search(error_text):
            hypotheses.extend(self._loop_already_running())

        return tuple(sorted(hypotheses, key=lambda h: -h.confidence))

    # ------------------------------------------------------------------
    # Sub-handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _coroutine_never_awaited(name: str) -> list[AsyncHypothesis]:
        return [
            AsyncHypothesis(
                kind="async_error",
                sub_kind="coroutine_never_awaited",
                explanation=(
                    f"Coroutine {name!r} was called but never awaited. "
                    "The call returned a coroutine object that was never scheduled."
                ),
                confidence=0.90,
                supporting=(
                    f"RuntimeWarning: coroutine {name!r} was never awaited",
                    "Python emits this warning exactly when an unawaited coroutine is garbage-collected",
                ),
                opposing=(),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description=f"Add await: result = await {name}(...)",
                        code_template=f"result = await {name}()",
                        confidence=0.85,
                    ),
                    AsyncCandidate(
                        rank=2,
                        description="Use asyncio.run() if calling from synchronous context",
                        code_template=f"import asyncio\nresult = asyncio.run({name}())",
                        confidence=0.70,
                    ),
                    AsyncCandidate(
                        rank=3,
                        description="Schedule with asyncio.create_task() if fire-and-forget",
                        code_template=f"asyncio.create_task({name}())",
                        confidence=0.50,
                    ),
                ),
            ),
            AsyncHypothesis(
                kind="async_error",
                sub_kind="missing_async_context",
                explanation=(
                    f"The caller of {name!r} may not be an async function, "
                    "preventing use of await."
                ),
                confidence=0.55,
                supporting=(f"Coroutine {name!r} was not awaited",),
                opposing=("Cannot confirm without seeing caller definition",),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Convert the calling function to async def",
                        code_template=f"async def caller():\n    result = await {name}()",
                        confidence=0.60,
                    ),
                ),
            ),
            AsyncHypothesis(
                kind="async_error",
                sub_kind="should_be_sync",
                explanation=(
                    f"If {name!r} does not need to be async, "
                    "remove the async/await machinery entirely."
                ),
                confidence=0.30,
                supporting=(),
                opposing=(f"Coroutine {name!r} exists — likely intended to be async",),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description=f"Remove async from {name!r} definition",
                        code_template=f"def {name}():  # remove async keyword\n    ...",
                        confidence=0.25,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _await_outside_async() -> list[AsyncHypothesis]:
        return [
            AsyncHypothesis(
                kind="async_error",
                sub_kind="await_outside_async",
                explanation=(
                    "'await' was used inside a synchronous function. "
                    "Python requires the enclosing function to be declared with 'async def'."
                ),
                confidence=0.92,
                supporting=("SyntaxError: 'await' outside async function",),
                opposing=(),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Change 'def' to 'async def' for the enclosing function",
                        code_template="async def my_function():\n    result = await some_coro()",
                        confidence=0.88,
                    ),
                    AsyncCandidate(
                        rank=2,
                        description="Remove the await and call the function synchronously if it is not actually a coroutine",
                        code_template="def my_function():\n    result = some_function()  # no await",
                        confidence=0.60,
                    ),
                    AsyncCandidate(
                        rank=3,
                        description="Wrap in asyncio.run() at the top level",
                        code_template="import asyncio\nasyncio.run(my_async_function())",
                        confidence=0.55,
                    ),
                ),
            ),
            AsyncHypothesis(
                kind="async_error",
                sub_kind="missing_async_def",
                explanation=(
                    "The function using 'await' was defined without 'async'. "
                    "This is a common oversight when adding async calls to existing code."
                ),
                confidence=0.70,
                supporting=("'await' present in a non-async function body",),
                opposing=(),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Add 'async' keyword to function definition",
                        code_template="async def existing_function():\n    ...",
                        confidence=0.70,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _wrong_await_target() -> list[AsyncHypothesis]:
        return [
            AsyncHypothesis(
                kind="async_error",
                sub_kind="non_coroutine_awaited",
                explanation=(
                    "'await' was applied to a non-coroutine object. "
                    "The called function is synchronous and does not return a coroutine."
                ),
                confidence=0.80,
                supporting=("TypeError about coroutine in await expression",),
                opposing=(),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Remove 'await' from the synchronous call",
                        code_template="result = sync_function()  # no await",
                        confidence=0.78,
                    ),
                    AsyncCandidate(
                        rank=2,
                        description="Make the called function async if it needs to be awaitable",
                        code_template="async def sync_function():\n    ...",
                        confidence=0.55,
                    ),
                    AsyncCandidate(
                        rank=3,
                        description="Wrap with asyncio.to_thread() for blocking I/O",
                        code_template="result = await asyncio.to_thread(sync_function)",
                        confidence=0.50,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _loop_already_running() -> list[AsyncHypothesis]:
        return [
            AsyncHypothesis(
                kind="async_error",
                sub_kind="event_loop_already_running",
                explanation=(
                    "asyncio.run() was called inside an already-running event loop. "
                    "This typically happens in Jupyter or nested async contexts."
                ),
                confidence=0.85,
                supporting=("RuntimeError: This event loop is already running",),
                opposing=(),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Use 'await' directly instead of asyncio.run() inside async context",
                        code_template="result = await my_coroutine()",
                        confidence=0.82,
                    ),
                    AsyncCandidate(
                        rank=2,
                        description="Use nest_asyncio for Jupyter (install nest_asyncio)",
                        code_template="import nest_asyncio\nnest_asyncio.apply()\nasyncio.run(coro())",
                        confidence=0.55,
                    ),
                    AsyncCandidate(
                        rank=3,
                        description="Schedule with loop.create_task() instead",
                        code_template="loop = asyncio.get_event_loop()\ntask = loop.create_task(coro())",
                        confidence=0.45,
                    ),
                ),
            ),
            AsyncHypothesis(
                kind="async_error",
                sub_kind="nested_asyncio_run",
                explanation=(
                    "A second asyncio.run() call was made before the first completed. "
                    "asyncio.run() should only be called once at the top level."
                ),
                confidence=0.60,
                supporting=("Nested asyncio.run() is a common anti-pattern",),
                opposing=("Loop may be running for other reasons",),
                candidates=(
                    AsyncCandidate(
                        rank=1,
                        description="Remove the inner asyncio.run() and use await",
                        code_template="# Remove asyncio.run(inner()) and use: await inner()",
                        confidence=0.60,
                    ),
                ),
            ),
        ]
