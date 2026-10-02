from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.text import Text

from sorter.reviewer import (
    ReviewSession,
    CONF_AUTO_ACCEPT,
    CONF_REQUIRE_REVIEW,
    UNSORTED_LABEL,
    GREEN_STYLE,
    YELLOW_STYLE,
    RED_STYLE,
    DELETE_STYLE,
)


def render_tree(tree: Tree) -> str:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=120)
    c.print(tree)
    return buf.getvalue()


def render_table(table: Table) -> str:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=120)
    c.print(table)
    return buf.getvalue()


def make_classification(
    path: str = "doc.pdf",
    category_path: str = "Zeno/Documents",
    confidence: int = 92,
    reason: str = "test file",
    action: str = "move",
):
    return {
        "path": path,
        "category_path": category_path,
        "confidence": confidence,
        "reason": reason,
        "action": action,
    }


def make_dedup(
    type: str = "exact",
    keep_path: str = "Zeno/Documents/report.pdf",
    delete_path: str = "Zeno/Downloads/report_old.pdf",
    keep_size: str = "1.2 MB",
    delete_size: str = "1.2 MB",
    keep_date: str = "2024-01-15",
    delete_date: str = "2023-06-10",
    reason: str = "same content",
    hamming: int = 0,
):
    return {
        "type": type,
        "keep_path": keep_path,
        "delete_path": delete_path,
        "keep_size": keep_size,
        "delete_size": delete_size,
        "keep_date": keep_date,
        "delete_date": delete_date,
        "reason": reason,
        "hamming": hamming,
    }


NAS_ROOT = Path("/mnt/NAS-Zeno/")


