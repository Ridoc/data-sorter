"""Medium-word naming: detection + the refinement second pass.

The 7b model applies the NAMING rules to a handful of folders but drops them
inside a full batch, so medium-word-named folders get re-asked in a small
batch. Detection decides WHO is re-asked; the LLM still decides WHERE.
"""

import json
from pathlib import Path

import pytest

from sorter.folder_classifier import is_medium_word_name


@pytest.mark.parametrize("name", [
    "bilder", "Bilder", "zenos bilder", "Eigene Bilder", "Char Bilder",
    "bilder&screens", "pics", "Realtreffenpics", "Fotos", "fotos",
    "Photos", "screens", "img", "bilder & screens",
])
def test_medium_word_names_detected(name):
    assert is_medium_word_name(name) is True


@pytest.mark.parametrize("name", [
    "BO Logo", "Katzen", "Zeugnisse", "Design", "Audio", "Zeichnungen",
    "", None,
])
def test_subjective_names_not_flagged(name):
    assert is_medium_word_name(name) is False


class TestRefinementPass:
    """Only medium-word folders are re-asked, and their answers win."""

    def _entries(self, names):
        def _folder_entry(path, exts):
            return {"path": path, "top_extensions": exts, "sample_files": ["a.jpg"],
                    "file_count": 3, "total_size": 100, "children": [],
                    "age_classification": "unknown", "context_hint": ""}
        return [_folder_entry(f"D/{n}", {".jpg": 3}) for n in names]

    def test_medium_word_folders_refined_in_small_batch(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        names = ["BO Logo", "zenos bilder", "Katzen", "Tina/bilder", "Rammstein 2010"]
        entries = self._entries(names)
        sizes = []

        def fake_call(self, prompt):
            sizes.append(prompt.count("[FOLDER]"))
            if len(sizes) == 1:          # main pass: answer everything, plainly
                return json.dumps([
                    {"path": f"D/{n}", "category_path": "Media/Photos",
                     "confidence": 85, "reason": "x"} for n in names])
            # refinement pass: the medium-word folders get proper names
            return json.dumps([
                {"path": "D/zenos bilder", "category_path": "Media/Photos/Zeno",
                 "confidence": 85, "reason": "owner", "own_target": True},
                {"path": "D/Tina/bilder", "category_path": "Media/Photos/Tina",
                 "confidence": 85, "reason": "owner", "own_target": True}])

        monkeypatch.setattr(OllamaClient, "_call_ollama", fake_call)
        out = sort_mod._classify_folders(
            entries, Path("config.yaml"), {"ollama": {"batch_size": 15}}, "tax")

        # Only the 2 medium-word folders were re-asked, not all 5
        assert sizes[0] == 5
        assert sizes[1] == 2
        by_path = {r["path"]: r for r in out}
        assert by_path["D/zenos bilder"]["category_path"] == "Media/Photos/Zeno"
        assert by_path["D/zenos bilder"]["own_target"] is True
        assert by_path["D/Tina/bilder"]["category_path"] == "Media/Photos/Tina"
        # Subjective folders keep their original verdict
        assert by_path["D/BO Logo"]["category_path"] == "Media/Photos"

    def test_refined_review_answer_does_not_overwrite(self, monkeypatch):
        """A refinement that abstains must not clobber a working verdict."""
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = self._entries(["BO Logo", "zenos bilder"])
        calls = []

        def fake_call(self, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps([
                    {"path": "D/BO Logo", "category_path": "Zeno/Documents/Design/BO Logo",
                     "confidence": 85, "reason": "logo"},
                    {"path": "D/zenos bilder", "category_path": "Media/Photos",
                     "confidence": 85, "reason": "x"}])
            return json.dumps([])   # refinement answers nothing

        monkeypatch.setattr(OllamaClient, "_call_ollama", fake_call)
        out = sort_mod._classify_folders(
            entries, Path("config.yaml"), {"ollama": {"batch_size": 15}}, "tax")
        by_path = {r["path"]: r for r in out}
        assert by_path["D/zenos bilder"]["category_path"] == "Media/Photos"
