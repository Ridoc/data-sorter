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
# Scope is the whole module, not just main(): the confirmation gate was extracted
# into _confirm_folder_moves(). These tests assert the SAFETY PROPERTIES of those
# statements, which must keep holding wherever the gate lives — pinning them to
# main() would make a pure refactor look like a regression.
SCOPE = TREE


def _grab(pred, what):
    for node in ast.walk(SCOPE):
        if isinstance(node, ast.Assign) and pred(node):
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"statement not found in sort.py: {what}")


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
        # WHY extracted too: the gate test below USES is_review to pick the expected
        # prefix. Hardcoding it in the test body made the suite green even when
        # production changed `== 0` to `< 50` — the exact vacuity this module exists
        # to eliminate (a test must never encode the predicate it is testing).
        "is_review": _grab(
            lambda n: isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "is_review",
            "is_review predicate"),
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
    # is_review comes from the REAL production statement, not from this test body.
    ir = _run(stmts["is_review"], {"fm": fm})
    ns = _run(stmts["prefix"], {"fm": fm, "is_review": ir["is_review"]})
    assert ns["prefix"] == want


def test_is_review_predicate_flags_only_zero_confidence(stmts):
    """Pins the gate's own rule: review == confidence 0 (a failed/uncertain
    classification), NOT merely 'below some threshold'."""
    for conf, want in [(0, True), (49, False), (50, False), (85, False)]:
        ir = _run(stmts["is_review"], {"fm": {"confidence": conf}})
        assert ir["is_review"] is want, conf


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


def test_gate_is_reachable_from_main():
    """The statements above are only safety guarantees if the LIVE gate calls them.

    Widening SCOPE to the module means a dead/unreachable copy would satisfy the
    other tests, so pin reachability separately: main() must both invoke
    _confirm_folder_moves() and keep the guard that gates it (interactive only —
    never under --dry-run or --execute).
    """
    main_fn = next(
        n for n in TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    # The call sits in an If BODY, not its condition — search the body statements.
    def _calls_gate(node):
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_confirm_folder_moves"
            for n in ast.walk(node)
        )

    guards = [
        n for n in ast.walk(main_fn)
        if isinstance(n, ast.If) and _calls_gate(n)
    ]
    assert guards, "main() no longer calls _confirm_folder_moves() — gate unreachable"

    guard_src = ast.get_source_segment(SRC, guards[0].test)
    for attr in ("dry_run", "execute"):
        assert attr in guard_src, f"gate guard lost its '{attr}' check: {guard_src}"
    assert "approved_folder_moves" in guard_src