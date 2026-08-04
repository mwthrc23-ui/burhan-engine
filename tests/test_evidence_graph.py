"""Tests for Evidence Graph V2 and semantic indexers."""
from __future__ import annotations

import json
import unittest

from burhan.evidence import (
    ConfidenceLevel,
    EvidenceEdge,
    EvidenceFact,
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeKind,
    SCHEMA_VERSION,
)
from burhan.index.python_indexer import PythonIndexer
from burhan.index.typescript_indexer import TypeScriptIndexer


# ---------------------------------------------------------------------------
# EvidenceFact
# ---------------------------------------------------------------------------

class EvidenceFactTests(unittest.TestCase):
    def test_fingerprint_is_set_automatically(self) -> None:
        fact = EvidenceFact(source="ast", summary="found NameError", weight=1.0)
        self.assertTrue(fact.fingerprint)
        self.assertEqual(len(fact.fingerprint), 16)

    def test_same_content_same_fingerprint(self) -> None:
        f1 = EvidenceFact(source="ast", summary="found NameError", weight=1.0)
        f2 = EvidenceFact(source="ast", summary="found NameError", weight=2.0)
        self.assertEqual(f1.fingerprint, f2.fingerprint)

    def test_different_content_different_fingerprint(self) -> None:
        f1 = EvidenceFact(source="ast", summary="A", weight=1.0)
        f2 = EvidenceFact(source="ast", summary="B", weight=1.0)
        self.assertNotEqual(f1.fingerprint, f2.fingerprint)

    def test_round_trip(self) -> None:
        fact = EvidenceFact(
            source="stack_trace",
            summary="line 42",
            weight=2.5,
            level=ConfidenceLevel.CONFIRMED,
            collected_at=3,
        )
        restored = EvidenceFact.from_dict(fact.to_dict())
        self.assertEqual(restored.source, fact.source)
        self.assertEqual(restored.summary, fact.summary)
        self.assertEqual(restored.level, ConfidenceLevel.CONFIRMED)
        self.assertEqual(restored.collected_at, 3)


# ---------------------------------------------------------------------------
# EvidenceGraph – immutability
# ---------------------------------------------------------------------------

class EvidenceGraphImmutabilityTests(unittest.TestCase):
    def test_with_node_does_not_mutate_original(self) -> None:
        g1 = EvidenceGraph()
        node = EvidenceNode(id="file:app.py", kind=EvidenceNodeKind.FILE, label="app.py")
        g2 = g1.with_node(node)
        self.assertEqual(len(g1.nodes), 0)
        self.assertEqual(len(g2.nodes), 1)

    def test_with_edge_idempotent(self) -> None:
        edge = EvidenceEdge(source="a", relation="calls", target="b")
        g1 = EvidenceGraph().with_edge(edge)
        g2 = g1.with_edge(edge)
        self.assertEqual(len(g2.edges), 1)

    def test_replace_node_same_id(self) -> None:
        node_v1 = EvidenceNode(id="x", kind=EvidenceNodeKind.SYMBOL, label="old")
        node_v2 = EvidenceNode(id="x", kind=EvidenceNodeKind.SYMBOL, label="new")
        g = EvidenceGraph().with_node(node_v1).with_node(node_v2)
        self.assertEqual(len(g.nodes), 1)
        self.assertEqual(g.nodes[0].label, "new")


# ---------------------------------------------------------------------------
# EvidenceGraph – facts and queries
# ---------------------------------------------------------------------------

