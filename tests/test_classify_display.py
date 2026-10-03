"""Driven tests for sort._classify_with_progress's per-file display branch.

WHY this file exists (tech-debt classify-progress-display-branch-untested):
this function renders `name -> category -> confidence` for EVERY classified file
and carries escape() call sites, yet until now ZERO tests referenced it. The
cost was already realised — a mis-scoped lazy `escape` import shipped through a
fully GREEN 528-test suite precisely because this branch never executed. A
rendering crash here is user-visible on a NAS of real family photos.

The static escape() guard (tests/test_markup_escaping.py) cannot reach these
branches: it inspects source, it never RUNS the function. This file drives it.

Only the boundaries are stubbed — OllamaClient.from_config (no network) and the
taxonomy loaders (no config file on disk). The display logic under test is the
real one. Every path lives under tmp_path; the NAS is never touched.
"""
import io

import pytest
from rich.console import Console

import sort as S
from sorter.scanner import FileEntry

TAXONOMY = {
    "top_level": ["Zeno", "Family", "Company", "Projects", "Media", "Archives"],
    "categories": {
        "Media": ["Photos", "Music", "Videos"],
        "Archives": ["Old_Projects", "_Duplicates_Delete"],
        "Zeno": ["Documents", "Downloads"],
    },
}


def _entry(tmp_path, name):
    """A real FileEntry (NamedTuple) rooted in tmp_path — never the NAS."""
    p = tmp_path / name
    p.write_bytes(b"x")
    return FileEntry(
        path=p, rel_path=name, name=name, mime="image/jpeg",
        size=1, mtime=0.0, is_dir=False,
    )


class _FakeClient:
    """Returns canned LLM results per rel_path. No network."""

    def __init__(self, results):
        self.results = results
        self.batches = []

    def classify_batch(self, files, taxonomy_yaml):
        self.batches.append([f.rel_path for f in files])
        return [dict(self.results.get(f.rel_path, {})) for f in files]


def _run(tmp_path, monkeypatch, results, entries=None, batch_size=8):
    """Drive the REAL _classify_with_progress with stubbed boundaries."""
    entries = entries or [_entry(tmp_path, n) for n in results]
    client = _FakeClient(results)

    # sort.py imports OllamaClient function-locally, so patch the defining class.
    import sorter.classifier as cls_mod
    import sorter.taxonomy as tax
    monkeypatch.setattr(cls_mod.OllamaClient, "from_config",
                        classmethod(lambda cls, path: client))
    monkeypatch.setattr(tax, "get_taxonomy_yaml_string", lambda p: "yaml-str")
    monkeypatch.setattr(tax, "load_taxonomy", lambda p: TAXONOMY)

    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False)
    out = S._classify_with_progress(
        entries, tmp_path / "config.yaml", {"ollama": {"model": "stub"}},
        console, batch_size=batch_size,
    )
    return out, buf.getvalue(), client


def test_normal_move_renders_name_category_confidence(tmp_path, monkeypatch):
    """conf >= 85 and a valid category -> the standard move line."""
    results = {"photo.jpg": {"category_path": "Media/Photos", "confidence": 92,
                              "reason": "family holiday photo"}}
    out, text, _ = _run(tmp_path, monkeypatch, results)

    assert "photo.jpg" in text
    assert "Media/Photos" in text
    assert "92%" in text
    assert "family holiday photo" in text
    # The real return value carries the resolved action.
    assert out[0]["action"] == "move"
    assert out[0]["path"] == "photo.jpg"


def test_review_band_renders_dim_red_line_not_the_normal_one(tmp_path, monkeypatch):
    """category_path omitted -> _Unsorted_Review -> the [dim]/[red] branch.

    This is a DIFFERENT f-string from the normal branch; only running it proves
    that one is still syntactically valid and still escapes.
    """
    results = {"mystery.bin": {"confidence": 40, "reason": "unknown blob"}}
    out, text, _ = _run(tmp_path, monkeypatch, results)

    assert "mystery.bin" in text
    assert "_Unsorted_Review" in text
    assert "40%" in text
    assert out[0]["action"] == "review"


def test_delete_action_renders_trash_branch(tmp_path, monkeypatch):
    """category_path == DELETE -> action delete -> the 🗑️ branch."""
    results = {"dupe.jpg": {"category_path": "DELETE", "confidence": 95,
                            "reason": "exact duplicate"}}
    out, text, _ = _run(tmp_path, monkeypatch, results)

    assert "🗑️" in text
    assert "dupe.jpg" in text
    assert out[0]["action"] == "delete"


def test_rename_suggestion_renders_with_detected_language(tmp_path, monkeypatch):
    """A suggested_name differing from the original prints the 📝 rename line,
    and the language is scraped out of the reason text."""
    results = {
        "IMG_0042.JPG": {
            "category_path": "Media/Photos", "confidence": 88,
            "reason": "detected language: de",
            "suggested_name": "Sommerurlaub_2024.jpg",
        }
    }
    out, text, _ = _run(tmp_path, monkeypatch, results)

    assert "📝" in text
    assert "Sommerurlaub_2024.jpg" in text
    # Pin the PARENTHESISED value, not a bare "de": the reason text itself
    # contains "de", so a loose `in` check also passes when lang detection is
    # stubbed out to "?" — that mutation would ship unnoticed.
    assert "(lang: de)" in text


