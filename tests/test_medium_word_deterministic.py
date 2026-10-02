"""Deterministic naming for medium-word folders.

The model chooses the CATEGORY but cannot reliably flag `own_target`, so the
destination NAME is computed in code. These tests pin that contract.
"""

import pytest

from sorter.folder_classifier import derive_medium_word_leaf
from sort import _apply_medium_word_naming, _taxonomy_prefix

TAX = {"categories": {
    "Zeno": ["Documents", "Finance", "Career"],
    "Media": ["Photos", "Videos", "Music"],
    "Archives": [],
}}

NAMES = [
    ("zenos bilder", "zenos"),
    ("Eigene Bilder", "Eigene"),
    ("Realtreffenpics", "Realtreffen"),
    ("Char Bilder", "Char"),
]
@pytest.mark.parametrize("name,leaf", NAMES)
def test_semantic_core_preserved(name, leaf):
    assert derive_medium_word_leaf(name) == leaf


@pytest.mark.parametrize("name", ["bilder", "Fotos", "pics", "img", "bilder&screens"])
def test_medium_only_name_has_no_core(name):
    assert derive_medium_word_leaf(name) is None


@pytest.mark.parametrize("cat,prefix", [
    ("Media/Photos", "Media/Photos"),
    ("Media/Photos/EVE/Neues YW Forum - Grafik", "Media/Photos"),
    ("Zeno/Documents/Design/Chars", "Zeno/Documents"),
    ("Nonsense/Bogus", ""),
    ("", ""),
])
def test_taxonomy_prefix_drops_pasted_ancestry(cat, prefix):
    assert _taxonomy_prefix(cat, TAX) == prefix


class TestApplyMediumWordNaming:
    def _fc(self, cat, **kw):
        d = {"category_path": cat, "confidence": 85, "reason": "r"}
        d.update(kw)
        return d

    def test_owner_taken_from_parent(self):
        fc = self._fc("Media/Photos")
        _apply_medium_word_naming({"path": "Diverse Daten/Tina/bilder"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/Tina"
        assert fc["own_target"] is True
        assert not fc.get("dissolve")

    def test_semantic_core_beats_parent(self):
        fc = self._fc("Media/Photos")
        _apply_medium_word_naming({"path": "Diverse Daten/Eigene Bilder/zenos bilder"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/zenos"

    def test_pasted_ancestry_is_stripped(self):
        """The model answers 'Media/Photos/EVE/-Y-/old_G_files' for a pic dump."""
        fc = self._fc("Media/Photos/EVE/-Y-/old_G_files")
        _apply_medium_word_naming(
            {"path": "Diverse Daten/EVE/-Y-/old_G_files/Realtreffenpics"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/Realtreffen"

    def test_pure_medium_with_no_owner_dissolves(self):
        fc = self._fc("Media/Photos/EVE")
        _apply_medium_word_naming({"path": "Diverse Daten/Fotos"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos"   # not Media/Photos/EVE
        assert fc["dissolve"] is True
        assert fc["own_target"] is False

    def test_junk_parent_dissolves_instead_of_using_weak_ancestor(self):
        """'bilder&screens' sits in a junk container, so there is no owner to name
        it after — it must dissolve rather than become "Media/Photos/EVE"."""
        fc = self._fc("Media/Photos", dissolve=True)
        _apply_medium_word_naming(
            {"path": "Diverse Daten/EVE/-Y-/old_G_files/bilder&screens"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos"
        assert fc["dissolve"] is True

    def test_derived_name_clears_stray_dissolve(self):
        fc = self._fc("Media/Photos", dissolve=True)
        _apply_medium_word_naming({"path": "Diverse Daten/Tina/bilder"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/Tina"
        assert fc["dissolve"] is False

    def test_explicit_own_target_respected(self):
        """The model naming 'Real Life Treffen' and flagging it must win."""
        fc = self._fc("Media/Photos/Real Life Treffen", own_target=True)
        _apply_medium_word_naming(
            {"path": "Diverse Daten/EVE/-Y-/old_G_files/Realtreffenpics"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/Real Life Treffen"
        assert not fc.get("dissolve")

    def test_subjective_folders_untouched(self):
        fc = self._fc("Zeno/Documents/Design")
        _apply_medium_word_naming({"path": "Diverse Daten/EVE/BO Logo"}, fc, TAX)
        assert fc["category_path"] == "Zeno/Documents/Design"
        assert "own_target" not in fc and "dissolve" not in fc

    def test_own_target_with_bare_category_is_not_trusted(self):
        """The model set own_target but named nothing — still derive a name,
        otherwise 'Eigene Bilder' dissolves flat into Media/Photos."""
        fc = self._fc("Media/Photos", own_target=True)
        _apply_medium_word_naming({"path": "Diverse Daten/Eigene Bilder"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos/Eigene"
        assert not fc.get("dissolve")

    def test_pictures_counts_as_medium_word(self):
        fc = self._fc("Media/Photos")
        _apply_medium_word_naming({"path": "Diverse Daten/Pictures"}, fc, TAX)
        assert fc["category_path"] == "Media/Photos"
        assert fc["dissolve"] is True
