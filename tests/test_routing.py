"""Tests for sorter/routing.py — deterministic extension→category rules."""
import pytest
from pathlib import Path

from sorter.routing import (
    EXT_ROUTE,
    dominant_media_kind,
    get_media_kind,
    route_by_extension,
    should_protect_from_delete,
)


class TestRouteByExtension:
    def test_audio_goes_to_music(self):
        assert route_by_extension("song.mp3") == "Media/Music"
        assert route_by_extension("voice.wav") == "Media/Music"
        assert route_by_extension("track.flac") == "Media/Music"
        assert route_by_extension("au_chunk.au") == "Media/Music"
        assert route_by_extension("dictation.dss") == "Media/Music"
        assert route_by_extension("album.nri") == "Media/Music"
        assert route_by_extension("export.mcx") == "Media/Music"

    def test_video_goes_to_videos(self):
        assert route_by_extension("clip.mp4") == "Media/Videos"
        assert route_by_extension("movie.mkv") == "Media/Videos"
        assert route_by_extension("recording.avi") == "Media/Videos"
        assert route_by_extension("film.wmv") == "Media/Videos"

    def test_image_goes_to_photos(self):
        assert route_by_extension("photo.jpg") == "Media/Photos"
        assert route_by_extension("pic.jpeg") == "Media/Photos"
        assert route_by_extension("art.png") == "Media/Photos"
        assert route_by_extension("logo.svg") == "Media/Photos"
        assert route_by_extension("graphic.gif") == "Media/Photos"

    def test_doc_goes_to_documents(self):
        assert route_by_extension("report.pdf") == "Zeno/Documents"
        assert route_by_extension("letter.docx") == "Zeno/Documents"
        assert route_by_extension("spreadsheet.xlsx") == "Zeno/Documents"
        assert route_by_extension("notes.txt") == "Zeno/Documents"
        assert route_by_extension("file.tex") == "Zeno/Documents"
        assert route_by_extension("slides.pps") == "Zeno/Documents"

    def test_design_goes_to_design(self):
        assert route_by_extension("logo.psd") == "Zeno/Documents/Design"
        assert route_by_extension("art.pspimage") == "Zeno/Documents/Design"
        assert route_by_extension("composite.xcf") == "Zeno/Documents/Design"
        assert route_by_extension("vector.ai") == "Zeno/Documents/Design"

    def test_binary_goes_to_downloads(self):
        assert route_by_extension("setup.exe") == "Zeno/Downloads"
        assert route_by_extension("archive.zip") == "Zeno/Downloads"
        assert route_by_extension("data.rar") == "Zeno/Downloads"
        assert route_by_extension("database.db") == "Zeno/Downloads"
        assert route_by_extension("part.r00") == "Zeno/Downloads"

    def test_unknown_extension_returns_none(self):
        assert route_by_extension("file.xyz") is None
        assert route_by_extension("noext") is None

    def test_mime_fallback_for_unknown_ext(self):
        """When extension is unknown but MIME is known, route by MIME."""
        assert route_by_extension("file.weird", "audio/opus") == "Media/Music"
        assert route_by_extension("file.weird", "video/unknown") == "Media/Videos"
        assert route_by_extension("file.weird", "image/tiff") == "Media/Photos"
        assert route_by_extension("file.weird", "text/calendar") == "Zeno/Documents"

    def test_case_insensitivity(self):
        assert route_by_extension("song.MP3") == "Media/Music"
        assert route_by_extension("clip.MOV") == "Media/Videos"
        assert route_by_extension("photo.JPG") == "Media/Photos"
        assert route_by_extension("report.PDF") == "Zeno/Documents"

    def test_skips_if_already_under_projects(self):
        """File under Projects/ should NOT be extension-routed (preserve context)."""
        assert route_by_extension("Projects/FC_Squad/custom_song.mp3") is None
        assert route_by_extension("Projects/ERGO_Paphos/logo.png") is None

    def test_skips_if_under_taxonomy(self):
        """File under Zeno/, Media/, etc. should NOT be extension-routed."""
        assert route_by_extension("Zeno/Documents/report.pdf") is None
        assert route_by_extension("Media/Photos/photo.jpg") is None
        assert route_by_extension("Family/Mom/recipe.txt") is None
        assert route_by_extension("Archives/Old_Projects/archive.zip") is None

    def test_routes_unorganized_files(self):
        """Files in non-taxonomy paths (Diverse Daten/) should be extension-routed."""
        assert route_by_extension("Diverse Daten/song.mp3") == "Media/Music"
        assert route_by_extension("Diverse Daten/photo.jpg") == "Media/Photos"
        assert route_by_extension("root_file.pdf") == "Zeno/Documents"

    def test_mixed_path_has_taxonomy_segment(self):
        """Path containing a taxonomy segment should skip routing."""
        assert route_by_extension("Diverse Daten/Zeno/report.mp3") is None  # has /Zeno/ segment
        assert route_by_extension("Diverse Daten/Projects/song.mp3") is None  # has /Projects/