def test_rename_line_absent_when_name_is_unchanged(tmp_path, monkeypatch):
    """suggested_name identical to the original must NOT print a rename line."""
    results = {
        "same.jpg": {"category_path": "Media/Photos", "confidence": 88,
                     "reason": "ok", "suggested_name": "same.jpg"}
    }
    _, text, _ = _run(tmp_path, monkeypatch, results)
    assert "📝" not in text


def test_long_filename_is_truncated_to_42_plus_ellipsis(tmp_path, monkeypatch):
    """Names over 45 chars are cut, so the listing stays one line wide."""
    long_name = "a" * 60 + ".jpg"
    results = {long_name: {"category_path": "Media/Photos", "confidence": 90,
                           "reason": "ok"}}
    _, text, _ = _run(tmp_path, monkeypatch, results)

    assert long_name not in text
    assert "a" * 42 + "..." in text


def test_markup_like_name_survives_rendering(tmp_path, monkeypatch):
    """The defect this whole branch is guilty of: a name containing Rich markup
    must display as itself, not be eaten as a style tag.

    Brackets ARE legal in SMB folder names on the NAS, but '/' is not — so the
    fixture uses a markup-looking name that is also creatable on disk.
    """
    weird = "[bold]Album[cyan].jpg"
    results = {weird: {"category_path": "Media/Photos", "confidence": 90,
                       "reason": "ok"}}
    _, text, _ = _run(tmp_path, monkeypatch, results)

    assert "[bold]Album[cyan].jpg" in text
    assert "\\[" not in text  # no leaked backslash


def test_every_escaped_slot_is_observable_when_it_carries_markup(tmp_path, monkeypatch):
    """Each escape() in this branch must be INDEPENDENTLY observable.

    The other tests use plain category paths, so removing escape(cat) or
    escape(reason_short) changes nothing they can see. This one feeds markup-
    bearing values into EVERY escaped slot at once, so dropping any single
    escape() renders differently and fails here.

    Rich swallows a lowercase tag like [red]; [REVIEW] survived historically
    only by accident. The rendered text must carry the brackets verbatim.
    """
    weird_name = "[bold]Album[cyan].jpg"
    weird_cat = "Media/[green]Photos"
    weird_reason = "matches [red]rule"
    weird_suggest = "[bold]Renamed[cyan].jpg"
    results = {
        weird_name: {
            "category_path": weird_cat,
            "confidence": 88,
            "reason": weird_reason,
            "suggested_name": weird_suggest,
        }
    }
    _, text, _ = _run(tmp_path, monkeypatch, results)

    for value in (weird_name, weird_cat, weird_reason, weird_suggest):
        assert value in text, f"{value!r} was altered/dropped by an unescaped slot"
    assert "\\[" not in text, "escape() missing — literal backslash leaked"


def test_batching_passes_through_and_every_file_is_reported(tmp_path, monkeypatch):
    """Batch loop sanity: files are chunked by batch_size and each gets a line."""
    names = [f"f{i}.jpg" for i in range(5)]
    results = {n: {"category_path": "Media/Photos", "confidence": 80,
                   "reason": "ok"} for n in names}
    entries = [_entry(tmp_path, n) for n in names]

    out, text, client = _run(tmp_path, monkeypatch, results, entries=entries,
                            batch_size=2)

    assert len(client.batches) == 3, f"5 files at batch_size=2 -> 3 batches, got {client.batches}"
    assert [len(b) for b in client.batches] == [2, 2, 1]
    assert len(out) == 5
    for n in names:
        assert n in text


@pytest.mark.parametrize("reason,expected", [
    ("detected language: de", "de"),      # the ORIGINAL bug: matched "la"
    ("detected: de", "de"),                # no 'language' keyword at all
    ("language detected: es", "es"),       # reversed qualifier order
    ("lang en", "en"),                     # whitespace separator, no colon
    ("language: deu", "deu"),              # 3-letter ISO-639-2 must NOT truncate
    ("detected language: pt-BR", "pt"),    # region suffix must not be swallowed
    ("no language here", "?"),             # negative control: fall back to '?'
    ("", "?"),                             # empty reason
    (None, "?"),                           # LLM emitted explicit "reason": null
])
def test_language_detection_shapes(tmp_path, monkeypatch, reason, expected):
    """Pin the extraction matrix, not one happy path.

    Two defects lived in this one regex: an unanchored capture returned the head
    of the NEXT word ('detected la[nguage]'), and a bare \\b silently rejected
    3-letter ISO codes. Both render wrong text to the user and neither reached
    production through a green suite.
    """
    results = {
        "IMG_0042.JPG": {
            "category_path": "Media/Photos", "confidence": 88, "reason": reason,
            "suggested_name": "Sommerurlaub_2024.jpg",
        }
    }
    _, text, _ = _run(tmp_path, monkeypatch, results)
    assert f"(lang: {expected})" in text, f"reason {reason!r} -> expected {expected!r}"
