"""Tests for the deduper module — SHA-256 dedup and image perceptual dedup."""

import hashlib
import pytest
from pathlib import Path
from PIL import Image, ImageDraw

from sorter.deduper import DedupScanner
from sorter.scanner import FileEntry


def make_entry(rel_path: str, mime: str = "text/plain", size: int = 1024,
               mtime: float = 1000.0) -> FileEntry:
    return FileEntry(
        path=Path(f"/mnt/test/{rel_path}"),
        rel_path=rel_path,
        name=Path(rel_path).name,
        mime=mime,
        size=size,
        mtime=mtime,
        is_dir=False,
        ctime=mtime,
        atime=mtime,
    )


DEFAULT_CFG = {
    'enabled': True,
    'exact_hash': True,
    'image_phash': True,
    'keep_newest': True,
    'trash_dir': '_Duplicates_Delete',
}


class TestSHA256:
    def test_sha256_same_content(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("hello world")
        b.write_text("hello world")
        scanner = DedupScanner(DEFAULT_CFG)
        assert scanner._sha256(a) == scanner._sha256(b)

    def test_sha256_different(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("hello world")
        b.write_text("goodbye world")
        scanner = DedupScanner(DEFAULT_CFG)
        assert scanner._sha256(a) != scanner._sha256(b)


class TestScanExact:
    def test_no_dupes(self, tmp_path):
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        entries = [
            make_entry("a.txt", size=1024),
            make_entry("b.txt", size=1024),
        ]
        entries[0] = entries[0]._replace(path=tmp_path / "a.txt")
        entries[1] = entries[1]._replace(path=tmp_path / "b.txt")
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_exact(entries)
        assert len(results) == 0

    def test_finds_dupes(self, tmp_path):
        content = b"hello world" * 100
        (tmp_path / "a.txt").write_bytes(content)
        (tmp_path / "b.txt").write_bytes(content)
        entries = [
            make_entry("a.txt", size=len(content)),
            make_entry("b.txt", size=len(content)),
        ]
        entries[0] = entries[0]._replace(path=tmp_path / "a.txt")
        entries[1] = entries[1]._replace(path=tmp_path / "b.txt")
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_exact(entries)
        assert len(results) == 1
        assert results[0]["type"] == "exact"
        assert results[0]["delete"].rel_path in ("a.txt", "b.txt")

    def test_keeps_newest(self, tmp_path):
        content = b"same content" * 100
        old_f = tmp_path / "old.txt"
        new_f = tmp_path / "new.txt"
        old_f.write_bytes(content)
        new_f.write_bytes(content)
        old_mtime = 1000.0
        new_mtime = 2000.0
        os_mod = __import__("os")
        os_mod.utime(str(old_f), (old_mtime, old_mtime))
        os_mod.utime(str(new_f), (new_mtime, new_mtime))
        entries = [
            make_entry("old.txt", size=len(content), mtime=old_mtime),
            make_entry("new.txt", size=len(content), mtime=new_mtime),
        ]
        entries[0] = entries[0]._replace(path=old_f)
        entries[1] = entries[1]._replace(path=new_f)
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_exact(entries)
        assert len(results) == 1
        assert results[0]["keep"].rel_path == "new.txt"
        assert results[0]["delete"].rel_path == "old.txt"

    def test_skip_small_files(self, tmp_path):
        small = tmp_path / "small.txt"
        large = tmp_path / "large.txt"
        content = b"tiny"
        small.write_text("tiny")
        large.write_text("tiny")
        entries = [
            make_entry("small.txt", size=4),
            make_entry("large.txt", size=1024),
        ]
        entries[0] = entries[0]._replace(path=small)
        entries[1] = entries[1]._replace(path=large)
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_exact(entries)
        assert len(results) == 0


import random

class TestImageNearDups:
    def test_image_near_dup(self, tmp_path):
        img_a = tmp_path / "a.jpg"
        img_b = tmp_path / "b.jpg"
        img_base = Image.new('RGB', (256, 256), 'white')
        draw = ImageDraw.Draw(img_base)
        random.seed(42)
        for _ in range(50):
            x1 = random.randint(0, 200)
            y1 = random.randint(0, 200)
            x2 = x1 + random.randint(10, 55)
            y2 = y1 + random.randint(10, 55)
            color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            draw.rectangle([x1, y1, x2, y2], fill=color, outline=(0, 0, 0))
        img_base.save(str(img_a), quality=90)
        img2 = Image.open(img_a).copy()
        img2.save(str(img_b), quality=20)

        entries = [
            make_entry("a.jpg", mime="image/jpeg", size=10000),
            make_entry("b.jpg", mime="image/jpeg", size=8000),
        ]
        entries[0] = entries[0]._replace(path=img_a)
        entries[1] = entries[1]._replace(path=img_b)
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_image_near_duplicates(entries)
        assert len(results) == 1
        assert results[0]["type"] == "near_image"
        assert "hamming" in results[0]


class TestScanAll:
    def test_scan_all_dedup(self, tmp_path):
        content = b"exact dup content " * 100
        f1 = tmp_path / "exact1.txt"
        f2 = tmp_path / "exact2.txt"
        f1.write_bytes(content)
        f2.write_bytes(content)
        entries = [
            make_entry("exact1.txt", size=len(content), mtime=1000.0),
            make_entry("exact2.txt", size=len(content), mtime=2000.0),
        ]
        entries[0] = entries[0]._replace(path=f1)
        entries[1] = entries[1]._replace(path=f2)
        scanner = DedupScanner(DEFAULT_CFG)
        results = scanner.scan_all(entries)
        assert len(results) == 1
        assert results[0]["type"] == "exact"


class TestFormatForReview:
    def test_format_for_review(self, tmp_path):
        f1 = tmp_path / "keep.txt"
        f2 = tmp_path / "delete.txt"
        f1.write_text("x" * 2048)
        f2.write_text("x" * 2048)
        entries = [
            make_entry("keep.txt", size=2048, mtime=2000.0),
            make_entry("delete.txt", size=2048, mtime=1000.0),
        ]
        entries[0] = entries[0]._replace(path=f1)
        entries[1] = entries[1]._replace(path=f2)
        scanner = DedupScanner(DEFAULT_CFG)
        pairs = scanner.scan_exact(entries)
        formatted = scanner.format_for_review(pairs)
        assert len(formatted) == 1
        f = formatted[0]
        assert f["keep_path"] == "keep.txt"
        assert f["delete_path"] == "delete.txt"
        assert "KB" in f["keep_size"]
        assert "KB" in f["delete_size"]
        assert "T" in f["keep_date"]
        assert "T" in f["delete_date"]
        assert f["type"] == "exact"