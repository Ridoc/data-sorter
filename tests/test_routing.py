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