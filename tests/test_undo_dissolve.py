"""Undo-safety tests for folder dissolve + collision moves.

WHY: a dissolve used to log ONE `folder_move` record whose target was the shared
CATEGORY directory. `reverse_move` treats any non-"delete" action as a directory
move, so `--undo` did `shutil.move(Media/Photos, .../bilder&screens)` -- relocating
the entire photo library into a folder named after a dump. These tests pin the
per-child logging that makes the operation reversible.
"""

import csv

import pytest

from sorter.executor import execute_moves
from sorter.undo import undo_last_run


def _make_tree(root):
    """root/bilder&screens/{a.jpg, sub/b.jpg}"""
    src = root / "bilder&screens"
    (src / "sub").mkdir(parents=True)
    (src / "a.jpg").write_bytes(b"AAA")
    (src / "sub" / "b.jpg").write_bytes(b"BBB")
    return src


def _dissolve_move(src, target):
    return {
        "source": str(src),
        "target": str(target),
        "is_folder": True,
        "_dissolve": True,
        "confidence": 85,
        "file_count": 2,
        "reason": "medium-word dump",
    }


def _rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestDissolveLogging:
    def test_dissolve_logs_one_move_record_per_child(self, tmp_path):
        src = _make_tree(tmp_path)
        target = tmp_path / "Media" / "Photos"
        csv_path = tmp_path / "undo.csv"

        execute_moves([_dissolve_move(src, target)], [], tmp_path, csv_path,
                      tmp_path / "trash", dry_run=False)

        rows = _rows(csv_path)
        moves = [r for r in rows if r["action"] == "move"]
        assert len(moves) == 2, f"expected one record per child, got {len(moves)}"
        names = sorted(r["source_path"].rsplit("/", 1)[-1] for r in moves)
        assert names == ["a.jpg", "sub"]
        assert [r for r in rows if r["action"] == "dissolve_summary"]
        assert not src.exists()

    def test_dissolve_records_carry_pre_move_hash(self, tmp_path):
        src = _make_tree(tmp_path)
        target = tmp_path / "Media" / "Photos"
        csv_path = tmp_path / "undo.csv"

        execute_moves([_dissolve_move(src, target)], [], tmp_path, csv_path,
                      tmp_path / "trash", dry_run=False)

        by_name = {r["source_path"].rsplit("/", 1)[-1]: r for r in _rows(csv_path)
                   if r["action"] == "move"}
        assert by_name["a.jpg"]["source_hash"], "file record must carry its pre-move hash"
        assert by_name["sub"]["source_hash"] == "", "dir record carries no hash"

    def test_dissolve_partial_failure_still_logs_moved_children(self, tmp_path):
        """A child that cannot move must not discard the records of those that did."""
        src = tmp_path / "dump"
        (src / "clash").mkdir(parents=True)
        (src / "clash" / "inner.txt").write_bytes(b"X")
        (src / "a.jpg").write_bytes(b"AAA")
        target = tmp_path / "Media" / "Photos"
        target.mkdir(parents=True)
        (target / "clash").write_bytes(b"FILE-NOT-DIR")  # dir onto file -> skipped
        csv_path = tmp_path / "undo.csv"

        execute_moves([_dissolve_move(src, target)], [], tmp_path, csv_path,
                      tmp_path / "trash", dry_run=False)

        moves = [r for r in _rows(csv_path) if r["action"] == "move"]
        assert [r["source_path"].rsplit("/", 1)[-1] for r in moves] == ["a.jpg"]
        assert (src / "clash" / "inner.txt").exists(), "skipped child stays put"
        assert src.exists(), "rmdir must be non-fatal so leftovers are visible"


class TestUndoAfterDissolve:
    def test_undo_after_dissolve_restores_tree(self, tmp_path):
        src = _make_tree(tmp_path)
        target = tmp_path / "Media" / "Photos"
        csv_path = tmp_path / "undo.csv"

        execute_moves([_dissolve_move(src, target)], [], tmp_path, csv_path,
                      tmp_path / "trash", dry_run=False)
        result = undo_last_run(csv_path, tmp_path, dry_run=False)

        assert result["failed"] == 0, f"undo reported failures: {result}"
        assert (src / "a.jpg").read_bytes() == b"AAA"
        assert (src / "sub" / "b.jpg").read_bytes() == b"BBB"

    def test_undo_dissolve_leaves_category_siblings_untouched(self, tmp_path):
        """THE TRAP: undo must never relocate the shared category directory."""
        target = tmp_path / "Media" / "Photos"
        (target / "other").mkdir(parents=True)
        (target / "other" / "keep.png").write_bytes(b"KEEP")
        src = tmp_path / "bilder&screens"
        src.mkdir()
        (src / "a.jpg").write_bytes(b"AAA")
        csv_path = tmp_path / "undo.csv"

        execute_moves([_dissolve_move(src, target)], [], tmp_path, csv_path,
                      tmp_path / "trash", dry_run=False)
        undo_last_run(csv_path, tmp_path, dry_run=False)

        assert target.exists(), "category dir must survive the undo"
        assert (target / "other" / "keep.png").read_bytes() == b"KEEP"
        assert (src / "a.jpg").read_bytes() == b"AAA"