class TestTreeGrouping:
    def test_returns_rich_tree(self):
        session = ReviewSession([], [], NAS_ROOT)
        tree = session.build_classification_tree([])
        assert isinstance(tree, Tree)

    def test_groups_by_top_level_category(self):
        classifications = [
            make_classification(path="a.pdf", category_path="Zeno/Documents", confidence=92),
            make_classification(path="b.jpg", category_path="Media/Photos", confidence=71),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        tree = session.build_classification_tree(classifications)
        out = render_tree(tree)
        assert "Zeno" in out
        assert "Media" in out

    def test_low_confidence_goes_to_unsorted(self):
        classifications = [
            make_classification(path="weird.xyz", category_path="Zeno/Documents", confidence=22),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        tree = session.build_classification_tree(classifications)
        out = render_tree(tree)
        assert UNSORTED_LABEL in out

    def test_delete_action_goes_to_unsorted(self):
        classifications = [
            make_classification(path="temp.prproj", category_path="DELETE", confidence=99, action="delete"),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        tree = session.build_classification_tree(classifications)
        out = render_tree(tree)
        assert UNSORTED_LABEL in out

    def test_mixed_categories(self):
        classifications = [
            make_classification(path="a.pdf", category_path="Zeno/Documents", confidence=92),
            make_classification(path="b.jpg", category_path="Family/Emily", confidence=71),
            make_classification(path="c.txt", category_path="Projects/FC_Squad", confidence=95),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        tree = session.build_classification_tree(classifications)
        out = render_tree(tree)
        assert "Zeno" in out
        assert "Family" in out
        assert "Projects" in out


class TestConfidenceColoring:
    def test_high_confidence_green(self):
        session = ReviewSession([], [], NAS_ROOT)
        label = session._format_file_label("doc.pdf", 92, "invoice")
        assert isinstance(label, Text)
        assert label.style == GREEN_STYLE

    def test_medium_confidence_yellow(self):
        session = ReviewSession([], [], NAS_ROOT)
        label = session._format_file_label("notes.txt", 71, "notes")
        assert isinstance(label, Text)
        assert label.style == YELLOW_STYLE

    def test_low_confidence_red(self):
        session = ReviewSession([], [], NAS_ROOT)
        label = session._format_file_label("weird.xyz", 22, "unknown")
        assert isinstance(label, Text)
        assert label.style == RED_STYLE

    def test_auto_accept_boundary(self):
        session = ReviewSession([], [], NAS_ROOT)
        label_85 = session._format_file_label("f.pdf", CONF_AUTO_ACCEPT, "")
        label_84 = session._format_file_label("f.pdf", CONF_AUTO_ACCEPT - 1, "")
        assert label_85.style == GREEN_STYLE
        assert label_84.style == YELLOW_STYLE

    def test_require_review_boundary(self):
        session = ReviewSession([], [], NAS_ROOT)
        label_50 = session._format_file_label("f.pdf", CONF_REQUIRE_REVIEW, "")
        label_49 = session._format_file_label("f.pdf", CONF_REQUIRE_REVIEW - 1, "")
        assert label_50.style == YELLOW_STYLE
        assert label_49.style == RED_STYLE

    def test_delete_action_strikethrough(self):
        session = ReviewSession([], [], NAS_ROOT)
        label = session._format_file_label("temp.prproj", 99, "Adobe temp", action="delete")
        assert label.style == DELETE_STYLE


class TestSummaryTable:
    def test_returns_rich_table(self):
        session = ReviewSession([], [], NAS_ROOT)
        table = session.show_summary_table()
        assert isinstance(table, Table)

    def test_counts_mixed_items(self):
        classifications = [
            make_classification(path="a.pdf", category_path="Zeno/Docs", confidence=92, action="move"),
            make_classification(path="b.txt", category_path="Zeno/Docs", confidence=60, action="move"),
            make_classification(path="c.prproj", category_path="DELETE", confidence=100, action="delete"),
            make_classification(path="d.xyz", category_path="Zeno/Docs", confidence=20, action="move"),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT, dry_run=True)
        session._reviewed = classifications
        table = session.show_summary_table()
        rendered = render_table(table)
        assert "4" in rendered
        assert "1" in rendered
        assert "1" in rendered

    def test_empty_classifications(self):
        session = ReviewSession([], [], NAS_ROOT)
        table = session.show_summary_table()
        rendered = render_table(table)
        assert "0" in rendered

    def test_counts_correctly_with_no_reviewed(self):
        classifications = [
            make_classification(path="a.pdf", confidence=92),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = []
        table = session.show_summary_table()
        rendered = render_table(table)
        assert "1" in rendered


class TestApprovedMoves:
    def test_get_approved_moves_returns_correct_format(self):
        classifications = [
            make_classification(path="a.pdf", category_path="Zeno/Docs", confidence=92),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        moves = session.get_approved_moves()
        assert len(moves) == 1
        assert moves[0]["source"] == str(NAS_ROOT / "a.pdf")
        assert moves[0]["target"] == str(NAS_ROOT / "Zeno" / "Docs" / "a.pdf")
        assert moves[0]["confidence"] == 92

    def test_skip_delete_in_moves(self):
        classifications = [
            make_classification(path="a.prproj", action="delete", confidence=99),
            make_classification(path="b.pdf", confidence=92),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        moves = session.get_approved_moves()
        assert len(moves) == 1
        assert "b.pdf" in moves[0]["source"]

    def test_skip_low_confidence_in_moves(self):
        classifications = [
            make_classification(path="low.xyz", confidence=10),
            make_classification(path="high.pdf", confidence=92),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        moves = session.get_approved_moves()
        assert len(moves) == 1
        assert "high.pdf" in moves[0]["source"]

    def test_no_classifications(self):
        session = ReviewSession([], [], NAS_ROOT)
        moves = session.get_approved_moves()
        assert moves == []


class TestApprovedDeletions:
    def test_get_approved_deletions_from_classifications(self):
        classifications = [
            make_classification(path="temp.prproj", action="delete", confidence=99, reason="Adobe temp"),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        deletions = session.get_approved_deletions()
        assert len(deletions) == 1
        assert deletions[0]["path"] == str(NAS_ROOT / "temp.prproj")
        assert deletions[0]["type"] == "adobe_temp"

    def test_get_approved_deletions_from_dedup(self):
        session = ReviewSession([], [], NAS_ROOT)
        session._approved_deletions.append({
            "path": str(NAS_ROOT / "dup.pdf"),
            "type": "duplicate",
            "reason": "duplicate",
        })
        deletions = session.get_approved_deletions()
        assert len(deletions) == 1
        assert deletions[0]["type"] == "duplicate"

    def test_get_approved_deletions_combined(self):
        classifications = [
            make_classification(path="temp.prproj", action="delete", confidence=99, reason="Adobe temp"),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        session._approved_deletions.append({
            "path": str(NAS_ROOT / "dup.pdf"),
            "type": "duplicate",
            "reason": "duplicate",
        })
        deletions = session.get_approved_deletions()
        assert len(deletions) == 2

    def test_no_deletions(self):
        classifications = [
            make_classification(path="a.pdf", confidence=92),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session._reviewed = classifications
        deletions = session.get_approved_deletions()
        assert deletions == []


class TestRunFlow:
    def test_nothing_to_do(self):
        session = ReviewSession([], [], NAS_ROOT)
        moves, deletions = session.run()
        assert moves == []
        assert deletions == []

    def test_run_with_classifications(self):
        classifications = [make_classification(path="a.pdf", confidence=92)]
        session = ReviewSession(classifications, [], NAS_ROOT)
        with (
            patch("questionary.confirm", return_value=Mock(ask=Mock(return_value=True))),
            patch("questionary.select", return_value=Mock(ask=Mock(return_value="Accept [A]"))),
        ):
            moves, deletions = session.run()
            assert len(moves) == 1
            assert deletions == []

    def test_run_rejects(self):
        classifications = [make_classification(path="a.pdf", confidence=92)]
        session = ReviewSession(classifications, [], NAS_ROOT)
        with patch("questionary.confirm", return_value=Mock(ask=Mock(return_value=False))):
            moves, deletions = session.run()
            assert moves == []
            assert deletions == []


class TestDedupFormatting:
    def test_dedup_table_renders(self):
        pairs = [make_dedup()]
        session = ReviewSession([], pairs, NAS_ROOT)
        with patch("questionary.confirm", return_value=Mock(ask=Mock(return_value=False))):
            session._approved_deletions = []
            session.show_dedup_pairs()
            assert len(session._approved_deletions) == 1

    def test_keep_both_skips_deletion(self):
        pairs = [make_dedup()]
        session = ReviewSession([], pairs, NAS_ROOT)
        with patch("questionary.confirm", return_value=Mock(ask=Mock(return_value=True))):
            session._approved_deletions = []
            session.show_dedup_pairs()
            assert len(session._approved_deletions) == 0


class TestInteractiveReview:
    def test_no_yellow_items(self):
        classifications = [
            make_classification(path="a.pdf", confidence=92),
            make_classification(path="b.prproj", confidence=99, action="delete"),
            make_classification(path="c.xyz", confidence=10),
        ]
        session = ReviewSession(classifications, [], NAS_ROOT)
        session.interactive_review()
        assert len(session._reviewed) == 3

    def test_skip_item_removed_from_reviewed(self):
        classifications = [make_classification(path="a.pdf", confidence=60)]
        session = ReviewSession(classifications, [], NAS_ROOT)
        with patch("questionary.select", return_value=Mock(ask=Mock(return_value="Skip [S]"))):
            session.interactive_review()
        assert len(session._reviewed) == 0

    def test_change_path(self):
        classifications = [make_classification(path="a.pdf", category_path="Zeno/Docs", confidence=60)]
        session = ReviewSession(classifications, [], NAS_ROOT)
        with (
            patch("questionary.select", return_value=Mock(ask=Mock(return_value="Change path [C]"))),
            patch("questionary.path", return_value=Mock(ask=Mock(return_value="Zeno/NewFolder"))),
        ):
            session.interactive_review()
        assert session._reviewed[0]["category_path"] == "Zeno/NewFolder"

    def test_accept_item_stays(self):
        classifications = [make_classification(path="a.pdf", category_path="Zeno/Docs", confidence=60)]
        session = ReviewSession(classifications, [], NAS_ROOT)
        with patch("questionary.select", return_value=Mock(ask=Mock(return_value="Accept [A]"))):
            session.interactive_review()
        assert len(session._reviewed) == 1
        assert session._reviewed[0]["category_path"] == "Zeno/Docs"