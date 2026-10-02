"""Language detection heuristic — charset-based + common German word list, no external libs.

Detects German (umlauts/ß, common ASCII words like Haus/Wohnung/Schule,
German suffixes -ung/-heit/-keit) and Greek (Greek alphabet) in folder/file
names to prevent the LLM from anglicizing non-English content in destination paths.
"""

import re
from typing import Literal

_LANG = Literal["de", "el", "en", "other"]

# German-specific characters (Umlaute, ß)
_GERMAN_CHARS = set("äöüÄÖÜß")

# Common ASCII-only German words (wouldn't appear in English folder names)
# These cover the most common folder names in the user's NAS without umlauts
_GERMAN_WORDS = {
    "haus", "wohnung", "schule", "ausbildung", "beruf", "arbeit", "projekt",
    "bilder", "fotos", "eigene", "urlaub", "reise", "rechnung", "rechnungen",
    "vertrag", "verträge", "zeugnis", "zeugnisse", "bewerbung", "bewerbungen",
    "unterlagen", "notizen", "ordner", "dateien", "dokument", "dokumente",
    "steuer", "steuern", "wochen", "monate", "jahre", "jahr",
    "sonstiges", "diverses", "verschiedenes", "archiv", "archive",
    "lernen", "studium", "schule", "stunde", "stunden", "noten",
    "ausbildung", "weiterbildung", "praxis", "theorie",
    "kosten", "einnahmen", "ausgaben", "finanzen",
    "angebote", "bestellung", "bestätigung", "abrechnung",
    "kündigung", "mahnung", "bescheid", "bescheinigung",
    "einkauf", "verkauf", "rechnungswesen",
    "bildschirm", "screenshots", "aufnahmen",
    "downloads", "uploads", "übertragung",
    "einstellungen", "konfiguration", "vorlagen",
    "familie", "freunde", "bekannte",
    "spiele", "spiel", "entwicklung", "entwicklungen",
    "website", "webseite", "homepage", "blog",
    "datenbank", "server", "backup", "sicherung",
    "persönlich", "privat", "geschäftlich",
    "alt", "neu", "aktuell", "abgeschlossen",
    "grafik", "grafiken",  # German spelling (English: graphic/graphics)
    "vorlage", "vorlagen",
    "beitrag", "beiträge", "thema", "themen",
    "banner", "button", "buttons", "marke", "marken",
    "neues", "forum",
}

# German suffixes that strongly indicate German nouns (ASCII-safe)
_GERMAN_SUFFIX_RE = re.compile(r"(?i)(ung|heit|keit|schaft|tum|nis|sal)$")


def _word_is_german_ascii(word: str) -> bool:
    """Check if a single ASCII word looks German via suffix patterns."""
    w = word.lower().strip("_-. ")
    if not w or len(w) < 3:
        return False
    if w in _GERMAN_WORDS:
        return True
    if _GERMAN_SUFFIX_RE.search(w):
        # Common German suffixes — verify against known English false positives
        if w.endswith("ung") and not w.endswith("young"):
            return True
        if w.endswith("heit"):
            return True
        if w.endswith("keit"):
            return True
        if w.endswith("schaft"):
            return True
        if w.endswith("tum"):
            return True
    return False


def detect_language(name: str) -> _LANG:
    """Detect language of a folder/file name using charset + word heuristics.

    Detection priority: Greek > German (umlauts) > German (ASCII words) > English.

    Args:
        name: The folder or file name to analyze.

    Returns:
        "de" if German (umlauts/ß, common German words, German suffixes)
        "el" if Greek (Greek alphabet found)
        "en" if ASCII-only and no German markers found
    """
    if not name:
        return "en"

    # Greek alphabet check (highest priority)
    if any(0x0370 <= ord(c) <= 0x03FF for c in name):
        return "el"

    # Umlaut/ß → definitely German
    if any(c in _GERMAN_CHARS for c in name):
        return "de"

    # Split into words and check each
    words = re.split(r"[\s_\-.,/()]+", name)
    german_word_count = sum(1 for w in words if _word_is_german_ascii(w))

    # If more than half of the non-trivial words are German → German
    nontrivial = [w for w in words if len(w) >= 3]
    if nontrivial and german_word_count / len(nontrivial) >= 0.5:
        return "de"

    return "en"


def leaf_language_mismatch(category_leaf: str, source_name: str) -> bool:
    """Check if the LLM's category leaf language differs from source folder language.

    Returns True if they are in different languages, meaning the LLM invented
    a translated/anglicized leaf instead of keeping the source folder name.

    Examples:
        leaf="Design", source="Haus in Zypern" -> True  (en vs de — "Haus" is German)
        leaf="Entwürfe Logo", source="Logo" -> False    (both de)
        leaf="Photos", source="Fotos" -> True            (en vs de — "Fotos" is German)
        leaf="Änderungen", source="Änderungen" -> False  (same name)
        leaf="Skyline", source="Wohnung" -> True          (en vs de — "Wohnung" is German)
        leaf="Documents", source="Dokumente" -> True      (en vs de)
    """
    if not category_leaf or not source_name:
        return False

    cat_lang = detect_language(category_leaf)
    src_lang = detect_language(source_name)

    # Source is known non-English (German/Greek) but LLM returned English -> mismatch
    if src_lang in ("de", "el") and cat_lang == "en":
        return True

    return False