class TestGetMediaKind:
    def test_returns_kind_string(self):
        assert get_media_kind("song.mp3") == "audio"
        assert get_media_kind("clip.mp4") == "video"
        assert get_media_kind("photo.jpg") == "image"
        assert get_media_kind("doc.pdf") == "doc"
        assert get_media_kind("design.psd") == "design"
        assert get_media_kind("setup.exe") == "binary"

    def test_unknown_returns_empty(self):
        assert get_media_kind("file.xyz") == ""


class TestDominantMediaKind:
    def test_audio_dominant(self):
        ext_counts = {".mp3": 10, ".wav": 5, ".jpg": 3}
        assert dominant_media_kind(ext_counts) == "audio"

    def test_image_dominant(self):
        ext_counts = {".jpg": 20, ".png": 10, ".docx": 2}
        assert dominant_media_kind(ext_counts) == "image"

    def test_no_dominant(self):
        ext_counts = {".jpg": 5, ".mp3": 5, ".pdf": 5}  # image=5/15=33%
        assert dominant_media_kind(ext_counts) == ""  # no kind >= 50%

    def test_empty(self):
        assert dominant_media_kind({}) == ""

    def test_custom_threshold(self):
        ext_counts = {".mp3": 4, ".wav": 1, ".jpg": 5}
        # audio = 5/10 = 0.5 = exactly threshold (>= matches)
        assert dominant_media_kind(ext_counts, 0.5) == "audio"
        # stricter threshold
        ext_counts2 = {".mp3": 5, ".wav": 2, ".jpg": 8}
        assert dominant_media_kind(ext_counts2, 0.8) == ""  # audio 7/15=47% < 80%


class Test3DRouting:
    def test_skp_routes_to_design(self):
        assert route_by_extension("wohnung_2013_v1.skp") == "Zeno/Documents/Design"
        assert route_by_extension("model.skb") == "Zeno/Documents/Design"
        assert route_by_extension("apartment.dwg") == "Zeno/Documents/Design"
        assert route_by_extension("scene.3ds") == "Zeno/Documents/Design"
        assert route_by_extension("mesh.obj") == "Zeno/Documents/Design"

    def test_skp_under_projects_not_routed(self):
        """3D models inside Projects/ keep context (path-context guard)."""
        assert route_by_extension("Projects/Wohnung/model.skp") is None


class TestDeleteProtection:
    def test_protected_extensions(self):
        """User content must never be auto-deleted."""
        assert should_protect_from_delete("wohnung.skp") is True
        assert should_protect_from_delete("plan.skb") is True
        assert should_protect_from_delete("model.dwg") is True
        assert should_protect_from_delete("photo.jpg") is True
        assert should_protect_from_delete("song.mp3") is True
        assert should_protect_from_delete("video.mp4") is True
        assert should_protect_from_delete("report.pdf") is True
        assert should_protect_from_delete("design.psd") is True

    def test_unprotected_extensions(self):
        """Temp/generated files can still be deletion candidates."""
        assert should_protect_from_delete("cache.tmp") is False
        assert should_protect_from_delete("app.exe") is False
        assert should_protect_from_delete("archive.rar") is False
        assert should_protect_from_delete("thumbs.db") is False
        assert should_protect_from_delete("setup.msi") is False

    def test_case_insensitive(self):
        assert should_protect_from_delete("MODEL.SKP") is True
        assert should_protect_from_delete("photo.JPG") is True

