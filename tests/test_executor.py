"""Tests for the executor module — file moves, trash, hashing, CSV logging."""

import csv
import pytest
from pathlib import Path

from sorter.executor import (
    ensure_dir,
    move_file,
    move_to_trash,
    compute_file_hash,
    log_move,
    execute_moves,
    dry_run_summary,
)


class TestEnsureDir:
    def test_creates_directory(self, tmp_path):
        d = tmp_path / "a" / "b" / "c"
        ensure_dir(d)
        assert d.exists()

    def test_existing_directory(self, tmp_path):
        ensure_dir(tmp_path)
        assert tmp_path.exists()


class TestMoveFile:
    def test_moves_file(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "sub" / "dst.txt"
        src.write_text("hello")
        ok = move_file(src, dst)
        assert ok
        assert dst.exists()
        assert not src.exists()

    def test_dry_run_does_not_move(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        ok = move_file(src, dst, dry_run=True)
        assert ok
        assert not dst.exists()
        assert src.exists()

    def test_missing_source(self, tmp_path):
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "dst.txt"
        ok = move_file(src, dst)
        assert not ok

    def test_target_exists_renames(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        dst.write_text("existing")
        ok = move_file(src, dst)
        assert ok
        assert not src.exists()
        assert (tmp_path / "dst_1.txt").exists()


class TestMoveToTrash:
    def test_moves_to_trash(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("content")
        trash_root = tmp_path / "trash"
        result = move_to_trash(src, trash_root)
        assert result is not None
        assert result.exists()
        assert "_Duplicates_Delete" in str(result)
        assert not src.exists()

    def test_dry_run(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("content")
        trash_root = tmp_path / "trash"
        result = move_to_trash(src, trash_root, dry_run=True)
        assert result is not None
        assert src.exists()

    def test_missing_source(self, tmp_path):
        src = tmp_path / "nonexistent.txt"
        trash_root = tmp_path / "trash"
        result = move_to_trash(src, trash_root)
        assert result is None


class TestComputeFileHash:
    def test_consistent_hash(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world" * 1000)
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2

    def test_different_content(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"content a")
        b.write_bytes(b"content b")
        assert compute_file_hash(a) != compute_file_hash(b)


class TestLogMove:
    def test_writes_csv(self, tmp_path):
        csv_path = tmp_path / "log.csv"
        log_move(csv_path, {
            "source_path": "/src/file.txt",
            "target_path": "/dst/file.txt",
            "confidence": "95",
            "source_hash": "abc123",
            "action": "move",
            "reason": "test",
        })
        assert csv_path.exists()
        with open(csv_path, 'r') as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert "source_path" in lines[0]

    def test_appends_to_existing(self, tmp_path):
        csv_path = tmp_path / "log.csv"
        log_move(csv_path, {"source_path": "/a", "target_path": "/b", "confidence": "90",
                            "source_hash": "h1", "action": "move", "reason": "r1"})
        log_move(csv_path, {"source_path": "/c", "target_path": "/d", "confidence": "80",
                            "source_hash": "h2", "action": "delete", "reason": "r2"})
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2


class TestExecuteMoves:
    def test_execute_moves_dry_run(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        moves = [{"source": str(src), "target": str(tmp_path / "sub" / "file.txt"),
                  "confidence": 95, "reason": "test"}]
        csv_path = tmp_path / ".sort_logs" / "undo.csv"
        result = execute_moves(moves, [], tmp_path, csv_path, tmp_path / "trash", dry_run=True)
        assert result["moved"] == 1
        assert result["deleted"] == 0
        assert src.exists()
        assert not (tmp_path / "sub" / "file.txt").exists()

    def test_execute_moves_actual(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        moves = [{"source": str(src), "target": str(tmp_path / "sub" / "file.txt"),
                  "confidence": 95, "reason": "test"}]
        csv_path = tmp_path / ".sort_logs" / "undo.csv"
        result = execute_moves(moves, [], tmp_path, csv_path, tmp_path / "trash", dry_run=False)
        assert result["moved"] == 1
        assert not src.exists()
        assert (tmp_path / "sub" / "file.txt").exists()
        assert csv_path.exists()

    def test_execute_deletions(self, tmp_path):
        f = tmp_path / "delete_me.txt"
        f.write_text("adobe temp")
        deletions = [{"path": str(f), "type": "adobe_temp", "reason": "Premiere temp"}]
        csv_path = tmp_path / ".sort_logs" / "undo.csv"
        result = execute_moves([], deletions, tmp_path, csv_path, tmp_path / "trash", dry_run=False)
        assert result["deleted"] == 1
        assert not f.exists()


class TestDryRunSummary:
    def test_summary_counts(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello world")
        moves = [{"source": str(src), "target": str(tmp_path / "sub" / "file.txt"),
                  "confidence": 95, "reason": "test"}]
        deletions = [{"path": str(tmp_path / "nonexistent.txt"), "type": "temp", "reason": "temp"}]
        summary = dry_run_summary(moves, deletions)
        assert summary["to_move"] == 1
        assert summary["to_delete"] == 1
        assert summary["space_freed_estimate"] > 0