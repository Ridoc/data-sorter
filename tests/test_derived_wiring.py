"""Wiring tests for the derived-naming safety feature (plan BATCH_3/BATCH_4).

WHY extract source instead of calling main(): the previous test re-implemented the
--execute predicate INSIDE the test body, so it could never fail when production
changed — while it was the only thing preventing a silently auto-applied name.
These tests locate the real statements in sort.main() by AST and execute them, so a
revert in the repo changes the outcome. If a future refactor renames these
statements the tests fail loudly ("statement not found") rather than passing vacuously.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "sort.py").read_text()
TREE = ast.parse(SRC)
MAIN = next(n for n in TREE.body
            if isinstance(n, ast.FunctionDef) and n.name == "main")


def _grab(pred, what):
    for node in ast.walk(MAIN):
        if isinstance(node, ast.Assign) and pred(node):
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"statement not found in sort.main(): {what}")


@pytest.fixture(scope="module")
def stmts():
    """Extract lazily so a renamed/removed statement FAILS A TEST, rather than
    aborting collection (which reads like a broken test file, not a broken feature)."""
    return {
        "prefix": _grab(
            lambda n: any(isinstance(t, ast.Constant) and isinstance(t.value, str)
                          and "[derived]" in t.value for t in ast.walk(n.value)),
            "confirmation-gate prefix"),
        "filter": _grab(
            lambda n: isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "approved_folder_moves"
            and isinstance(n.value, ast.ListComp),
            "--execute auto-approve filter"),
        "note": _grab(
            lambda n: isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "derived_note",
            "derived note"),
        "count": _grab(
            lambda n: isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "derived_folder_count",
            "derived count"),
        "prop": _grab(
            lambda n: isinstance(n.targets[0], ast.Subscript)
            and isinstance(n.targets[0].value, ast.Name)
            and n.targets[0].value.id == "move"
            and isinstance(n.targets[0].slice, ast.Constant)
            and n.targets[0].slice.value == "_derived",
            "move['_derived'] propagation"),
    }


def _run(code, ns):
    exec(compile(code, "<extracted>", "exec"), ns)
    return ns


MOVES = [
    {"source": "s1", "file_count": 10, "confidence": 85, "_derived": False},
    {"source": "s2", "file_count": 10, "confidence": 70, "_derived": True},
    {"source": "s3", "file_count": 10, "confidence": 0, "_derived": True},
    {"source": "s4", "file_count": 10, "confidence": 85},
]


@pytest.mark.parametrize("derived,conf,want", [
    (True, 70, "\u26a0\ufe0f [derived] "),
    (False, 85, ""),                              # no over-application
    (None, 0, "\u26a0\ufe0f [REVIEW] "),
    (True, 0, "\u26a0\ufe0f [REVIEW] "),           # review wins over derived
])
def test_confirmation_gate_prefix(stmts, derived, conf, want):
    fm = {"confidence": conf}
    if derived is not None:
        fm["_derived"] = derived
    ns = _run(stmts["prefix"], {"fm": fm, "is_review": fm.get("confidence", 0) == 0})
    assert ns["prefix"] == want


def test_execute_filter_excludes_derived(stmts):
    ns = _run(stmts["filter"], {"approved_folder_moves": list(MOVES)})
    assert [m["source"] for m in ns["approved_folder_moves"]] == ["s1", "s4"]


def test_derived_flag_propagates_to_move_and_blocks_execute(stmts):
    """End-to-end: a derived classification must reach the move AND stop --execute."""
    for fc, want in [({"_derived": True}, True), ({"_derived": False}, False), ({}, False)]:
        move = {"source": "s", "file_count": 1, "confidence": 70}
        _run(stmts["prop"], {"move": move, "fc": fc})
        assert move.get("_derived", False) is want, fc
        kept = _run(stmts["filter"], {"approved_folder_moves": [move]})["approved_folder_moves"]
        assert bool(kept) is not want, f"derived={want} kept={bool(kept)}"


def test_ready_summary_reports_derived_count(stmts):
    ns = _run(stmts["count"], {"approved_folder_moves": list(MOVES)})
    ns = _run(stmts["note"], ns)
    assert ns["derived_folder_count"] == 2
    assert "auto-named" in ns["derived_note"] and "2" in ns["derived_note"]


def test_ready_summary_silent_without_derived(stmts):
    ns = _run(stmts["count"], {"approved_folder_moves": [MOVES[0], MOVES[3]]})
    ns = _run(stmts["note"], ns)
    assert ns["derived_folder_count"] == 0
    assert ns["derived_note"] == ""