# ── design-routing-fix: dominant-type override is an LLM-failure fallback only ──

import json


def _folder_entry(path, exts):
    name = path.split("/")[-1]
    return {
        "path": path, "is_folder": True, "file_count": sum(exts.values()),
        "total_size": 1000, "mime_types": [], "top_extensions": exts,
        "sample_files": [f"{name}_{i}.png" for i in range(3)],
        "children": [f"{path}/{name}_{i}.png" for i in range(3)],
        "age_classification": "recent",
    }


@pytest.fixture
def classify(monkeypatch):
    """Run the real _classify_folders with a stubbed LLM; returns (fn, prompts)."""
    from sorter.classifier import OllamaClient
    import sort as sort_mod

    prompts = []

    def run(entry, llm_result=None, raises=False):
        def fake_call(self, prompt):
            prompts.append(prompt)
            if raises:
                raise RuntimeError("llm down")
            return json.dumps([dict(llm_result, path=entry["path"], is_folder=True)])
        monkeypatch.setattr(OllamaClient, "_call_ollama", fake_call)
        return sort_mod._classify_folders(
            [entry], Path("config.yaml"), {"ollama": {"batch_size": 15}}, "taxonomy"
        )[0]

    return run, prompts


class TestDominantOverrideIsFallbackOnly:
    IMG = {".png": 9}

    def test_confident_llm_design_not_flipped_to_photos(self, classify):
        run, _ = classify
        r = run(_folder_entry("Schule/Entwürfe Logo", self.IMG),
                {"category_path": "Zeno/Documents/Design/Entwürfe Logo",
                 "confidence": 90, "reason": "logo drafts"})
        assert r["category_path"] == "Zeno/Documents/Design/Entwürfe Logo"
        assert "[Det" not in r["reason"]

    def test_low_confidence_llm_verdict_respected(self, classify):
        run, _ = classify
        r = run(_folder_entry("Scans/Zeugnisse", self.IMG),
                {"category_path": "Zeno/Documents", "confidence": 60, "reason": "scans"})
        assert r["category_path"] == "Zeno/Documents"
        assert r["confidence"] == 60

    def test_unsorted_result_falls_back_to_dominant_kind(self, classify):
        run, _ = classify
        r = run(_folder_entry("X/Pics", self.IMG),
                {"category_path": "_Unsorted_Review", "confidence": 0, "reason": "?"})
        assert r["category_path"] == "Media/Photos"
        assert "Det fallback" in r["reason"]

    def test_llm_exception_falls_back_to_dominant_kind(self, classify):
        run, _ = classify
        r = run(_folder_entry("X/Pics", self.IMG), raises=True)
        assert r["category_path"] == "Media/Photos"

    def test_no_dominant_kind_stays_unsorted(self, classify):
        run, _ = classify
        r = run(_folder_entry("X/Mixed", {".png": 3, ".mp3": 3, ".pdf": 3}),
                {"category_path": "_Unsorted_Review", "confidence": 0, "reason": "?"})
        assert r["category_path"] == "_Unsorted_Review"


class TestFolderPromptRules:
    def test_folder_prompt_has_design_and_subject_rules(self, classify):
        run, prompts = classify
        run(_folder_entry("A/Banner", {".png": 5}),
            {"category_path": "Zeno/Documents/Design/Banner", "confidence": 90, "reason": "x"})
        p = prompts[0]
        assert "SUBJECT first" in p
        assert "Branding, layouts, mockups and drafts" in p
        assert "Zeno/Documents/Design" in p
        assert "never a" in p and "top-level Zeno/Design" in p
        assert "Subject: Banner" in p
        # Medium-word naming contract (owner/event instead of "bilder"/"pics")
        assert "Bilder, bilder, pics" in p
        assert "own_target" in p and "dissolve" in p
        # The old "prefer Design when unsure" bias is what misrouted Eigene
        # Bilder / zenos bilder; it must not creep back in.
        assert "prefer Design" not in p


