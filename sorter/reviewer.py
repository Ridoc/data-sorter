from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.panel import Panel
from rich import box

console = Console()


GREEN_STYLE = Style(dim=True, color="green")
YELLOW_STYLE = Style(bold=True, color="yellow")
RED_STYLE = Style(dim=True, color="red")
DELETE_STYLE = Style(strike=True, color="red")
UNSORTED_LABEL = "_Unsorted_Review"
CONF_AUTO_ACCEPT = 85
CONF_REQUIRE_REVIEW = 50


class ReviewSession:
    def __init__(
        self,
        classifications: List[Dict],
        dedup_pairs: List[Dict],
        nas_root: Path,
        dry_run: bool = False,
    ):
        self.classifications = classifications
        self.dedup_pairs = dedup_pairs
        self.nas_root = nas_root
        self.dry_run = dry_run
        self._approved_moves: List[Dict] = []
        self._approved_deletions: List[Dict] = []
        self._reviewed: List[Dict] = []

    def run(self) -> Tuple[List[Dict], List[Dict]]:
        if not self.classifications and not self.dedup_pairs:
            console.print(Panel("[bold yellow]Nothing to do[/]", title="Review"))
            return ([], [])

        if self.dedup_pairs:
            self.show_dedup_pairs()

        if self.classifications:
            tree = self.build_classification_tree(self.classifications)
            console.print(Panel(tree, title="Classification Preview", border_style="blue"))
            self.interactive_review()

        self.show_summary_table()
        if self.confirm_execution():
            return (self.get_approved_moves(), self.get_approved_deletions())
        return ([], [])

    def show_dedup_pairs(self) -> None:
        table = Table(
            title="Duplicate Files",
            box=box.ROUNDED,
            header_style="bold magenta",
        )
        table.add_column("Type", width=10)
        table.add_column("Keep", width=30)
        table.add_column("Delete", width=30)
        table.add_column("Keep Size", width=10)
        table.add_column("Delete Size", width=10)
        table.add_column("Reason", width=30)

        for pair in self.dedup_pairs:
            table.add_row(
                pair.get("type", "?"),
                pair.get("keep_path", ""),
                pair.get("delete_path", ""),
                pair.get("keep_size", ""),
                pair.get("delete_size", ""),
                pair.get("reason", ""),
            )

        console.print(Panel(table, title="Duplicate Detection"))

        for pair in self.dedup_pairs:
            from questionary import confirm

            keep_both = confirm(
                f"Keep BOTH [yellow]{pair['keep_path']}[/] and [red]{pair['delete_path']}[/]?",
                default=False,
            ).ask()
            if keep_both is None:
                keep_both = False
            if not keep_both:
                self._approved_deletions.append({
                    "path": str(self.nas_root / pair["delete_path"]),
                    "type": "duplicate",
                    "reason": pair.get("reason", "duplicate"),
                })

    def build_classification_tree(self, classifications: List[Dict]) -> Tree:
        root = Tree("📁 NAS Files")
        grouped: Dict[str, List[Dict]] = {}

        for item in classifications:
            cat = item.get("category_path", UNSORTED_LABEL)
            confidence = item.get("confidence", 0)
            action = item.get("action", "move")
            if confidence < CONF_REQUIRE_REVIEW or action == "delete":
                display_cat = UNSORTED_LABEL
            else:
                display_cat = cat
            top = display_cat.split("/")[0]
            grouped.setdefault(top, []).append(item)

        for top_level in sorted(grouped):
            folder_label = f"📁 {top_level}/"
            folder_node = root.add(folder_label)
            cat_subs: Dict[str, List[Dict]] = {}
            for item in grouped[top_level]:
                cat = item.get("category_path", UNSORTED_LABEL)
                confidence = item.get("confidence", 0)
                action = item.get("action", "move")
                if confidence < CONF_REQUIRE_REVIEW or action == "delete":
                    display_cat = UNSORTED_LABEL
                else:
                    display_cat = cat
                cat_subs.setdefault(display_cat, []).append(item)
            for cat_name in sorted(cat_subs):
                items = cat_subs[cat_name]
                cat_label = f"  📁 {cat_name.split('/', 1)[-1]}/"
                cat_node = folder_node.add(cat_label)
                for item in items:
                    path = item.get("path", "?")
                    confidence = item.get("confidence", 0)
                    reason = item.get("reason", "")
                    action = item.get("action", "move")
                    is_folder = item.get("is_folder", False)
                    folder_move = item.get("_folder_move", {})
                    file_count = folder_move.get("file_count", 0) if folder_move else 0
                    if is_folder and folder_move:
                        from pathlib import Path
                        target_name = Path(folder_move["target"]).name
                        display = f"{path.split('/')[-1]} → {target_name}"
                    else:
                        display = path
                    label = self._format_file_label(display, confidence, reason, action,
                                                    is_folder=is_folder, file_count=file_count)
                    cat_node.add(label)
        return root

    def _format_file_label(
        self, path: str, confidence: int, reason: str, action: str = "move",
        is_folder: bool = False, file_count: int = 0,
    ) -> Text:
        if is_folder:
            file_str = f" ({file_count} files)" if file_count else ""
            return Text(f"📁 {path}/{file_str}  ({confidence}%) — {reason}", style=Style(bold=True, color="cyan"))
        if action == "delete":
            return Text(f"🗑️ {path} ({reason})", style=DELETE_STYLE)
        if confidence >= CONF_AUTO_ACCEPT:
            icon = "🟢"
            style = GREEN_STYLE
        elif confidence >= CONF_REQUIRE_REVIEW:
            icon = "🟡"
            style = YELLOW_STYLE
        else:
            icon = "🔴"
            style = RED_STYLE
        return Text(f"{icon} {path}  ({confidence}%)", style=style)

    def interactive_review(self) -> None:
        yellow_items: Dict[str, List[Dict]] = {}
        for item in self.classifications:
            confidence = item.get("confidence", 0)
            action = item.get("action", "move")
            if (
                CONF_REQUIRE_REVIEW <= confidence < CONF_AUTO_ACCEPT
                and action == "move"
            ):
                cat = item.get("category_path", UNSORTED_LABEL)
                yellow_items.setdefault(cat, []).append(item)

        if not yellow_items:
            console.print("[dim]No items require review.[/]")
            self._reviewed = list(self.classifications)
            return

        from questionary import select

        for cat, items in sorted(yellow_items.items()):
            console.print(f"\n[bold yellow]📁 {cat}/[/] — {len(items)} item(s) need review")
            for item in items:
                path = item.get("path", "?")
                confidence = item.get("confidence", 0)
                reason = item.get("reason", "")
                choice = select(
                    message=f"[yellow]{path}[/] ({confidence}%) — {reason}",
                    choices=[
                        "Accept [A]",
                        "Change path [C]",
                        "Skip [S]",
                    ],
                    default="Accept [A]",
                ).ask()
                if choice is None:
                    choice = "Accept [A]"
                if choice.startswith("Change"):
                    from questionary import path as qpath
                    new_cat = qpath(
                        "New category path:",
                        default=cat,
                    ).ask()
                    if new_cat is None:
                        new_cat = cat
                    item["category_path"] = new_cat
                elif choice.startswith("Skip"):
                    item["_skipped"] = True
        self._reviewed = [
            item for item in self.classifications if not item.get("_skipped")
        ]

    def show_summary_table(self) -> Table:
        total = len(self.classifications) if self.classifications else 0
        move_count = 0
        delete_count = 0
        unsorted_count = 0
        for item in self._reviewed or self.classifications or []:
            action = item.get("action", "move")
            confidence = item.get("confidence", 0)
            if action == "delete":
                delete_count += 1
            elif confidence < CONF_REQUIRE_REVIEW:
                unsorted_count += 1
            else:
                move_count += 1

        table = Table(
            title="Review Summary",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Metric", style="bold")
        table.add_column("Count")
        table.add_row("Total files reviewed", str(total))
        table.add_row("Files to move", str(move_count))
        table.add_row("Files to delete (Adobe Premiere temps)", str(delete_count))
        table.add_row("Duplicates to delete", str(len(self._approved_deletions)))
        table.add_row(f"Files to {UNSORTED_LABEL}", str(unsorted_count))

        console.print(Panel(table, title="Summary"))
        return table

    def confirm_execution(self) -> bool:
        move_count = sum(
            1
            for item in (self._reviewed or self.classifications or [])
            if item.get("action", "move") != "delete"
            and item.get("confidence", 0) >= CONF_REQUIRE_REVIEW
        )
        delete_count = (
            sum(
                1
                for item in (self._reviewed or self.classifications or [])
                if item.get("action") == "delete"
            )
            + len(self._approved_deletions)
        )

        summary = (
            f"[bold]Files to move:[/] {move_count}\n"
            f"[bold]Files to delete:[/] {delete_count}\n"
            f"[bold]Duplicates to delete:[/] {len(self._approved_deletions)}\n"
        )
        if self.dry_run:
            summary += "\n[yellow]DRY RUN — no files will be changed[/]"
        console.print(Panel(summary, title="Execution Plan"))

        from questionary import confirm
        result = confirm("Proceed with these changes?", default=False).ask()
        return bool(result)

    def get_approved_moves(self) -> List[Dict]:
        moves = []
        for item in self._reviewed or self.classifications or []:
            action = item.get("action", "move")
            if action == "delete":
                continue
            # Folder moves
            if item.get("is_folder") and item.get("_folder_move"):
                moves.append(item["_folder_move"])
                continue
            confidence = item.get("confidence", 0)
            if confidence < CONF_REQUIRE_REVIEW:
                continue
            moves.append({
                "source": str(self.nas_root / item["path"]),
                "target": str(self.nas_root / item["category_path"] / Path(item["path"]).name),
                "confidence": confidence,
                "reason": item.get("reason", ""),
            })
        return moves

    def get_approved_deletions(self) -> List[Dict]:
        deletions = list(self._approved_deletions)
        for item in self._reviewed or self.classifications or []:
            if item.get("action") == "delete":
                deletions.append({
                    "path": str(self.nas_root / item["path"]),
                    "type": "adobe_temp",
                    "reason": item.get("reason", "Adobe Premiere temp file"),
                })
        return deletions