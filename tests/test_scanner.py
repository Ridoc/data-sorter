import pytest
from pathlib import Path
from sorter.scanner import FileEntry, walk_nas, get_mime, load_ignore_patterns, matches_ignore, age_string
import re


class TestAgeString:
    def test_recent(self):
        import time
        s = age_string(time.time())
        assert "seconds" in s or "hours" in s

    def test_ten_years(self):
        import time
        ten_years_ago = time.time() - (365.25 * 86400 * 10)
        s = age_string(ten_years_ago)
        assert "10.0 years" in s

    def test_unknown(self):
        assert age_string(0) == "unknown"

    def test_future(self):
        import time
        assert age_string(time.time() + 99999) == "future"

    def test_file_entry_has_time_fields(self):
        fe = FileEntry(Path("/f"), "f", "f", "text/plain", 100, 100.0, False, ctime=50.0, atime=75.0)
        assert fe.mtime == 100.0
        assert fe.ctime == 50.0
        assert fe.atime == 75.0
import re


class TestGetMime:
    def test_pdf(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_text("dummy")
        assert get_mime(f) == "application/pdf"

    def test_jpg(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_text("dummy")
        assert get_mime(f) == "image/jpeg"

    def test_jpeg(self, tmp_path):
        f = tmp_path / "pic.jpeg"
        f.write_text("dummy")
        assert get_mime(f) == "image/jpeg"

    def test_png(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_text("dummy")
        assert get_mime(f) == "image/png"

    def test_heic(self, tmp_path):
        f = tmp_path / "img.heic"
        f.write_text("dummy")
        assert get_mime(f) == "image/heic"

    def test_mp4(self, tmp_path):
        f = tmp_path / "vid.mp4"
        f.write_text("dummy")
        assert get_mime(f) == "video/mp4"

    def test_mov(self, tmp_path):
        f = tmp_path / "vid.mov"
        f.write_text("dummy")
        assert get_mime(f) == "video/quicktime"

    def test_avi(self, tmp_path):
        f = tmp_path / "vid.avi"
        f.write_text("dummy")
        assert get_mime(f) == "video/x-msvideo"

    def test_wmv(self, tmp_path):
        f = tmp_path / "vid.wmv"
        f.write_text("dummy")
        assert get_mime(f) == "video/x-ms-wmv"

    def test_mp3(self, tmp_path):
        f = tmp_path / "audio.mp3"
        f.write_text("dummy")
        assert get_mime(f) == "audio/mpeg"

    def test_wav(self, tmp_path):
        f = tmp_path / "audio.wav"
        f.write_text("dummy")
        assert get_mime(f) == "audio/wav"

    def test_flac(self, tmp_path):
        f = tmp_path / "audio.flac"
        f.write_text("dummy")
        assert get_mime(f) == "audio/flac"

    def test_txt(self, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("hello")
        assert get_mime(f) == "text/plain"

    def test_md(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# hi")
        assert get_mime(f) == "text/markdown"

    def test_yaml(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("key: val")
        assert get_mime(f) == "text/yaml"

    def test_yml(self, tmp_path):
        f = tmp_path / "cfg.yml"
        f.write_text("key: val")
        assert get_mime(f) == "text/yaml"

    def test_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("{}")
        assert get_mime(f) == "application/json"

    def test_py(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("x=1")
        assert get_mime(f) == "text/x-python"

    def test_html(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text("<html/>")
        assert get_mime(f) == "text/html"

    def test_csv(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        assert get_mime(f) == "text/csv"

    def test_vcf(self, tmp_path):
        f = tmp_path / "contacts.vcf"
        f.write_text("BEGIN:VCARD")
        assert get_mime(f) == "text/vcard"

    def test_docx(self, tmp_path):
        f = tmp_path / "report.docx"
        f.write_text("dummy")
        assert get_mime(f) == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_xlsx(self, tmp_path):
        f = tmp_path / "sheet.xlsx"
        f.write_text("dummy")
        assert get_mime(f) == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_pptx(self, tmp_path):
        f = tmp_path / "slides.pptx"
        f.write_text("dummy")
        assert get_mime(f) == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def test_doc(self, tmp_path):
        f = tmp_path / "old.doc"
        f.write_text("dummy")
        assert get_mime(f) == "application/msword"

    def test_ppt_old(self, tmp_path):
        f = tmp_path / "old.ppt"
        f.write_text("dummy")
        assert get_mime(f) == "application/vnd.ms-powerpoint"

    def test_xls_old(self, tmp_path):
        f = tmp_path / "old.xls"
        f.write_text("dummy")
        assert get_mime(f) == "application/vnd.ms-excel"

    def test_zip(self, tmp_path):
        f = tmp_path / "archive.zip"
        f.write_text("dummy")
        assert get_mime(f) == "application/zip"

    def test_aup(self, tmp_path):
        f = tmp_path / "project.aup"
        f.write_text("dummy")
        assert get_mime(f) == "application/octet-stream"

    def test_prproj(self, tmp_path):
        f = tmp_path / "project.prproj"
        f.write_text("dummy")
        assert get_mime(f) == "application/octet-stream"

    def test_unknown_extension(self, tmp_path, monkeypatch):
        import magic as magic_mod
        monkeypatch.setattr(magic_mod, 'from_file', lambda p, **kw: (_ for _ in ()).throw(Exception('mock fail')))
        f = tmp_path / "file.xyz"
        f.write_text("dummy")
        assert get_mime(f) == "application/octet-stream"

    def test_no_extension(self, tmp_path, monkeypatch):
        import magic as magic_mod
        monkeypatch.setattr(magic_mod, 'from_file', lambda p, **kw: (_ for _ in ()).throw(Exception('mock fail')))
        f = tmp_path / "README"
        f.write_text("hello")
        assert get_mime(f) == "application/octet-stream"


class TestLoadIgnorePatterns:
    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / ".sortignore"
        assert load_ignore_patterns(f) == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / ".sortignore"
        f.write_text("")
        assert load_ignore_patterns(f) == []

    def test_only_comments(self, tmp_path):
        f = tmp_path / ".sortignore"
        f.write_text("# this is a comment\n# another\n")
        assert load_ignore_patterns(f) == []

    def test_basic_patterns(self, tmp_path):
        f = tmp_path / ".sortignore"
        f.write_text("*.pyc\n__pycache__/\n")
        patterns = load_ignore_patterns(f)
        assert len(patterns) == 2

    def test_mixed_content(self, tmp_path):
        f = tmp_path / ".sortignore"
        f.write_text("# dirs\nOpenCode/\n.git/\n\n# files\n*.pyc\n.DS_Store\n")
        patterns = load_ignore_patterns(f)
        assert len(patterns) == 4


class TestMatchesIgnore:
    def setup_method(self):
        self.patterns = [re.compile(r'^\.git$'), re.compile(r'^__pycache__$')]

    def test_match_exact(self):
        assert matches_ignore(Path("/root/.git"), self.patterns)

    def test_match_dir_segment(self):
        assert matches_ignore(Path("/root/project/.git/config"), self.patterns)

    def test_no_match(self):
        assert not matches_ignore(Path("/root/src/main.py"), self.patterns)

    def test_empty_patterns(self):
        assert not matches_ignore(Path("/root/file.txt"), [])


class TestWalkNas:
    def test_basic_walk(self, tmp_path):
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("ccc")

        files = walk_nas(tmp_path, [], max_depth=None)
        assert len(files) == 3
        assert all(not e.is_dir for e in files)
        names = sorted(e.name for e in files)
        assert names == ["a.txt", "b.txt", "c.txt"]

    def test_empty_directory(self, tmp_path):
        files = walk_nas(tmp_path, [])
        assert files == []

    def test_ignore_pattern(self, tmp_path):
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")
        patterns = [re.compile(r'^.*\.pyc$')]
        files = walk_nas(tmp_path, patterns)
        assert len(files) == 1
        assert files[0].name == "keep.txt"

    def test_ignore_dir_does_not_descend(self, tmp_path):
        skip_dir = tmp_path / "OpenCode"
        skip_dir.mkdir()
        (skip_dir / "secret.txt").write_text("shh")
        (tmp_path / "visible.txt").write_text("visible")

        patterns = [re.compile(r'^OpenCode$')]
        files = walk_nas(tmp_path, patterns)
        assert len(files) == 1
        assert files[0].name == "visible.txt"

    def test_max_depth(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        subsub = sub / "subsub"
        subsub.mkdir()
        (subsub / "deep.txt").write_text("deep")
        (tmp_path / "root.txt").write_text("root")

        files = walk_nas(tmp_path, [], max_depth=1)
        assert len(files) == 1
        assert files[0].name == "root.txt"

    def test_file_entry_fields(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello")
        files = walk_nas(tmp_path, [])
        assert len(files) == 1
        e = files[0]
        assert isinstance(e.path, Path)
        assert e.name == "test.txt"
        assert e.mime == "text/plain"
        assert e.size == 5
        assert isinstance(e.mtime, float)
        assert not e.is_dir
        assert e.rel_path == "test.txt"

    def test_symlink_skipped(self, tmp_path):
        (tmp_path / "real.txt").write_text("real")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(tmp_path / "real.txt")
        except OSError:
            pytest.skip("symlink not supported on this platform")
        patterns = [re.compile(r'^link\.txt$')]
        files = walk_nas(tmp_path, patterns)
        assert len(files) == 1
        assert files[0].name == "real.txt"