"""Integration tests for the folder-move confirmation gate.

tech-debt folder-move-confirm-gate-no-integration-test: the gate's index-parsing
flow ('1 3' | 'all' | 'none') was never exercised with a mocked stdin, so the one
prompt standing between a folder move and the filesystem was untested.

These drive the REAL gate function from sort.py with a monkeypatched
builtins.input — no re-implementation of the parse in the test body, so a revert
in the gate flips these outcomes instead of passing vacuously.
"""
import builtins
import io

import pytest
from rich.console import Console

from sort import _confirm_folder_moves


@pytest.fixture
def moves(tmp_path):
    """Four folder moves with source/target under tmp_path (the gate prints paths
    relative to nas_root)."""
    out = []
    for i in range(1, 5):
        out.append(
            {
                "source": str(tmp_path / f"folder{i}"),
                "target": str(tmp_path / "Media" / "Photos" / f"folder{i}"),
                "file_count": i,
                "total_size": 0,
                "confidence": 85,
            }
        )
    return out


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=200)


def _answer(monkeypatch, text):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: text)


def test_all_keeps_every_move(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "all")
    assert _confirm_folder_moves(moves, tmp_path, console) == moves


def test_all_is_case_insensitive(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "ALL")
    assert _confirm_folder_moves(moves, tmp_path, console) == moves


def test_none_rejects_everything(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "none")
    assert _confirm_folder_moves(moves, tmp_path, console) == []


def test_index_subset_approves_only_those_folders(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "1 3")
    assert _confirm_folder_moves(moves, tmp_path, console) == [moves[0], moves[2]]


def test_subset_does_not_mutate_caller_list(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "2")
    approved = _confirm_folder_moves(moves, tmp_path, console)
    assert approved == [moves[1]]
    assert len(moves) == 4


def test_out_of_range_index_approves_nothing(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "9")
    assert _confirm_folder_moves(moves, tmp_path, console) == []


def test_non_numeric_input_approves_nothing(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "x")
    assert _confirm_folder_moves(moves, tmp_path, console) == []


def test_mixed_valid_invalid_approves_nothing(monkeypatch, moves, console, tmp_path):
    # "1 x" must NOT partially approve folder 1: the parse fails, so nothing moves.
    _answer(monkeypatch, "1 x")
    assert _confirm_folder_moves(moves, tmp_path, console) == []


def test_empty_input_approves_nothing(monkeypatch, moves, console, tmp_path):
    _answer(monkeypatch, "")
    assert _confirm_folder_moves(moves, tmp_path, console) == []


def test_derived_move_is_flagged_in_the_listing(monkeypatch, moves, console, tmp_path):
    moves[1]["_derived"] = True
    _answer(monkeypatch, "all")
    _confirm_folder_moves(moves, tmp_path, console)
    listing = console.file.getvalue()
    assert "⚠️ [derived]" in listing
    assert "folder2" in listing


def test_markup_like_folder_names_render_verbatim(monkeypatch, console, tmp_path):
    """A folder whose name looks like Rich markup must still display as itself.

    The listing is the last human check before folders move on the real NAS, so a
    name that renders as altered text is a name consent is given against. Without
    escape(), "[bold]Album" renders as "Album" (and a lowercase "[derived]"
    disappears entirely) because Rich parses the brackets as a style tag.
    """
    moves = [
        {
            "source": str(tmp_path / "[bold]Album"),
            "target": str(tmp_path / "Media" / "Photos" / "[green]2024"),
            "file_count": 3,
            "total_size": 0,
            "confidence": 85,
            "_derived": True,
        }
    ]
    _answer(monkeypatch, "all")
    _confirm_folder_moves(moves, tmp_path, console)
    listing = console.file.getvalue()
    # DISCRIMINATING assertion: with escape() the literal brackets survive as TEXT.
    # Revert the escape() calls and Rich eats them as style tags — "[bold]Album"
    # renders as "Album" — so both lines below disappear and this test goes red.
    assert "[bold]Album" in listing
    assert "[green]2024" in listing
    # The derived warning is likewise text, not markup (lowercase tags get eaten).
    assert "⚠️ [derived]" in listing