class EvidenceGraphFactTests(unittest.TestCase):
    def test_all_facts_includes_loose_and_attached(self) -> None:
        loose = EvidenceFact(source="s", summary="loose", weight=1.0)
        attached = EvidenceFact(source="s", summary="attached", weight=1.0)
        node = EvidenceNode(
            id="n1", kind=EvidenceNodeKind.HYPOTHESIS, label="h",
            facts=(attached,)
        )
        g = EvidenceGraph().with_node(node).with_fact(loose)
        self.assertEqual(len(g.all_facts()), 2)

    def test_confirmed_facts_filtered(self) -> None:
        c = EvidenceFact(source="s", summary="c", weight=1.0, level=ConfidenceLevel.CONFIRMED)
        i = EvidenceFact(source="s", summary="i", weight=1.0, level=ConfidenceLevel.INFERRED)
        g = EvidenceGraph().with_fact(c).with_fact(i)
        self.assertEqual(len(g.confirmed_facts()), 1)

    def test_opposing_facts(self) -> None:
        contra_node = EvidenceNode(
            id="contra:n",
            kind=EvidenceNodeKind.HYPOTHESIS,
            label="counter",
            facts=(EvidenceFact(source="s", summary="contradicting", weight=1.0),),
        )
        hyp_id = "hypothesis:0"
        g = (
            EvidenceGraph()
            .with_node(contra_node)
            .with_edge(EvidenceEdge(source="contra:n", relation="contradicts", target=hyp_id))
        )
        opposing = g.opposing_facts(hyp_id)
        self.assertEqual(len(opposing), 1)
        self.assertEqual(opposing[0].summary, "contradicting")

    def test_attach_fact_to_existing_node(self) -> None:
        node = EvidenceNode(id="sym:foo", kind=EvidenceNodeKind.SYMBOL, label="foo")
        fact = EvidenceFact(source="ast", summary="defined at line 5", weight=1.0)
        g = EvidenceGraph().with_node(node).attach_fact_to_node("sym:foo", fact)
        found = next(n for n in g.nodes if n.id == "sym:foo")
        self.assertEqual(len(found.facts), 1)

    def test_attach_fact_creates_placeholder_node(self) -> None:
        fact = EvidenceFact(source="ast", summary="orphan", weight=1.0)
        g = EvidenceGraph().attach_fact_to_node("new:node", fact)
        self.assertEqual(len(g.nodes), 1)
        self.assertEqual(g.nodes[0].id, "new:node")


# ---------------------------------------------------------------------------
# EvidenceGraph – serialisation
# ---------------------------------------------------------------------------

class EvidenceGraphSerialisationTests(unittest.TestCase):
    def _build(self) -> EvidenceGraph:
        node = EvidenceNode(
            id="file:x.py",
            kind=EvidenceNodeKind.FILE,
            label="x.py",
            facts=(EvidenceFact(source="scanner", summary="scanned", weight=1.0),),
        )
        edge = EvidenceEdge(source="file:x.py", relation="defines", target="sym:foo", confidence=0.9)
        return EvidenceGraph().with_node(node).with_edge(edge)

    def test_to_dict_has_schema_version(self) -> None:
        d = self._build().to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)

    def test_round_trip(self) -> None:
        g = self._build()
        restored = EvidenceGraph.from_dict(g.to_dict())
        self.assertEqual(len(restored.nodes), len(g.nodes))
        self.assertEqual(len(restored.edges), len(g.edges))

    def test_json_stable(self) -> None:
        g = self._build()
        j1 = json.dumps(g.to_dict(), sort_keys=True)
        j2 = json.dumps(g.to_dict(), sort_keys=True)
        self.assertEqual(j1, j2)

    def test_arabic_summary_nonempty(self) -> None:
        g = self._build()
        summary = g.arabic_summary()
        self.assertIn("الأدلة", summary)


# ---------------------------------------------------------------------------
# PythonIndexer
# ---------------------------------------------------------------------------

class PythonIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexer = PythonIndexer()

    def test_supports_py(self) -> None:
        self.assertTrue(self.indexer.supports("src/app.py"))

    def test_not_supports_ts(self) -> None:
        self.assertFalse(self.indexer.supports("app.ts"))

    def test_extracts_function(self) -> None:
        src = "def greet(name):\n    return 'hi'\n"
        result = self.indexer.index_source("app.py", src)
        names = [s.name for s in result.symbols]
        self.assertIn("greet", names)

    def test_extracts_class(self) -> None:
        src = "class Foo:\n    def bar(self): pass\n"
        result = self.indexer.index_source("app.py", src)
        kinds = {s.kind for s in result.symbols}
        self.assertIn("class", kinds)

    def test_extracts_method_with_parent(self) -> None:
        src = "class Foo:\n    def bar(self): pass\n"
        result = self.indexer.index_source("app.py", src)
        method = next((s for s in result.symbols if s.name == "bar"), None)
        self.assertIsNotNone(method)
        self.assertEqual(method.parent, "Foo")  # type: ignore[union-attr]

    def test_extracts_imports(self) -> None:
        src = "from os import path\nimport sys\n"
        result = self.indexer.index_source("app.py", src)
        modules = [i.module for i in result.imports]
        self.assertIn("os", modules)
        self.assertIn("sys", modules)

    def test_extracts_calls(self) -> None:
        src = "def run():\n    print('hello')\n    len([1,2])\n"
        result = self.indexer.index_source("app.py", src)
        callees = [c.callee for c in result.calls]
        self.assertIn("print", callees)
        self.assertIn("len", callees)

    def test_syntax_error_returns_degraded(self) -> None:
        result = self.indexer.index_source("bad.py", "def (:")
        self.assertTrue(result.degraded)
        self.assertEqual(result.confidence, 0.0)

    def test_not_degraded_for_valid_python(self) -> None:
        result = self.indexer.index_source("ok.py", "x = 1\n")
        self.assertFalse(result.degraded)
        self.assertEqual(result.confidence, 1.0)

    def test_module_level_variable(self) -> None:
        src = "VERSION = '1.0'\n"
        result = self.indexer.index_source("app.py", src)
        names = [s.name for s in result.symbols]
        self.assertIn("VERSION", names)

    def test_to_dict_json_serialisable(self) -> None:
        src = "def foo(): pass\n"
        result = self.indexer.index_source("app.py", src)
        import json
        json.dumps(result.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# TypeScriptIndexer
# ---------------------------------------------------------------------------

class TypeScriptIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexer = TypeScriptIndexer()

    def test_supports_ts(self) -> None:
        self.assertTrue(self.indexer.supports("src/index.ts"))

    def test_supports_tsx(self) -> None:
        self.assertTrue(self.indexer.supports("App.tsx"))

    def test_not_supports_py(self) -> None:
        self.assertFalse(self.indexer.supports("app.py"))

    def test_extracts_function(self) -> None:
        src = "function greet(name: string): string { return name; }\n"
        result = self.indexer.index_source("app.ts", src)
        names = [s.name for s in result.symbols]
        self.assertIn("greet", names)

    def test_extracts_class(self) -> None:
        src = "export class Greeter { greet() { } }\n"
        result = self.indexer.index_source("app.ts", src)
        kinds = {s.kind for s in result.symbols}
        self.assertIn("class", kinds)

    def test_extracts_interface(self) -> None:
        src = "export interface User { name: string; }\n"
        result = self.indexer.index_source("types.ts", src)
        kinds = {s.kind for s in result.symbols}
        self.assertIn("interface", kinds)

    def test_extracts_type_alias(self) -> None:
        src = "export type ID = string;\n"
        result = self.indexer.index_source("types.ts", src)
        kinds = {s.kind for s in result.symbols}
        self.assertIn("type", kinds)

    def test_extracts_imports(self) -> None:
        src = "import { foo, bar } from './utils';\n"
        result = self.indexer.index_source("app.ts", src)
        modules = [i.module for i in result.imports]
        self.assertIn("./utils", modules)

    def test_is_degraded(self) -> None:
        result = self.indexer.index_source("app.ts", "const x = 1;\n")
        self.assertTrue(result.degraded)
        self.assertEqual(result.confidence, 0.6)

    def test_to_dict_json_serialisable(self) -> None:
        src = "function foo() {}\n"
        result = self.indexer.index_source("app.ts", src)
        import json
        json.dumps(result.to_dict())  # must not raise


if __name__ == "__main__":
    unittest.main()
