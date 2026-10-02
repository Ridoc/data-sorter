"""Tests for folder-level classification."""
from pathlib import Path

import pytest

from sorter.folder_classifier import (
    identify_cohesive_folders,
    make_folder_entry,
    resolve_folder_move,
    format_folder_for_prompt,
    _is_temp_or_generated_dir,
    _dir_has_meaningful_name,
    group_files_by_directory,
    meaningful_ancestors,
)
from sorter.scanner import FileEntry


def _make_file(path, tmp_base, mime="text/plain", size=100, mtime=100):
    """Helper to create a FileEntry with proper rel_path."""
    rel = str(path.relative_to(tmp_base))
    return FileEntry(path=path, rel_path=rel, name=path.name,
                     mime=mime, size=size, mtime=mtime, is_dir=False,
                     ctime=mtime, atime=mtime)


def test_is_temp_dir():
    """Temp/generated directories should be identified."""
    assert _is_temp_or_generated_dir("audacity_temp")
    assert _is_temp_or_generated_dir("node_modules")
    assert _is_temp_or_generated_dir("Adobe Premiere Pro Auto-Save")
    assert _is_temp_or_generated_dir("__pycache__")
    assert _is_temp_or_generated_dir(".git")
    assert not _is_temp_or_generated_dir("Hypnose")
    assert not _is_temp_or_generated_dir("Bewerbungsunterlagen")
    assert not _is_temp_or_generated_dir("Documents")


def test_meaningful_dir_name():
    """User-created dirs should be identified vs auto-generated ones."""
    assert _dir_has_meaningful_name("Hypnose")
    assert _dir_has_meaningful_name("Bewerbungsunterlagen")
    assert _dir_has_meaningful_name("Training & Coaching")
    assert _dir_has_meaningful_name("VIDEO PROJEKTE")
    assert not _dir_has_meaningful_name("e00")  # hex-looking
    assert not _dir_has_meaningful_name("d00")
    assert not _dir_has_meaningful_name(".git")
    assert not _dir_has_meaningful_name(".thumbnails")


def test_group_files_by_directory():
    """Files should be grouped by their immediate parent."""
    files = [
        FileEntry(Path("/a/doc.txt"), "doc.txt", "doc.txt", "text/plain", 10, 100, False),
        FileEntry(Path("/a/other.txt"), "other.txt", "other.txt", "text/plain", 20, 200, False),
        FileEntry(Path("/b/img.png"), "img.png", "img.png", "image/png", 30, 300, False),
    ]
    groups = group_files_by_directory(files)
    assert Path("/a") in groups
    assert Path("/b") in groups
    assert len(groups[Path("/a")]) == 2
    assert len(groups[Path("/b")]) == 1


def test_identify_cohesive_folders(tmp_path):
    """User-created folders with >=2 files should be identified as cohesive."""
    # Create a cohesive folder
    hyp_dir = tmp_path / "Hypnose"
    hyp_dir.mkdir()
    files = [
        _make_file(hyp_dir / "file1.au", tmp_path, mime="audio/basic"),
        _make_file(hyp_dir / "file2.au", tmp_path, mime="audio/basic"),
        _make_file(hyp_dir / "file3.au", tmp_path, mime="audio/basic"),
        # Individual file at root (not in a cohesive folder)
        _make_file(tmp_path / "root.txt", tmp_path),
    ]
    
    cohesive, remaining = identify_cohesive_folders(files, min_files=2)
    assert len(cohesive) == 1  # Hypnose dir
    assert list(cohesive.keys())[0].name == "Hypnose"
    assert len(remaining) == 1  # root.txt


def test_temp_dir_not_cohesive(tmp_path):
    """Temp/generated directories should NOT be identified as cohesive."""
    temp_dir = tmp_path / "audacity_temp"
    temp_dir.mkdir()
    files = [
        _make_file(temp_dir / "f1.tmp", tmp_path, mime="application/octet-stream"),
        _make_file(temp_dir / "f2.tmp", tmp_path, mime="application/octet-stream"),
    ]
    
    cohesive, remaining = identify_cohesive_folders(files, min_files=2)
    assert len(cohesive) == 0
    assert len(remaining) == 2


def test_make_folder_entry(tmp_path):
    """Folder entry should summarize directory contents for the LLM."""
    dir_path = tmp_path / "TestDir"
    dir_path.mkdir()
    (dir_path / "doc1.pdf").write_text("test")
    (dir_path / "doc2.pdf").write_text("test2")
    (dir_path / "notes.txt").write_text("hello")
    
    entries = [
        _make_file(dir_path / "doc1.pdf", tmp_path, mime="application/pdf"),
        _make_file(dir_path / "doc2.pdf", tmp_path, mime="application/pdf"),
        _make_file(dir_path / "notes.txt", tmp_path, mime="text/plain"),
    ]
    
    entry = make_folder_entry(dir_path, entries, tmp_path)
    assert entry["is_folder"] is True
    assert entry["file_count"] == 3
    assert ".pdf" in entry["top_extensions"]
    assert ".txt" in entry["top_extensions"]
    assert len(entry["children"]) == 3


