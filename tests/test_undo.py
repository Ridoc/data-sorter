"""Tests for the undo module — CSV parsing, hash verification, reverse moves."""

import csv
import pytest
from pathlib import Path

from sorter.executor import compute_file_hash, execute_moves
from sorter.undo import (
    parse_undo_log,
    verify_file_hash,
    reverse_move,
    undo_last_run,
    get_last_run_path,
)


class TestParseUndoLog:
    def test_parses_csv(self, tmp_path):
        csv_path = tmp_path / "undo.csv"
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "source_path", "target_path", "confidence",
                        "source_hash", "action", "reason"])
            w.writerow(["2026-01-01T00:00:00", "/src/a.txt", "/dst/a.txt", "95",
                        "abc123", "move", "test"])
        records = parse_undo_log(csv_path)
        assert len(records) == 1
        assert records[0]["source_path"] == "/src/a.txt"
        assert records[0]["action"] == "move"

    def test_missing_file(self, tmp_path):
        csv_path = tmp_path / "nonexistent.csv"
        records = parse_undo_log(csv_path)
        assert records == []


class TestVerifyFileHash:
    def test_matching_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h = compute_file_hash(f)
        assert verify_file_hash(f, h) is True

    def test_mismatching_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert verify_file_hash(f, "deadbeef" * 8) is False

    def test_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert verify_file_hash(f, "abc") is False


class TestReverseMove:
    def test_reverses_move(self, tmp_path):
        src = tmp_path / "original" / "file.txt"
        dst = tmp_path / "moved" / "file.txt"
        src.parent.mkdir()
        dst.parent.mkdir()
        src.write_text("content")
        import shutil
        shutil.move(str(src), str(dst))
        assert dst.exists()
        assert not src.exists()

        record = {
            "source_path": str(src),
            "target_path": str(dst),
            "action": "move",
        }
        ok = reverse_move(record, dry_run=False)
        assert ok
        assert src.exists()
        assert not dst.exists()

    def test_dry_run(self, tmp_path):
        src = tmp_path / "original" / "file.txt"
        dst = tmp_path / "moved" / "file.txt"
        dst.parent.mkdir(parents=True)
        dst.write_text("content")

        record = {
            "source_path": str(src),
            "target_path": str(dst),
            "action": "move",
        }
        ok = reverse_move(record, dry_run=True)
        assert ok
        assert dst.exists()
        assert not src.exists()

    def test_missing_target(self, tmp_path):
        record = {
            "source_path": str(tmp_path / "src.txt"),
            "target_path": str(tmp_path / "nonexistent.txt"),
            "action": "move",
        }
        ok = reverse_move(record)
        assert not ok


class TestUndoLastRun:
    def test_undo_last_run_dry_run(self, tmp_path):
        src = tmp_path / "file.txt"
        dst = tmp_path / "sub" / "file.txt"
        src.write_text("hello")
        csv_path = tmp_path / "log.csv"
        execute_moves(
            [{"source": str(src), "target": str(dst), "confidence": 95, "reason": "test"}],
            [], tmp_path, csv_path, tmp_path / "trash",
            dry_run=False,
        )
        result = undo_last_run(csv_path, tmp_path, dry_run=True)
        assert result["reverted"] == 1
        assert not src.exists()
        assert dst.exists()

    def test_undo_last_run_actual(self, tmp_path):
        src = tmp_path / "file.txt"
        dst = tmp_path / "sub" / "file.txt"
        src.write_text("hello")
        csv_path = tmp_path / "log.csv"
        execute_moves(
            [{"source": str(src), "target": str(dst), "confidence": 95, "reason": "test"}],
            [], tmp_path, csv_path, tmp_path / "trash",
            dry_run=False,
        )
        result = undo_last_run(csv_path, tmp_path, dry_run=False)
        assert result["reverted"] == 1
        assert src.exists()
        assert not dst.exists()


class TestGetLastRunPath:
    def test_finds_most_recent(self, tmp_path):
        log_dir = tmp_path / ".sort_logs"
        log_dir.mkdir()
        old = log_dir / "sort_undo_old.csv"
        new = log_dir / "sort_undo_new.csv"
        old.write_text("a")
        new.write_text("b")
        result = get_last_run_path(tmp_path)
        assert result is not None
        assert result.name == "sort_undo_new.csv"

    def test_no_log_dir(self, tmp_path):
        result = get_last_run_path(tmp_path)
        assert result is None