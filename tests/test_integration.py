"""Integration tests for the full NAS File Sorter pipeline."""
import json
import tempfile
from pathlib import Path

import pytest

from sorter.scanner import FileEntry, walk_nas
from sorter.taxonomy import load_taxonomy, get_taxonomy_yaml_string
from sorter.deduper import DedupScanner
from sorter.executor import execute_moves, dry_run_summary
from sorter.undo import undo_last_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config_path():
    return Path("config.yaml")


@pytest.fixture
def taxonomy(config_path):
    return load_taxonomy(config_path)


@pytest.fixture
def sample_files(tmp_path):
    """Create a small directory tree with test files."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "images").mkdir()
    (tmp_path / "Adobe Premiere Pro Auto-Save").mkdir()

    # Text file
    txt = tmp_path / "docs" / "readme.txt"
    txt.write_text("This is a sample readme file for testing.")

    # Image file
    from PIL import Image
    img = tmp_path / "images" / "test.png"
    Image.new('RGB', (100, 100), color='red').save(str(img))

    # Duplicate text files (large enough to bypass 1KB min-size filter)
    dup_text = "x" * 1500
    dup1 = tmp_path / "docs" / "duplicate.txt"
    dup2 = tmp_path / "images" / "duplicate.txt"
    dup1.write_text(dup_text)
    dup2.write_text(dup_text)

    # Adobe Premiere temp
    prv = tmp_path / "Adobe Premiere Pro Auto-Save" / "project.prproj"
    prv.write_text("premiere project data")

    # Loose root file
    root_file = tmp_path / "invoice.pdf"
    root_file.write_text("Invoice from vendor for services rendered")

    # Return FileEntry list
    files = walk_nas(tmp_path, [], max_depth=3)
    return [f for f in files if not f.is_dir]


def test_taxonomy_loading(config_path, taxonomy):
    """Verify taxonomy loads correctly from config."""
    assert "top_level" in taxonomy
    assert "Zeno" in taxonomy["top_level"]
    assert "Company" in taxonomy["top_level"]
    cats = taxonomy.get("categories", {})
    assert "5_Circles" in cats.get("Company", [])


def test_taxonomy_yaml_has_all_folders(config_path):
    """Verify taxonomy YAML includes all expected folders."""
    yaml_str = get_taxonomy_yaml_string(config_path)
    assert "top_level:" in yaml_str
    assert "Zeno:" in yaml_str
    assert "5_Circles" in yaml_str
    assert "Family:" in yaml_str
    assert "Projects:" in yaml_str


def test_scanner_finds_all_files(sample_files):
    """Scanner should discover all non-dir files in the test tree."""
    assert len(sample_files) == 6  # 6 files created


def test_scanner_returns_file_entries(sample_files):
    """Each file should be a proper FileEntry with required fields."""
    for f in sample_files:
        assert f.path.exists()
        assert isinstance(f.name, str)
        assert isinstance(f.size, int)
        assert isinstance(f.mtime, float)
        assert f.mime is not None


def test_scanner_mime_types(sample_files):
    """MIME types should be detected correctly."""
    mime_map = {f.name: f.mime for f in sample_files}
    assert mime_map.get("readme.txt") == "text/plain"
    assert mime_map.get("test.png") == "image/png"
    assert mime_map.get("duplicate.txt") == "text/plain"


def test_dedup_detects_exact_duplicates(sample_files):
    """Dedup should find files with identical content."""
    from sorter.deduper import DedupScanner
    scanner = DedupScanner({"enabled": True, "exact_hash": True, "image_phash": False})
    pairs = scanner.scan_exact(sample_files)
    assert len(pairs) >= 1
    # Both 'duplicate.txt' files should be detected
    dup_names = {p["delete"].name for p in pairs}
    assert "duplicate.txt" in dup_names


def test_dedup_format_for_review(sample_files):
    """Format for review should produce correct output fields."""
    from sorter.deduper import DedupScanner
    scanner = DedupScanner({"enabled": True, "exact_hash": True, "image_phash": False})
    pairs = scanner.scan_exact(sample_files)
    formatted = scanner.format_for_review(pairs)
    if formatted:
        f = formatted[0]
        assert "keep_path" in f
        assert "delete_path" in f
        assert "keep_size" in f
        assert "delete_size" in f
        assert "reason" in f


def test_executor_dry_run(tmp_path):
    """Dry run should not move any files."""
    src = tmp_path / "source" / "test.txt"
    src.parent.mkdir()
    src.write_text("hello")
    dst = tmp_path / "target" / "test.txt"

    result = execute_moves(
        [{"source": str(src), "target": str(dst), "confidence": 95, "reason": "test"}],
        [],
        tmp_path,
        tmp_path / "undo.csv",
        tmp_path / "trash",
        dry_run=True,
    )
    assert result["moved"] == 1
    assert src.exists()  # Not moved
    assert not dst.exists()


def test_executor_real_move(tmp_path):
    """Real move should move file and log to CSV."""
    src = tmp_path / "source" / "test.txt"
    src.parent.mkdir()
    src.write_text("hello")
    dst = tmp_path / "target" / "test.txt"

    result = execute_moves(
        [{"source": str(src), "target": str(dst), "confidence": 95, "reason": "test"}],
        [],
        tmp_path,
        tmp_path / "undo.csv",
        tmp_path / "trash",
        dry_run=False,
    )
    assert result["moved"] == 1
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "hello"
    # CSV should exist
    assert (tmp_path / "undo.csv").exists()


def test_undo_restores_move(tmp_path):
    """Undo should restore moved files to original location."""
    src = tmp_path / "source" / "test.txt"
    src.parent.mkdir()
    src.write_text("hello")
    dst = tmp_path / "target" / "test.txt"

    execute_moves(
        [{"source": str(src), "target": str(dst), "confidence": 95, "reason": "test"}],
        [],
        tmp_path,
        tmp_path / "undo.csv",
        tmp_path / "trash",
        dry_run=False,
    )
    assert not src.exists()
    assert dst.exists()

    result = undo_last_run(tmp_path / "undo.csv", tmp_path, dry_run=False)
    assert result["reverted"] == 1
    assert src.exists()
    assert src.read_text() == "hello"
    assert not dst.exists()


def test_pipeline_scan_to_execute_end_to_end(tmp_path):
    """End-to-end: scan → classify (mocked) → dedup → execute."""
    # Setup test files
    (tmp_path / "inbox").mkdir()
    dup_text = "x" * 1500
    txt = tmp_path / "inbox" / "test.txt"
    txt.write_text(dup_text)
    dup = tmp_path / "inbox" / "test_copy.txt"
    dup.write_text(dup_text)
    img_path = tmp_path / "inbox" / "pic.png"
    from PIL import Image
    Image.new('RGB', (10, 10), color='red').save(str(img_path))

    # 1. Scan
    files = walk_nas(tmp_path, [], max_depth=3)
    files = [f for f in files if not f.is_dir]
    assert len(files) == 3

    # 2. Mock classifications (simulating LLM output)
    classifications = [
        {"path": "inbox/test.txt", "category_path": "Zeno/Documents",
         "confidence": 92, "reason": "test document", "action": "move"},
        {"path": "inbox/test_copy.txt", "category_path": "Zeno/Documents",
         "confidence": 88, "reason": "duplicate test", "action": "move"},
        {"path": "inbox/pic.png", "category_path": "Media/Photos",
         "confidence": 95, "reason": "test image", "action": "move"},
    ]

    # 3. Dedup
    from sorter.deduper import DedupScanner
    scanner = DedupScanner({"enabled": True, "exact_hash": True, "image_phash": False})
    pairs = scanner.format_for_review(scanner.scan_all(files))
    assert len(pairs) == 1  # test.txt and test_copy.txt are duplicates

    # 4. Execute (simulating user approving all)
    approved_moves = [
        {"source": str(tmp_path / "inbox/test.txt"),
         "target": str(tmp_path / "Zeno/Documents/test.txt"),
         "confidence": 92, "reason": "test"},
        {"source": str(tmp_path / "inbox/pic.png"),
         "target": str(tmp_path / "Media/Photos/pic.png"),
         "confidence": 95, "reason": "test"},
    ]
    approved_deletions = [
        {"path": str(tmp_path / "inbox/test_copy.txt"),
         "type": "duplicate", "reason": "duplicate"},
    ]

    result = execute_moves(
        approved_moves, approved_deletions,
        tmp_path, tmp_path / "undo.csv",
        tmp_path / "trash", dry_run=False,
    )
    assert result["moved"] == 2
    assert result["deleted"] == 1

    # 5. Verify
    assert (tmp_path / "Zeno/Documents/test.txt").exists()
    assert (tmp_path / "Media/Photos/pic.png").exists()
    assert (tmp_path / "inbox/test.txt").exists() is False
    assert (tmp_path / "inbox/test_copy.txt").exists() is False

    # 6. Undo
    undo_result = undo_last_run(tmp_path / "undo.csv", tmp_path, dry_run=False)
    assert undo_result["reverted"] == 3
    assert (tmp_path / "inbox/test.txt").exists()
    assert (tmp_path / "inbox/pic.png").exists()
    assert (tmp_path / "inbox/test_copy.txt").exists()  # restored from trash


def test_config_includes_5_circles(config_path):
    """5 Circles should be in the Company category."""
    from sorter.taxonomy import load_taxonomy
    tax = load_taxonomy(config_path)
    company_cats = tax.get("categories", {}).get("Company", [])
    assert "5_Circles" in company_cats


def test_config_ollama_endpoint(config_path):
    """Config should have valid Ollama endpoint."""
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    ollama = cfg.get("ollama", {})
    assert ollama.get("endpoint") == "http://localhost:11434"
    assert ollama.get("model") == "qwen2.5-coder:7b"