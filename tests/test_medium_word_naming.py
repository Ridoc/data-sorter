"""Medium-word folder naming + dissolve.

A folder named only after the medium ("bilder", "pics", "screens") carries no
subject of its own, so the destination must be named for its owner, event or
depicted subject instead — and a purely medium-word folder dissolves into the
category rather than leaving a media-word subfolder behind.
"""

import pytest
from pathlib import Path

from sorter.folder_classifier import resolve_folder_move
from sorter.executor import _dissolve_folder


@pytest.mark.parametrize("rel,category", [
    ("EVE/zenos bilder", "Media/Photos/Zeno"),
    ("Tina/bilder", "Media/Photos/Tina"),
    ("EVE/-Y-/old_G_files/Realtreffenpics", "Media/Photos/Real Life Treffen"),
    ("EVE/ID-Cards/Char Bilder", "Zeno/Documents/Design/Chars"),
    ("Eigene Bilder", "Media/Photos/Eigene"),
])
def test_own_target_uses_llm_leaf_verbatim(tmp_path, rel, category):
    """The LLM owns the leaf when it names the destination for owner/event."""
    move = resolve_folder_move(
        {"path": rel},
        {"category_path": category, "confidence": 85, "reason": "r", "own_target": True},
        tmp_path,
    )
    assert move is not None
    assert str(Path(move["target"]).relative_to(tmp_path)) == category


def test_without_own_target_leaf_still_comes_from_source(tmp_path):
    """Regression guard: normal folders keep the code-owned leaf contract."""
    move = resolve_folder_move(
        {"path": "EVE/BO Logo"},
        {"category_path": "Zeno/Documents/Design", "confidence": 85, "reason": "r"},
        tmp_path,
    )
    assert str(Path(move["target"]).relative_to(tmp_path)) == "Zeno/Documents/Design/BO Logo"


def test_own_target_cannot_escape_nas_root(tmp_path):
    """A verbatim path must never climb out of the NAS root."""
    assert resolve_folder_move(
        {"path": "x/bilder"},
        {"category_path": "../../etc", "confidence": 85, "reason": "r", "own_target": True},
        tmp_path,
    ) is None


def test_dissolve_moves_children_into_category_and_drops_folder(tmp_path):
    src = tmp_path / "bilder&screens"
    src.mkdir()
    (src / "10.jpg").write_text("a")
    (src / "2005.11.20.jpg").write_text("b")
    (src / "sub").mkdir()
    (src / "sub" / "deep.png").write_text("c")

    target = tmp_path / "Media" / "Photos"
    assert _dissolve_folder(src, target) is True

    assert not src.exists()                                    # medium-word folder gone
    assert (target / "10.jpg").read_text() == "a"
    assert (target / "2005.11.20.jpg").read_text() == "b"
    assert (target / "sub" / "deep.png").read_text() == "c"     # nested dirs survive


def test_dissolve_suffixes_name_collisions(tmp_path):
    src = tmp_path / "bilder"
    src.mkdir()
    (src / "a.jpg").write_text("new")
    target = tmp_path / "Media" / "Photos"
    target.mkdir(parents=True)
    (target / "a.jpg").write_text("existing")

    assert _dissolve_folder(src, target) is True
    assert (target / "a.jpg").read_text() == "existing"        # not clobbered
    assert (target / "a_1.jpg").read_text() == "new"


def test_dissolve_dry_run_touches_nothing(tmp_path):
    src = tmp_path / "bilder"
    src.mkdir()
    (src / "a.jpg").write_text("a")
    target = tmp_path / "Media" / "Photos"

    assert _dissolve_folder(src, target, dry_run=True) is True
    assert src.exists()
    assert not target.exists()


def test_parser_keeps_dissolve_and_own_target_flags():
    from sorter.classifier import OllamaClient
    client = OllamaClient.__new__(OllamaClient)
    parsed = client._parse_response(
        '[{"path": "Tina/bilder", "category_path": "Media/Photos/Tina", '
        '"confidence": 85, "own_target": true, "dissolve": true}]'
    )
    assert parsed[0]["own_target"] is True
    assert parsed[0]["dissolve"] is True


def test_parser_defaults_flags_to_false():
    from sorter.classifier import OllamaClient
    client = OllamaClient.__new__(OllamaClient)
    parsed = client._parse_response('[{"path": "x", "category_path": "Media/Photos"}]')
    assert parsed[0]["own_target"] is False
    assert parsed[0]["dissolve"] is False
