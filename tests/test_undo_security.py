"""Regression tests: the undo CSV is UNTRUSTED INPUT.

The undo log lives at a fixed, append-only path (`nas_root/.sort_logs/
sort_undo.csv`, sort.py:33). Anyone or anything able to write there can append a
row, and --undo executes it with the user's privileges. These tests pin the
containment and hash-integrity rules that make a crafted row inert.
"""

import csv
from pathlib import Path

from sorter.executor import compute_file_hash, execute_moves
from sorter.undo import undo_last_run, _within_root


CSV_HEADER = ["timestamp", "source_path", "target_path", "confidence",
              "source_hash", "action", "reason", "original_name"]


def _write_csv(csv_path, rows):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for r in rows:
            w.writerow([r.get(k, "") for k in CSV_HEADER])


class TestRootContainment:
    def test_crafted_row_cannot_move_file_outside_nas_root(self, tmp_path):
        """The proven CRITICAL: a planted row relocated a file beyond the root."""
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        victim = nas / "Media" / "photo.jpg"
        victim.write_bytes(b"SECRET")

        outside = tmp_path / "outside" / "stolen.jpg"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"PLANTED")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(tmp_path / "escape.jpg"),   # absent, outside the root
            "target_path": str(outside),
            "action": "move",
            "source_hash": "",
            "reason": "crafted",
        }])

        result = undo_last_run(csv_path, nas, dry_run=False)

        assert result["failed"] == 0, f"must be refused, not attempted: {result}"
        assert result["skipped"] == 1
        assert outside.exists(), "planted file must not be moved"
        assert not (tmp_path / "escape.jpg").exists(), "must not create outside-root target"

    def test_relative_traversal_row_is_refused(self, tmp_path):
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        inside = nas / "Media" / "a.jpg"
        inside.write_bytes(b"A")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(nas / ".." / ".." / "escaped.jpg"),
            "target_path": str(inside),
            "action": "move",
            "source_hash": "deadbeef",
        }])

        result = undo_last_run(csv_path, nas, dry_run=False)

        assert result["skipped"] == 1
        assert inside.exists(), "in-root target must be left alone"

    def test_symlink_pointing_out_of_root_is_refused(self, tmp_path):
        """Resolution, not string prefix — a symlink must not launder the check."""
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "secret.jpg").write_bytes(b"S")
        (nas / "Media" / "link").symlink_to(outside_dir)

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(nas / "restored.jpg"),
            "target_path": str(nas / "Media" / "link" / "secret.jpg"),
            "action": "move",
            # A VALID hash on purpose: it must not be the empty-hash rule that
            # saves us here, otherwise the test would pass for the wrong reason.
            "source_hash": compute_file_hash(outside_dir / "secret.jpg"),
        }])

        result = undo_last_run(csv_path, nas, dry_run=False)

        assert result["skipped"] == 1
        assert (outside_dir / "secret.jpg").exists(), "symlinked escape must be refused"
        assert not (nas / "restored.jpg").exists()

    def test_folder_move_is_reversible_per_file(self, tmp_path):
        """A whole-folder move must emit one undo row PER FILE.

        A single aggregate row is unrecoverable: reverse_move cannot tell whether the
        destination was created by this run or already held the user's data, so
        moving it back would relocate the whole category.
        """
        nas = tmp_path / "nas"
        src = nas / "src" / "Katzen"
        (src / "deep").mkdir(parents=True)
        (src / "a.jpg").write_bytes(b"A")
        (src / "deep" / "b.jpg").write_bytes(b"B")
        target = nas / "Media" / "Photos" / "Katzen"
        csv_path = nas / ".sort_logs" / "sort_undo.csv"

        execute_moves([{"source": str(src), "target": str(target), "is_folder": True}],
                      [], nas, csv_path, tmp_path / "_Trash", dry_run=False)

        rows = list(csv.DictReader(open(csv_path)))
        move_rows = [r for r in rows if r["action"] == "move"]
        assert len(move_rows) == 2, rows
        assert all(r["source_hash"] for r in move_rows), "each row needs a pre-move hash"

        result = undo_last_run(csv_path, nas, dry_run=False)
        assert result["reverted"] == 2, result
        assert (src / "a.jpg").read_bytes() == b"A"
        assert (src / "deep" / "b.jpg").read_bytes() == b"B"

    def test_within_root_accepts_real_moves(self, tmp_path):
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        assert _within_root(nas / "Media" / "a.jpg", nas)
        assert not _within_root(tmp_path / "elsewhere.jpg", nas)


    def test_source_path_equal_to_root_cannot_bump_outside_root(self, tmp_path):
        """shutil_move resolves collisions by appending _N in the DESTINATION's parent.

        A row whose source_path IS the root therefore landed at <root>_1 — one level
        outside the NAS root — while passing every other containment check.
        """
        nas = tmp_path / "nas"
        (nas / "Media" / "Photos").mkdir(parents=True)
        (nas / "Media" / "Photos" / "p.jpg").write_bytes(b"P")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(nas),
            "target_path": str(nas / "Media" / "Photos"),
            "action": "move",
            "source_hash": "",          # dirs legitimately carry no hash
        }])

        undo_last_run(csv_path, nas, dry_run=False)

        assert not Path(str(nas) + "_1").exists(), "must not create <root>_1 outside root"
        assert (nas / "Media" / "Photos" / "p.jpg").exists()

    def test_empty_source_path_does_not_abort_the_whole_run(self, tmp_path):
        """A crafted empty/'.' source made shutil_move raise ValueError, killing
        every subsequent row in the run. One bad row must not strand the rest."""
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        good_target = nas / "Media" / "legit.jpg"
        good_target.write_bytes(b"L")
        bad_target = nas / "Media" / "victim.jpg"
        bad_target.write_bytes(b"V")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [
            {"source_path": "", "target_path": str(bad_target),
             "action": "delete", "source_hash": ""},
            {"source_path": str(nas / "orig.jpg"), "target_path": str(good_target),
             "action": "move", "source_hash": compute_file_hash(good_target)},
        ])

        result = undo_last_run(csv_path, nas, dry_run=False)   # must not raise

        assert result["skipped"] == 1, result
        assert result["reverted"] == 1, f"good row after a bad one must still revert: {result}"
        assert (nas / "orig.jpg").exists()


class TestHashIntegrity:
    def test_file_restore_without_hash_is_refused(self, tmp_path):
        nas = tmp_path / "nas"
        (nas / "Media").mkdir(parents=True)
        moved = nas / "Media" / "a.jpg"
        moved.write_bytes(b"A")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(nas / "orig.jpg"),
            "target_path": str(moved),
            "action": "move",
            "source_hash": "",   # empty = nothing to verify against
        }])

        result = undo_last_run(csv_path, nas, dry_run=False)

        assert result["skipped"] == 1
        assert moved.exists(), "unverifiable file must not be restored"

    def test_directory_restore_without_hash_is_allowed(self, tmp_path):
        """Dirs legitimately carry an empty hash — they move as units."""
        nas = tmp_path / "nas"
        (nas / "Media" / "Photos").mkdir(parents=True)
        (nas / "Media" / "Photos" / "x.jpg").write_bytes(b"A")

        csv_path = nas / ".sort_logs" / "sort_undo.csv"
        _write_csv(csv_path, [{
            "source_path": str(nas / "bilder&screens"),
            "target_path": str(nas / "Media" / "Photos"),
            "action": "move",
            "source_hash": "",
        }])

        result = undo_last_run(csv_path, nas, dry_run=False)

        assert result["failed"] == 0, result
        assert (nas / "bilder&screens" / "x.jpg").exists()