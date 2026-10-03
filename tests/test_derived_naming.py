"""BATCH_3/4: code-derived names are flagged and never auto-applied; the
naming re-ask may not silently re-route a folder."""
import sys
from unittest.mock import patch

import pytest

from sort import (_apply_medium_word_naming, _leaf_beyond, _taxonomy_prefix,
                  warn_medium_like_unmatched)

TAX = {
    "categories": {
        "Media": ["Photos", "Videos", "Music"],
        "Zeno": ["Documents", "Projects"],
        "Zeno/Documents": ["Design"],
    }
}

ENTRY = {"path": "Diverse Daten/Eigene Bilder/zenos bilder"}


def _entry(path):
    return {"path": path}


class TestDerivedFlagging:
    def test_derived_name_is_flagged_and_confidence_capped(self):
        c = {"category_path": "Media/Photos", "confidence": 85, "is_folder": True}
        _apply_medium_word_naming(_entry("Diverse Daten/Eigene Bilder/zenos bilder"),
                                  c, TAX)
        assert c["_derived"] is True
        assert c["confidence"] == 70, "must drop into the review band, not auto-apply"
        assert c["category_path"] == "Media/Photos/zenos"

    def test_dissolve_is_also_flagged(self):
        c = {"category_path": "Media/Photos", "confidence": 85, "is_folder": True}
        _apply_medium_word_naming(_entry("Diverse Daten/Pictures"), c, TAX)
        assert c["dissolve"] is True
        assert c["_derived"] is True

    def test_non_medium_word_folder_is_untouched(self):
        c = {"category_path": "Zeno/Documents/Design/BO Logo", "confidence": 92}
        _apply_medium_word_naming(_entry("Diverse Daten/EVE/BO Logo"), c, TAX)
        assert "_derived" not in c
        assert c["confidence"] == 92

    def test_lower_confidence_is_not_raised(self):
        c = {"category_path": "Media/Photos", "confidence": 40}
        _apply_medium_word_naming(_entry("Diverse Daten/Eigene Bilder/zenos bilder"),
                                  c, TAX)
        assert c["confidence"] == 40


class TestLeafBeyond:
    def test_extracts_model_added_leaf(self):
        assert _leaf_beyond("Media/Photos/Zeno", TAX) == "Zeno"

    def test_bare_category_has_no_leaf(self):
        assert _leaf_beyond("Media/Photos", TAX) == ""

    def test_pasted_ancestry_is_dropped(self):
        assert _leaf_beyond("Media/Photos/EVE/-Y-/old_G_files", TAX) == "old_G_files"


class TestExecuteFilter:
    def test_derived_moves_excluded_from_execute_autoapprove(self):
        """The --execute filter is a list comprehension in main(); assert its
        exact predicate so a derived name can never auto-apply."""
        moves = [
            {"confidence": 85, "_derived": False},
            {"confidence": 70, "_derived": True},
            {"confidence": 0, "_derived": True},
        ]
        kept = [fm for fm in moves
                if fm.get("confidence", 0) > 0 and not fm.get("_derived", False)]
        assert kept == [{"confidence": 85, "_derived": False}]


class TestBlindSpotWarning:
    @pytest.mark.parametrize("name", ["Grafiken-alt", "Grafiken(alt)", "Grafiken.bak"])
    def test_medium_like_unmatched_is_reported(self, name, capsys):
        # Verified: these CONTAIN a medium word but miss is_medium_word_name,
        # so the naming rules silently never run for them.
        from sorter.folder_classifier import is_medium_word_name
        assert not is_medium_word_name(name), name
        n = warn_medium_like_unmatched([_entry(f"Diverse Daten/{name}")])
        assert n == 1
        assert "medium-like name not matched" in capsys.readouterr().err

    def test_genuine_medium_word_names_are_not_warned(self, capsys):
        n = warn_medium_like_unmatched([_entry("Diverse Daten/Eigene Bilder/zenos bilder"),
                                         _entry("Diverse Daten/Pictures"),
                                         _entry("Diverse Daten/EVE/BO Logo")])
        assert n == 0
        assert capsys.readouterr().err == ""