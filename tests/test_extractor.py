from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from sorter.extractor import (
    can_extract_text,
    extract_batch,
    extract_content,
    get_file_summary,
)
from sorter.scanner import FileEntry


class TestCanExtractText:
    def test_pdf(self):
        assert can_extract_text("application/pdf") is True

    def test_office(self):
        assert can_extract_text("application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True
        assert can_extract_text("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") is True

    def test_text(self):
        assert can_extract_text("text/plain") is True
        assert can_extract_text("text/markdown") is True
        assert can_extract_text("text/x-python") is True
        assert can_extract_text("text/html") is True
        assert can_extract_text("text/csv") is True

    def test_media(self):
        assert can_extract_text("image/jpeg") is False
        assert can_extract_text("video/mp4") is False
        assert can_extract_text("audio/mpeg") is False

    def test_binary(self):
        assert can_extract_text("application/octet-stream") is False
        assert can_extract_text("application/zip") is False


class TestTextFileExtraction:
    def test_basic_text(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\nline 2\nline 3\n")
        result = extract_content(f, "text/plain")
        assert result["text"] == "hello world\nline 2\nline 3\n"
        assert result["metadata"]["line_count"] == 4
        assert result["error"] is None

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = extract_content(f, "text/plain")
        assert result["text"] == ""
        assert result["metadata"]["line_count"] == 1
        assert result["error"] is None

    def test_truncation(self, tmp_path: Path):
        content = "x" * 3000
        f = tmp_path / "long.txt"
        f.write_text(content)
        result = extract_content(f, "text/plain")
        assert len(result["text"]) == 2000
        assert result["text"] == "x" * 2000
        assert result["error"] is None

    def test_markdown(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("# Header\n\nSome *markdown*")
        result = extract_content(f, "text/markdown")
        assert "# Header" in result["text"]
        assert result["error"] is None

    def test_python(self, tmp_path: Path):
        f = tmp_path / "script.py"
        f.write_text("def foo():\n    pass\n")
        result = extract_content(f, "text/x-python")
        assert "def foo" in result["text"]
        assert result["metadata"]["line_count"] == 3  # "def foo():\n    pass\n" → 2 newlines → 3 lines

    def test_binary_bytes_in_text(self, tmp_path: Path):
        f = tmp_path / "binaryish.txt"
        f.write_bytes(b"hello\x00world\n")
        result = extract_content(f, "text/plain")
        assert result["error"] is None
        assert "hello" in result["text"]


class TestImageExtraction:
    def test_jpeg_exif(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (64, 128), color="red")
        img.save(img_path, format="JPEG")
        result = extract_content(img_path, "image/jpeg")
        assert result["metadata"]["width"] == 64
        assert result["metadata"]["height"] == 128
        assert result["text"] == ""
        assert result["error"] is None

    def test_png_no_exif(self, tmp_path: Path):
        img_path = tmp_path / "test.png"
        img = Image.new("RGB", (320, 240), color="blue")
        img.save(img_path, format="PNG")
        result = extract_content(img_path, "image/png")
        assert result["metadata"]["width"] == 320
        assert result["metadata"]["height"] == 240
        assert result["error"] is None

    def test_corrupt_image(self, tmp_path: Path):
        f = tmp_path / "corrupt.jpg"
        f.write_bytes(b"not a real image")
        result = extract_content(f, "image/jpeg")
        assert result["error"] is not None


class TestBinaryFallback:
    def test_unknown_mime(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = extract_content(f, "application/octet-stream")
        assert result["text"] == ""
        assert result["metadata"] == {}
        assert result["error"] is None

    def test_zip_file(self, tmp_path: Path):
        import zipfile

        f = tmp_path / "test.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("a.txt", "hello")
        result = extract_content(f, "application/zip")
        assert result["text"] == ""
        assert result["error"] is None


class TestExtractBatch:
    def test_multiple_files(self, tmp_path: Path):
        files = []
        for i in range(3):
            p = tmp_path / f"test{i}.txt"
            p.write_text(f"content {i}\n")
            files.append(FileEntry(path=p, rel_path=f"test{i}.txt", name=f"test{i}.txt", mime="text/plain", size=p.stat().st_size, mtime=p.stat().st_mtime, is_dir=False))

        results = extract_batch(files, max_workers=2)
        assert len(results) == 3
        for i, r in enumerate(results):
            assert r["text"] == f"content {i}\n"
            assert r["error"] is None

    def test_mixed_types(self, tmp_path: Path):
        txt = tmp_path / "a.txt"
        txt.write_text("hello")
        img = tmp_path / "b.png"
        Image.new("RGB", (10, 10)).save(img, format="PNG")
        bin_f = tmp_path / "c.bin"
        bin_f.write_bytes(b"\xff\xfe")

        entries = [
            FileEntry(path=txt, rel_path="a.txt", name="a.txt", mime="text/plain", size=txt.stat().st_size, mtime=txt.stat().st_mtime, is_dir=False),
            FileEntry(path=img, rel_path="b.png", name="b.png", mime="image/png", size=img.stat().st_size, mtime=img.stat().st_mtime, is_dir=False),
            FileEntry(path=bin_f, rel_path="c.bin", name="c.bin", mime="application/octet-stream", size=bin_f.stat().st_size, mtime=bin_f.stat().st_mtime, is_dir=False),
        ]

        results = extract_batch(entries)
        assert results[0]["text"] == "hello"
        assert results[1]["metadata"]["width"] == 10
        assert results[2]["text"] == ""


class TestGetFileSummary:
    def test_text_file(self):
        entry = FileEntry(path=Path("/dummy.txt"), rel_path="dummy.txt", name="dummy.txt", mime="text/plain", size=2048, mtime=0, is_dir=False)
        content = {"text": "abc\ndef\n", "metadata": {"line_count": 3}}
        s = get_file_summary(entry, content)
        assert "TEXT" in s
        assert "2.0KB" in s
        assert "3 lines" in s

    def test_image(self):
        entry = FileEntry(path=Path("/d.jpg"), rel_path="d.jpg", name="d.jpg", mime="image/jpeg", size=500000, mtime=0, is_dir=False)
        content = {"text": "", "metadata": {"width": 1920, "height": 1080, "exif": {"datetime_original": "2023:01:15 12:00:00"}}}
        s = get_file_summary(entry, content)
        assert "IMAGE" in s
        assert "1920x1080" in s
        assert "2023:01:15" in s

    def test_video(self):
        entry = FileEntry(path=Path("/v.mp4"), rel_path="v.mp4", name="v.mp4", mime="video/mp4", size=10_000_000, mtime=0, is_dir=False)
        content = {"text": "", "metadata": {"duration": 125.5, "codec": "h264", "width": 1920, "height": 1080}}
        s = get_file_summary(entry, content)
        assert "VIDEO" in s
        assert "2:05" in s
        assert "h264" in s or "1920x1080" in s