class TestResultOrderMatchesInput:
    """LLM returns results in arbitrary order; callers zip positionally."""

    def test_out_of_order_results_rekeyed_to_input_order(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [
            _folder_entry("D/Rammstein 2010", {".jpg": 20}),
            _folder_entry("D/Sunset Villa", {".jpg": 5}),
            _folder_entry("D/Katzen", {".jpg": 9}),
        ]
        # LLM answers in reverse order, each with a *correct* category
        answers = [
            {"path": "D/Katzen", "category_path": "Media/Photos/Katzen", "confidence": 85, "reason": "cats"},
            {"path": "D/Sunset Villa", "category_path": "Zeno/Documents/Sunset Villa", "confidence": 85, "reason": "house"},
            {"path": "D/Rammstein 2010", "category_path": "Media/Photos/Rammstein 2010", "confidence": 85, "reason": "concert"},
        ]
        monkeypatch.setattr(
            OllamaClient, "_call_ollama",
            lambda self, prompt: json.dumps(answers),
        )
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert [r["path"] for r in out] == [e["path"] for e in entries]
        assert out[0]["category_path"] == "Media/Photos/Rammstein 2010"
        assert out[1]["category_path"] == "Zeno/Documents/Sunset Villa"
        assert out[2]["category_path"] == "Media/Photos/Katzen"

    def test_missing_result_uses_fallback_not_positional_mixup(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry("D/A", {".jpg": 3}), _folder_entry("D/B", {".jpg": 3})]
        monkeypatch.setattr(
            OllamaClient, "_call_ollama",
            lambda self, prompt: json.dumps([
                {"path": "D/A", "category_path": "Media/Photos/A", "confidence": 85, "reason": "x"}]),
        )
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert [r["path"] for r in out] == ["D/A", "D/B"]
        # B was never answered for (retry also unanswered) -> deterministic fallback
        assert out[1]["category_path"] == "Media/Photos"
        assert "Det fallback" in out[1]["reason"]

    def test_unanswered_without_dominant_kind_stays_review(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry("D/Mixed", {".png": 3, ".mp3": 3, ".pdf": 3})]
        monkeypatch.setattr(OllamaClient, "_call_ollama", lambda self, prompt: "[]")
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert out[0]["category_path"] == "_Unsorted_Review"
        assert out[0]["confidence"] == 0


class TestOmittedFoldersRetried:
    """LLM drops folders from big batches; they get one focused retry."""

    def test_dropped_folder_is_retried_not_reviewed(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry(f"D/F{i}", {".png": 4}) for i in range(4)]
        calls = {"n": 0}

        def fake_call(self, prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                # answer only the first two of four
                return json.dumps([
                    {"path": "D/F0", "category_path": "Media/Photos/F0", "confidence": 85, "reason": "a"},
                    {"path": "D/F1", "category_path": "Media/Photos/F1", "confidence": 85, "reason": "b"},
                ])
            assert "D/F2" in prompt and "D/F3" in prompt
            return json.dumps([
                {"path": "D/F2", "category_path": "Zeno/Documents/Design/F2", "confidence": 85, "reason": "c"},
                {"path": "D/F3", "category_path": "Zeno/Documents/Design/F3", "confidence": 85, "reason": "d"},
            ])

        monkeypatch.setattr(OllamaClient, "_call_ollama", fake_call)
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert calls["n"] == 2
        assert [r["path"] for r in out] == ["D/F0", "D/F1", "D/F2", "D/F3"]
        assert out[2]["category_path"] == "Zeno/Documents/Design/F2"
        assert out[3]["category_path"] == "Zeno/Documents/Design/F3"

    def test_llm_error_yields_fallback_not_crash(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry("D/A", {".png": 3})]
        monkeypatch.setattr(OllamaClient, "_call_ollama",
                            lambda self, prompt: (_ for _ in ()).throw(RuntimeError("down")))
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert len(out) == 1
        # LLM unreachable twice -> deterministic last-resort net, not a crash
        assert out[0]["category_path"] == "Media/Photos"
        assert "Det fallback" in out[0]["reason"]


class TestFallbackCoversExplicitUnsorted:
    """Plan: fallback fires on _Unsorted_Review OR confidence 0."""

    def test_unsorted_with_mid_confidence_gets_fallback(self, classify):
        run, _ = classify
        r = run(_folder_entry("A/2015 Grafiken", {".png": 5}),
                {"category_path": "_Unsorted_Review", "confidence": 30, "reason": "unsure"})
        assert r["category_path"] == "Media/Photos"
        assert "Det fallback" in r["reason"]

    def test_nest_guarded_folder_stays_in_review(self, classify):
        run, _ = classify
        # folder name appears mid-path but is NOT the leaf -> self-nesting
        r = run(_folder_entry("A/Bilder", {".png": 5}),
                {"category_path": "Zeno/Documents/Bilder/Fotos", "confidence": 80, "reason": "x"})
        assert r["category_path"] == "_Unsorted_Review"
        assert "Nest guard" in r["reason"]
        assert "Det fallback" not in r["reason"]
        assert "_nest_flagged" not in r

    def test_leaf_equal_to_folder_name_is_legitimate(self, classify):
        run, _ = classify
        r = run(_folder_entry("A/Bilder", {".png": 5}),
                {"category_path": "Zeno/Documents/Bilder", "confidence": 80, "reason": "x"})
        assert r["category_path"] == "Zeno/Documents/Bilder"
        assert "Nest guard" not in r["reason"]


class TestRetryPromptKeepsRules:
    """A bare re-ask made the model guess by type; the retry must reuse rules."""

    def test_retry_prompt_contains_design_rules(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry(f"D/F{i}", {".png": 4}) for i in range(3)]
        seen = []

        def fake_call(self, prompt):
            seen.append(prompt)
            if len(seen) == 1:
                return json.dumps([{"path": "D/F0", "category_path": "Media/Photos/F0",
                                    "confidence": 85, "reason": "a"}])
            return json.dumps([
                {"path": "D/F1", "category_path": "Zeno/Documents/Design/F1", "confidence": 85, "reason": "b"},
                {"path": "D/F2", "category_path": "Zeno/Documents/Design/F2", "confidence": 85, "reason": "c"},
            ])

        monkeypatch.setattr(OllamaClient, "_call_ollama", fake_call)
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        retry = seen[1]
        assert "Branding, layouts, mockups and drafts" in retry
        assert "Zeno/Documents/Design" in retry
        assert "SUBJECT first" in retry
        assert "own_target" in retry and "dissolve" in retry
        assert "answer for ALL 2 folder(s)" in retry
        assert out[1]["category_path"] == "Zeno/Documents/Design/F1"


class TestTrailingSlashPath:
    """The LLM echoes 'Diverse Daten/EVE/BO Logo/' — must still match."""

    def test_trailing_slash_result_is_matched_to_its_folder(self, monkeypatch):
        import sort as sort_mod
        from sorter.classifier import OllamaClient

        entries = [_folder_entry("Diverse Daten/EVE/BO Logo",
                                 {".jpg": 20, ".pspimage": 17, ".png": 13})]
        monkeypatch.setattr(
            OllamaClient, "_call_ollama",
            lambda self, prompt: json.dumps([{
                "path": "Diverse Daten/EVE/BO Logo/",
                "category_path": "Zeno/Documents/BO_Logo",
                "confidence": 85, "reason": "collection of BO logos"}]),
        )
        out = sort_mod._classify_folders(entries, Path("config.yaml"),
                                         {"ollama": {"batch_size": 15}}, "tax")
        assert out[0]["path"] == "Diverse Daten/EVE/BO Logo"
        assert out[0]["category_path"] == "Zeno/Documents/BO_Logo"
        assert out[0]["confidence"] == 85
        assert "Det fallback" not in out[0]["reason"]
        assert "no result" not in out[0]["reason"]