def test_format_folder_for_prompt():
    """Folder prompt should be readable by LLM."""
    entry = {
        "path": "Berufliches/Hypnose",
        "file_count": 15,
        "total_size": 500000,
        "top_extensions": {".au": 15},
        "sample_files": ["e00004a5.au", "e00004c1.au", "e00004c3.au"],
    }
    formatted = format_folder_for_prompt(entry)
    assert "Berufliches/Hypnose" in formatted
    assert "15" in formatted
    assert ".au" in formatted


def test_resolve_folder_move():
    """Folder classification should create a proper move action."""
    folder_entry = {
        "path": "Berufliches/Hypnose",
        "file_count": 15,
        "children": ["Berufliches/Hypnose/f1.au", "Berufliches/Hypnose/f2.au"],
    }
    classification = {
        "category_path": "Zeno/Documents/Hypnosis",
        "confidence": 90,
        "reason": "Hypnosis audio files",
    }
    nas_root = Path("/mnt/NAS-Zeno")
    
    move = resolve_folder_move(folder_entry, classification, nas_root)
    assert move is not None
    assert move["is_folder"] is True
    assert "Berufliches/Hypnose" in move["source"]
    assert "Zeno/Documents/Hypnosis" in move["target"]
    assert move["file_count"] == 15


def test_resolve_folder_move_low_confidence():
    """Low confidence folder classification should return None (scatter)."""
    folder_entry = {"path": "test", "file_count": 5}
    classification = {"category_path": "_Unsorted_Review", "confidence": 10}
    
    move = resolve_folder_move(folder_entry, classification, Path("/nas"))
    assert move is None


class TestMeaningfulAncestors:
    def test_eve_example(self):
        """Diverse Daten/EVE/... with scan_root='Diverse Daten' -> [EVE]"""
        result = meaningful_ancestors("Diverse Daten/EVE/-Y-/old_G_files/bilder&screens",
                                       "bilder&screens", scan_root="Diverse Daten")
        assert result == ["EVE"]

    def test_school_path(self):
        """Diverse Daten/Ausbildung/Schule/Projekt Müller -> [Ausbildung, Schule]"""
        result = meaningful_ancestors("Diverse Daten/Ausbildung/Schule/Projekt Müller",
                                       "Projekt Müller", scan_root="Diverse Daten")
        assert result == ["Ausbildung", "Schule"]

    def test_no_scan_root(self):
        """Without scan_root, first segment not dropped -> [EVE]"""
        result = meaningful_ancestors("EVE/-Y-/old_G_files/bilder&screens",
                                       "bilder&screens")
        assert result == ["EVE"]

    def test_no_ancestors(self):
        """Diverse Daten/Album -> [] (no meaningful ancestors between root and leaf)"""
        result = meaningful_ancestors("Diverse Daten/Album", "Album", scan_root="Diverse Daten")
        assert result == []

    def test_all_junk(self):
        """_temp/.cache/old_logs/leaf -> []"""
        result = meaningful_ancestors("Diverse Daten/_temp/.cache/old_logs/leaf",
                                       "leaf", scan_root="Diverse Daten")
        assert result == []

    def test_single_meaningful(self):
        """Berufliches/Hypnose with scan_root -> [] (root + leaf only)"""
        result = meaningful_ancestors("Berufliches/Hypnose", "Hypnose", scan_root="Berufliches")
        assert result == []

    def test_multiple_segments(self):
        """Diverse Daten/Projects/FC_Squad/Design/logos -> [Projects, FC_Squad, Design]"""
        result = meaningful_ancestors("Diverse Daten/Projects/FC_Squad/Design/logos",
                                       "logos", scan_root="Diverse Daten")
        assert result == ["Projects", "FC_Squad", "Design"]

    def test_eve_simple(self):
        """Diverse Daten/EVE/BO Logo -> [EVE]"""
        result = meaningful_ancestors("Diverse Daten/EVE/BO Logo", "BO Logo",
                                       scan_root="Diverse Daten")
        assert result == ["EVE"]

    def test_short_segments_removed(self):
        """ScanTop/a/xy/leaf -> [ScanTop] (a, xy junked, no scan_root to drop)"""
        result = meaningful_ancestors("ScanTop/a/xy/leaf", "leaf")
        assert result == ["ScanTop"]