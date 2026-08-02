from __future__ import annotations

import unittest

from burhan.model import BirNode, BurhanState, NodeKind


class BurhanStateTests(unittest.TestCase):
    def test_adding_a_node_returns_a_new_state(self) -> None:
        original = BurhanState.empty(goal="إصلاح الخطأ دون تغيير الواجهة")
        node = BirNode(
            id="symbol:app.greet",
            kind=NodeKind.SYMBOL,
            label="greet",
            attributes=(("file", "app.py"),),
        )

        updated = original.with_node(node)

        self.assertEqual(original.nodes, ())
        self.assertEqual(updated.nodes, (node,))
        self.assertIsNot(original, updated)

    def test_duplicate_node_ids_replace_the_old_value_without_mutation(self) -> None:
        first = BirNode("event:error", NodeKind.EVENT, "NameError")
        second = BirNode("event:error", NodeKind.EVENT, "TS2304")
        state = BurhanState.empty("diagnose").with_node(first)

        updated = state.with_node(second)

        self.assertEqual(len(updated.nodes), 1)
        self.assertEqual(updated.nodes[0].label, "TS2304")
        self.assertEqual(state.nodes[0].label, "NameError")


if __name__ == "__main__":
    unittest.main()
