"""Tests for deterministic file context detection."""
import time
from pathlib import Path

import pytest

from sorter.context import (
    detect_audacity,
    detect_adobe_premiere,
    context_hint,
    folder_chain,
    classify_age,
    age_string,
    project_key,
    canonicalize_path,
)


class TestDetectAudacity:
    def test_aup_project_file(self):
        assert detect_audacity("Hypnose/hypnose.aup")

    def test_aup3_project_file(self):
        assert detect_audacity("Project/project.aup3")

    def test_au_under_data_dir(self):
        assert detect_audacity("Hypnose/hypnose_data/e00/d00/e0000005.au")
        assert detect_audacity("Hypnose/hypnose_data/e00/file.au")

    def test_under_audacity_temp(self):
        assert detect_audacity("Hypnose/audacity_temp/tmp123.wav")

    def test_deeply_nested_data(self):
        assert detect_audacity("a/b/c/d/e/f_g_data/x/y/z/file.au")

    def test_au_not_under_data(self):
        """.au file NOT under a _data/ or audacity_temp dir is not necessarily Audacity."""
        assert not detect_audacity("somewhere/sound.au")

    def test_non_audacity_file(self):
        assert not detect_audacity("Documents/report.docx")
        assert not detect_audacity("Berufliches/manager.docx")


class TestDetectAdobePremiere:
    def test_prv_extension(self):
        assert detect_adobe_premiere("project.prv")

    def test_auto_save_dir(self):
        assert detect_adobe_premiere("Adobe Premiere Pro Auto-Save/backup.prproj")

    def test_preview_files_dir(self):
        assert detect_adobe_premiere("Adobe Premiere Pro Preview Files/preview.wav")

    def test_conformed_audio_dir(self):
        assert detect_adobe_premiere("Conformed Audio Files/audio.wav")

    def test_non_premiere_file(self):
        assert not detect_adobe_premiere("Documents/report.docx")
        assert not detect_adobe_premiere("Hypnose/hypnose.aup")

    def test_au_not_premiere(self):
        """.au files are NOT Adobe Premiere — they're Audacity format."""
        assert not detect_adobe_premiere("Hypnose/hypnose_data/e00/d00/e0000005.au")


class TestContextHint:
    def test_audacity_data(self):
        assert context_hint("Hypnose/hypnose_data/e00/file.au") == "AUDACITY_PROJECT"

    def test_premiere(self):
        assert context_hint("Adobe Premiere Pro Auto-Save/backup.prproj") == "ADOBE_PREMIERE_TEMP"

    def test_normal(self):
        assert context_hint("Documents/report.docx") == "NONE"
        assert context_hint("Berufliches/manager.docx") == "NONE"

    def test_audacity_prioritized_over_none(self):
        """Audacity detection catches *_data folders."""
        assert "AUDACITY" in context_hint("Project/sound_data/dir/file.au")


class TestFolderChain:
    def test_simple(self):
        assert folder_chain("Berufliches/Hypnose/hypnose_data/e00/file.au") == [
            "Berufliches", "Hypnose", "hypnose_data", "e00"]

    def test_shallow(self):
        assert folder_chain("documents/report.docx") == ["documents"]

    def test_root_file(self):
        assert folder_chain("report.docx") == []


class TestClassifyAge:
    def test_recent(self):
        now = time.time()
        assert classify_age(now, now, now) == "RECENT"

    def test_old_user_content(self):
        """6 years old -> archive_years(5) < 6 < delete_candidate(10) -> OLD_USER_CONTENT"""
        seven_years = time.time() - (365.25 * 86400 * 7)
        assert classify_age(seven_years, seven_years, seven_years) == "OLD_USER_CONTENT"

    def test_old_temp_backup(self):
        """12 years old -> > delete_candidate(10) -> OLD_TEMP_BACKUP"""
        twelve_years = time.time() - (365.25 * 86400 * 12)
        assert classify_age(twelve_years, twelve_years, twelve_years) == "OLD_TEMP_BACKUP"

    def test_zero_timestamps(self):
        assert classify_age(0, 0, 0) == "RECENT"

    def test_custom_threshold(self):
        three_years = time.time() - (365.25 * 86400 * 3)
        # With threshold 2 years, 3 years is over the threshold
        assert classify_age(three_years, three_years, three_years,
                            delete_candidate_years=2, archive_years=1) == "OLD_TEMP_BACKUP"


class TestAgeStringReExport:
    def test_roundtrip(self):
        assert "years" in age_string(time.time() - (365.25 * 86400 * 5))


class TestProjectKey:
    def test_audacity_data_folder(self):
        assert project_key("Berufliches/Hypnose/hypnose_data/e00/d00/file.au") == "Berufliches/Hypnose"

    def test_au_not_under_data(self):
        assert project_key("somewhere/sound.au") is None

    def test_aup_project_file(self):
        assert project_key("Berufliches/Hypnose/hypnose.aup") == "Berufliches/Hypnose"

    def test_aup3_project(self):
        assert project_key("Project/project.aup3") == "Project"

    def test_audacity_temp(self):
        assert project_key("Hypnose/audacity_temp/tmp123.wav") == "Hypnose"

    def test_normal_file(self):
        assert project_key("Documents/report.docx") is None
        assert project_key("Berufliches/manager.docx") is None

    def test_root_file(self):
        assert project_key("file.txt") is None


class TestCanonicalizePath:
    def test_spelling_variant(self):
        from sorter.context import canonicalize_path
        assert canonicalize_path("Zeno/Documents/Hypnosis", "Hypnose") == "Zeno/Documents/Hypnose"

    def test_exact_match(self):
        assert canonicalize_path("Zeno/Documents/Hypnose", "Hypnose") == "Zeno/Documents/Hypnose"

    def test_different_segment(self):
        """Completely different last segment should NOT be changed."""
        assert canonicalize_path("Zeno/Documents", "Hypnose") == "Zeno/Documents"

    def test_no_source_unit(self):
        assert canonicalize_path("Zeno/Documents", "") == "Zeno/Documents"