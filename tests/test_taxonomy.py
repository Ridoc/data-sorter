import pytest
from pathlib import Path
from sorter.taxonomy import (
    is_adobe_premiere_temp,
    load_taxonomy,
    taxonomy_to_yaml,
    get_taxonomy_yaml_string,
    validate_category_path,
    resolve_category,
)


SAMPLE_TAXONOMY = {
    "top_level": ["Zeno", "Family", "Company", "Projects", "Media", "Archives", "_Unsorted_Review"],
    "categories": {
        "Zeno": ["Documents", "Downloads", "Bilder", "Videos"],
        "Family": ["Mom", "Andreas"],
        "Company": ["5_Circles"],
        "Projects": ["ERGO_Paphos", "Websites"],
        "Media": ["Music", "Photos", "Videos"],
        "Archives": ["Old_Projects"],
    },
}


class TestIsAdobePremiereTemp:
    def test_auto_save_in_path(self):
        assert is_adobe_premiere_temp("videos/Adobe Premiere Pro Auto-Save/crash.prproj")

    def test_preview_files_in_path(self):
        assert is_adobe_premiere_temp("Adobe Premiere Pro Preview Files/something.mp4")

    def test_conformed_audio(self):
        assert is_adobe_premiere_temp("Conformed Audio Files/audio.wav")

    def test_prv_extension(self):
        assert is_adobe_premiere_temp("preview.PRV")
        assert is_adobe_premiere_temp("sub/preview.prv")

    def test_auto_save_subdir(self):
        assert is_adobe_premiere_temp("project/Adobe Premiere Pro Auto-Save/backup.prproj")

    def test_normal_file(self):
        assert not is_adobe_premiere_temp("project/project_file.prproj")

    def test_normal_document(self):
        assert not is_adobe_premiere_temp("Documents/invoice.pdf")

    def test_empty_string(self):
        assert not is_adobe_premiere_temp("")


class TestValidateCategoryPath:
    def test_valid_top_level(self):
        assert validate_category_path("Zeno/Documents", SAMPLE_TAXONOMY) is True

    def test_valid_no_subfolder(self):
        assert validate_category_path("Archives", SAMPLE_TAXONOMY) is True

    def test_invalid_top_level(self):
        assert validate_category_path("Invalid/Documents", SAMPLE_TAXONOMY) is False

    def test_empty_path(self):
        assert validate_category_path("", SAMPLE_TAXONOMY) is False

    def test_none_path(self):
        assert validate_category_path(None, SAMPLE_TAXONOMY) is False

    def test_unsorted_review(self):
        assert validate_category_path("_Unsorted_Review", SAMPLE_TAXONOMY) is True


class TestTaxonomyToYaml:
    def test_basic_conversion(self):
        result = taxonomy_to_yaml(SAMPLE_TAXONOMY)
        assert "top_level:" in result
        assert "  - Zeno" in result
        assert "categories:" in result
        assert "  Zeno:" in result
        assert "    - Documents" in result

    def test_empty_categories(self):
        tax = {"top_level": ["Zeno"], "categories": {"Zeno": []}}
        result = taxonomy_to_yaml(tax)
        assert "Zeno: []" in result


class TestResolveCategory:
    def test_valid_path_returns_move(self):
        result = resolve_category(
            {"path": "doc.pdf", "category_path": "Zeno/Documents", "confidence": 90, "reason": "test"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "move"
        assert result.get("is_new", False) is False

    def test_delete_path_returns_delete(self):
        result = resolve_category(
            {"path": "temp.prv", "category_path": "DELETE", "confidence": 100, "reason": "temp"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "delete"

    def test_new_subfolder_flagged_is_new(self):
        result = resolve_category(
            {"path": "doc.pdf", "category_path": "Zeno/New_Folder", "confidence": 70, "reason": "new"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "move"
        assert result.get("is_new") is True

    def test_case_normalized_media_child(self):
        """Media/videos (lowercase) → Media/Videos (canonical), not is_new."""
        result = resolve_category(
            {"path": "clip.mp4", "category_path": "Media/videos", "confidence": 90, "reason": "test"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "move"
        assert result.get("is_new", False) is False
        assert result["category_path"] == "Media/Videos"

    def test_case_normalized_unknown_child_stays_new(self):
        """Case-insensitive match fails → still is_new for genuinely new subfolder."""
        result = resolve_category(
            {"path": "doc.pdf", "category_path": "Media/Whatever", "confidence": 70, "reason": "new"},
            SAMPLE_TAXONOMY,
        )
        assert result.get("is_new") is True

    def test_invalid_top_level_returns_review(self):
        result = resolve_category(
            {"path": "doc.pdf", "category_path": "Invalid/Stuff", "confidence": 50, "reason": "bad"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "review"

    def test_review_top_level_in_unsorted(self):
        result = resolve_category(
            {"path": "doc.pdf", "category_path": "_Unsorted_Review", "confidence": 30, "reason": "unknown"},
            SAMPLE_TAXONOMY,
        )
        assert result["action"] == "move"

    def test_preserves_original_fields(self):
        result = resolve_category(
            {"path": "f.txt", "category_path": "Family/Mom", "confidence": 95, "reason": "mom"},
            SAMPLE_TAXONOMY,
        )
        assert result["path"] == "f.txt"
        assert result["confidence"] == 95
        assert result["reason"] == "mom"


class TestLoadTaxonomy:
    def test_load_from_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("taxonomy:\n  top_level:\n    - Zeno\n  categories:\n    Zeno:\n      - Documents\n")
        tax = load_taxonomy(cfg)
        assert tax["top_level"] == ["Zeno"]
        assert tax["categories"]["Zeno"] == ["Documents"]

    def test_get_taxonomy_yaml_string(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("taxonomy:\n  top_level:\n    - Zeno\n  categories:\n    Zeno:\n      - Documents\n")
        result = get_taxonomy_yaml_string(cfg)
        assert "Zeno" in result
        assert "Documents" in result


class TestDesignInTaxonomy:
    def test_design_in_zeno_children(self):
        """Design should be a known child of Zeno (content-understanding plan)."""
        tax = load_taxonomy(Path("config.yaml"))
        zeno_children = tax.get("categories", {}).get("Zeno", [])
        assert "Design" in zeno_children, "Design not in Zeno children"

    def test_design_in_taxonomy_yaml_string(self):
        """Taxonomy YAML string should contain Design."""
        yaml_str = get_taxonomy_yaml_string(Path("config.yaml"))
        assert "Design" in yaml_str, "Design missing from taxonomy YAML"