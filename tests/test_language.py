"""Tests for language detection and mismatch guard."""
import pytest

from sorter.language import detect_language, leaf_language_mismatch


class TestDetectLanguage:
    """Tests for detect_language() — charset + word heuristics."""

    # --- German detection (umlauts) ---

    def test_german_umlaut_ae(self):
        assert detect_language("Änderungen") == "de"

    def test_german_umlaut_oe(self):
        assert detect_language("Öffnungszeiten") == "de"

    def test_german_umlaut_ue(self):
        assert detect_language("Überweisung") == "de"

    def test_german_umlaut_ss(self):
        assert detect_language("Straße") == "de"

    def test_german_umlaut_mixed(self):
        assert detect_language("Entwürfe Logo") == "de"

    def test_german_umlaut_lowercase(self):
        assert detect_language("änderungen") == "de"

    # --- German detection (ASCII words) ---

    def test_german_word_haus(self):
        assert detect_language("Haus in Zypern") == "de"

    def test_german_word_wohnung(self):
        assert detect_language("Wohnung") == "de"

    def test_german_word_ausbildung(self):
        assert detect_language("Ausbildung") == "de"

    def test_german_word_schule(self):
        assert detect_language("Schule") == "de"

    def test_german_word_bewerbung(self):
        assert detect_language("BEWERBUNG") == "de"

    def test_german_word_fotos(self):
        assert detect_language("Fotos") == "de"

    def test_german_word_bilder(self):
        assert detect_language("Bilder") == "de"

    def test_german_word_eigene(self):
        assert detect_language("Eigene Videos") == "de"

    def test_german_word_projekt(self):
        assert detect_language("Projekt Müller") == "de"

    def test_german_word_notizen(self):
        assert detect_language("Notizen") == "de"

    def test_german_word_unterlagen(self):
        assert detect_language("Unterlagen") == "de"

    # --- German suffix detection ---

    def test_german_suffix_ung(self):
        assert detect_language("Rechnung") == "de"

    def test_german_suffix_heit(self):
        assert detect_language("Gesundheit") == "de"

    def test_german_suffix_keit(self):
        assert detect_language("Freundlichkeit") == "de"

    def test_german_suffix_schaft(self):
        assert detect_language("Mannschaft") == "de"

    # --- English detection ---

    def test_english_design(self):
        assert detect_language("Design") == "en"

    def test_english_photos(self):
        assert detect_language("Photos") == "en"

    def test_english_documents(self):
        assert detect_language("Documents") == "en"

    def test_english_sunset_villa(self):
        assert detect_language("Sunset Villa") == "en"

    def test_english_project(self):
        assert detect_language("Project") == "en"

    def test_english_video(self):
        assert detect_language("Video") == "en"

    def test_english_music(self):
        assert detect_language("Music") == "en"

    def test_english_archives(self):
        assert detect_language("Archives") == "en"

    def test_english_family(self):
        assert detect_language("Family") == "en"

    def test_english_company(self):
        assert detect_language("Company") == "en"

    # --- Proper names / mixed ---

    def test_proper_name_eve(self):
        assert detect_language("EVE") == "en"

    def test_proper_name_bo_logo(self):
        assert detect_language("BO Logo") == "en"

    def test_proper_name_villa_cyprus(self):
        assert detect_language("Sunset Villa") == "en"

    def test_empty_name(self):
        assert detect_language("") == "en"

    # --- Greek detection ---

    def test_greek(self):
        assert detect_language("Ελληνικά") == "el"

    def test_greek_photos(self):
        assert detect_language("Φωτογραφίες") == "el"


class TestLeafLanguageMismatch:
    """Tests for leaf_language_mismatch() — detects anglicized leaves."""

    def test_en_vs_de_design_haus(self):
        """English 'Design' vs German 'Haus in Zypern' -> mismatch."""
        assert leaf_language_mismatch("Design", "Haus in Zypern") is True

    def test_en_vs_de_photos_fotos(self):
        """English 'Photos' vs German 'Fotos' -> mismatch."""
        assert leaf_language_mismatch("Photos", "Fotos") is True

    def test_en_vs_de_documents_dokumente(self):
        """English 'Documents' vs German 'Dokumente' -> mismatch."""
        assert leaf_language_mismatch("Documents", "Dokumente") is True

    def test_en_vs_de_design_wohnung(self):
        """English 'Design' vs German 'Wohnung' -> mismatch."""
        assert leaf_language_mismatch("Design", "Wohnung") is True

    def test_en_vs_de_videos_eigene(self):
        """English 'Videos' vs German 'Eigene Videos' -> mismatch."""
        assert leaf_language_mismatch("Videos", "Eigene Videos") is True

    # --- Same language — no mismatch ---

    def test_same_name_schule(self):
        """Same name -> no mismatch."""
        assert leaf_language_mismatch("Schule", "Schule") is False

    def test_both_de_entwuerfe_logo(self):
        """Both German 'Entwürfe Logo' vs 'Logo' -> no mismatch."""
        assert leaf_language_mismatch("Entwürfe Logo", "Logo") is False

    def test_both_en_sunset_villa(self):
        """Both English -> no mismatch."""
        assert leaf_language_mismatch("Sunset Villa", "House") is False

    def test_both_en_documents(self):
        """Both English -> no mismatch."""
        assert leaf_language_mismatch("Documents", "Contracts") is False

    # --- Edge cases ---

    def test_empty_leaf(self):
        assert leaf_language_mismatch("", "Haus") is False

    def test_empty_source(self):
        assert leaf_language_mismatch("Design", "") is False

    def test_both_empty(self):
        assert leaf_language_mismatch("", "") is False

    def test_none_names(self):
        assert leaf_language_mismatch(None, "Haus") is False  # type: ignore

    # --- Greek vs English ---

    def test_en_vs_el(self):
        """English 'Documents' vs Greek 'Ελληνικά' -> mismatch."""
        assert leaf_language_mismatch("Documents", "Ελληνικά") is True

    def test_el_vs_el(self):
        """Both Greek -> no mismatch."""
        assert leaf_language_mismatch("Ελληνικά", "Φωτογραφίες") is False