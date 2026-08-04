"""Tests for enhanced semantic indexing (Phase 2)."""
from __future__ import annotations

import pytest

from burhan.index.python_indexer import PythonIndexer, EnhancedIndexResult
from burhan.index.typescript_indexer import TypeScriptIndexer
from burhan.index.incremental_index import IncrementalIndex


# ---------------------------------------------------------------------------
# Python indexer — enhanced facts
# ---------------------------------------------------------------------------

PYTHON_SOURCE = """\
import os
from pathlib import Path

CONSTANT = 42

class Animal:
    def speak(self):
        pass

class Dog(Animal):
    def speak(self):
        name = "dog"
        return name

def test_dog_speaks():
    d = Dog()
    d.speak()
"""


class TestPythonIndexerEnhanced:
    def setup_method(self) -> None:
        self.indexer = PythonIndexer()

    def test_index_source_returns_index_result(self) -> None:
        result = self.indexer.index_source("sample.py", PYTHON_SOURCE)
        assert result.file == "sample.py"
        assert result.language == "python"
        assert not result.degraded

    def test_symbols_include_classes_and_functions(self) -> None:
        result = self.indexer.index_source("sample.py", PYTHON_SOURCE)
        names = {s.name for s in result.symbols}
        assert "Animal" in names
        assert "Dog" in names
        assert "test_dog_speaks" in names

    def test_symbols_include_module_variable(self) -> None:
        result = self.indexer.index_source("sample.py", PYTHON_SOURCE)
        names = {s.name for s in result.symbols}
        assert "CONSTANT" in names

    def test_imports_collected(self) -> None:
        result = self.indexer.index_source("sample.py", PYTHON_SOURCE)
        modules = {i.module for i in result.imports}
        assert "os" in modules
        assert "pathlib" in modules

    def test_calls_collected(self) -> None:
        enhanced = self.indexer.index_enhanced("sample.py", PYTHON_SOURCE)
        callee_names = {c.callee for c in enhanced.base.calls}
        assert "Dog" in callee_names or "speak" in callee_names

    def test_inheritance_detected(self) -> None:
        enhanced = self.indexer.index_enhanced("sample.py", PYTHON_SOURCE)
        child_to_base = {i.child_class: i.base_class for i in enhanced.inheritance}
        assert child_to_base.get("Dog") == "Animal"

    def test_uses_collected_inside_scope(self) -> None:
        enhanced = self.indexer.index_enhanced("sample.py", PYTHON_SOURCE)
        use_names = {u.name for u in enhanced.uses}
        assert "Dog" in use_names

    def test_test_symbols_identified(self) -> None:
        enhanced = self.indexer.index_enhanced("sample.py", PYTHON_SOURCE)
        assert "test_dog_speaks" in enhanced.test_symbols

    def test_syntax_error_returns_degraded(self) -> None:
        result = self.indexer.index_source("bad.py", "def f(:\n")
        assert result.degraded
        assert result.confidence == 0.0

    def test_enhanced_to_dict_includes_inheritance(self) -> None:
        enhanced = self.indexer.index_enhanced("sample.py", PYTHON_SOURCE)
        d = enhanced.to_dict()
        assert "inheritance" in d
        assert "uses" in d
        assert "test_symbols" in d

    def test_method_parent_is_class(self) -> None:
        result = self.indexer.index_source("sample.py", PYTHON_SOURCE)
        dog_speak = next(
            (s for s in result.symbols if s.name == "speak" and s.parent == "Dog"), None
        )
        assert dog_speak is not None

    def test_supports_py_files(self) -> None:
        assert self.indexer.supports("module.py")
        assert self.indexer.supports("stub.pyi")
        assert not self.indexer.supports("app.ts")

    def test_confidence_is_1_for_valid_source(self) -> None:
        result = self.indexer.index_source("ok.py", "x = 1\n")
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# TypeScript indexer
# ---------------------------------------------------------------------------

TS_SOURCE = """\
import { useState } from 'react';
import React from 'react';

interface User {
  name: string;
}

type UserId = number;

class UserService {
  getUser(id: number): User {
    return { name: 'Alice' };
  }
}

export function fetchUsers(): User[] {
  return [];
}

const handler = async () => {
  const svc = new UserService();
  svc.getUser(1);
};
"""


class TestTypeScriptIndexer:
    def setup_method(self) -> None:
        self.indexer = TypeScriptIndexer()

    def test_supports_ts_files(self) -> None:
        assert self.indexer.supports("app.ts")
        assert self.indexer.supports("comp.tsx")
        assert not self.indexer.supports("main.py")

    def test_symbols_include_class_and_function(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        names = {s.name for s in result.symbols}
        assert "UserService" in names
        assert "fetchUsers" in names

    def test_interface_collected(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        names = {s.name for s in result.symbols}
        assert "User" in names

    def test_type_alias_collected(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        names = {s.name for s in result.symbols}
        assert "UserId" in names

    def test_imports_collected(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        modules = {i.module for i in result.imports}
        assert "react" in modules

    def test_degraded_mode_flag(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        assert result.degraded is True
        assert result.confidence < 1.0

    def test_calls_collected(self) -> None:
        result = self.indexer.index_source("app.ts", TS_SOURCE)
        callees = {c.callee for c in result.calls}
        assert len(callees) > 0


# ---------------------------------------------------------------------------
# Incremental index
# ---------------------------------------------------------------------------

class TestIncrementalIndex:
    def setup_method(self) -> None:
        self.indexer = PythonIndexer()
        self.cache = IncrementalIndex(self.indexer)

    def test_first_call_is_miss(self) -> None:
        self.cache.index("a.py", "x = 1\n")
        stats = self.cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0

    def test_second_call_same_content_is_hit(self) -> None:
        content = "x = 1\n"
        self.cache.index("a.py", content)
        self.cache.index("a.py", content)
        stats = self.cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_changed_content_is_miss(self) -> None:
        self.cache.index("a.py", "x = 1\n")
        self.cache.index("a.py", "x = 2\n")
        stats = self.cache.stats()
        assert stats["misses"] == 2

    def test_different_files_tracked_separately(self) -> None:
        self.cache.index("a.py", "x = 1\n")
        self.cache.index("b.py", "x = 1\n")
        assert self.cache.cache_size() == 2

    def test_cache_size_bounded(self) -> None:
        small = IncrementalIndex(self.indexer, max_entries=2)
        for i in range(5):
            small.index(f"f{i}.py", f"x = {i}\n")
        assert small.cache_size() <= 2

    def test_invalidate_removes_entries(self) -> None:
        self.cache.index("a.py", "x = 1\n")
        removed = self.cache.invalidate("a.py")
        assert removed == 1
        assert self.cache.cache_size() == 0

    def test_clear_resets_stats(self) -> None:
        self.cache.index("a.py", "x = 1\n")
        self.cache.clear()
        stats = self.cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["cache_size"] == 0

    def test_unsupported_file_returns_degraded(self) -> None:
        result = self.cache.index("app.js", "const x = 1;")
        assert result.degraded

    def test_stats_hit_rate(self) -> None:
        content = "x = 1\n"
        self.cache.index("a.py", content)
        self.cache.index("a.py", content)
        stats = self.cache.stats()
        assert stats["hit_rate"] == 0.5

    def test_invalid_max_entries_raises(self) -> None:
        with pytest.raises(ValueError):
            IncrementalIndex(self.indexer, max_entries=0)

    def test_language_proxied_from_adapter(self) -> None:
        assert self.cache.language == "python"

    def test_supports_proxied_from_adapter(self) -> None:
        assert self.cache.supports("foo.py")
        assert not self.cache.supports("foo.ts")
