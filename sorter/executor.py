"""File execution module — moves files to target locations, moves dupes to
staging trash, logs all operations to CSV for undo."""

from pathlib import Path
from typing import List, Dict, Optional
import csv
import shutil
import hashlib
import os
import sys
from datetime import datetime


_CHUNK_SIZE = 65536


def resolve_dir_target(target: Path) -> Path:
    """The path a folder move will ACTUALLY land on.

    WHY separate: `_move_folder` used to resolve the collision into its own local
    variable, so callers that logged undo rows beforehand recorded the intended
    destination. On a name clash that path never exists, `--undo` cannot find the
    file, and the moved folder is stranded at the bumped name. Both the mover and
    the undo-row builder must agree, so the bump lives here.
    """
    if not target.exists():
        return target
    stem, parent, counter = target.stem, target.parent, 1
    while target.exists():
        target = parent / f"{stem}_{counter}"
        counter += 1
    return target


def _move_folder(source: Path, target: Path, dry_run: bool = False) -> Optional[Path]:
    """Move an entire directory tree. Returns the ACTUAL destination, or None."""
    if not source.exists():
        print(f"Error: source folder not found: {source}", file=sys.stderr)
        return None
    if dry_run:
        return resolve_dir_target(target)
    ensure_dir(target.parent)
    try:
        target = resolve_dir_target(target)
        shutil.move(str(source), str(target))
        return target
    except (PermissionError, OSError) as e:
        print(f"Error moving folder {source} -> {target}: {e}", file=sys.stderr)
        return None


def folder_move_records(source: Path, target: Path) -> List[Dict]:
    """Per-file records for a whole-directory move.

    WHY: SAFETY_INVARIANT #2 -- every file that moves needs its own undo row. A single
    aggregate row cannot be reversed safely, because `reverse_move` cannot know whether
    the destination directory was created by this run or already held the user's data
    (moving it back wholesale would relocate the entire category).
    """
    records: List[Dict] = []
    if not source.exists():
        return records
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        records.append({
            "source": str(path),
            "target": str(target / path.relative_to(source)),
            "is_dir": False,
            "hash": compute_file_hash(path),
        })
    return records


def _dissolve_folder(source: Path, target_dir: Path, dry_run: bool = False) -> List[Dict]:
    """Move a folder's children directly into target_dir, then drop the folder.

    WHY: a folder named only after the medium ("bilder&screens") carries no
    subject of its own; moving it as a unit leaves a media-word folder inside
    the category. Children (incl. nested dirs) move out, so rmdir succeeds.

    Returns the ACTUAL moves performed — post-collision destinations with the
    pre-move hash — so the caller can log one undo record per child. A single
    summary record would make --undo relocate the whole target category.
    """
    if not source.exists():
        print(f"Error: source folder not found: {source}", file=sys.stderr)
        return []
    moved: List[Dict] = []
    if not dry_run:
        ensure_dir(target_dir)  # dry-run must touch nothing, not even mkdir
    for child in sorted(source.iterdir()):
        dest = target_dir / child.name
        try:
            if dest.exists() and dest.is_dir() != child.is_dir():
                continue  # never merge a file onto a dir or vice versa
            if dest.exists():
                # Suffix MUST be re-attached: "a.jpg" -> "a_1.jpg", not "a_1"
                stem, suffix, parent = dest.stem, dest.suffix, dest.parent
                counter = 1
                while dest.exists():
                    dest = parent / f"{stem}_{counter}{suffix}"
                    counter += 1
            is_dir = child.is_dir()
            # Hash BEFORE the move: undo_last_run verifies it and the file is gone after
            digest = ""
            if not is_dir and not dry_run:
                try:
                    digest = compute_file_hash(child)
                except (PermissionError, OSError) as e:
                    print(f"Warning: cannot hash {child}: {e}", file=sys.stderr)
            if not dry_run:
                shutil.move(str(child), str(dest))
            moved.append({"source": str(child), "target": str(dest),
                          "is_dir": is_dir, "hash": digest})
        except (PermissionError, OSError) as e:
            # Isolate: one bad child must not discard the records of those that moved
            print(f"Error dissolving {child} -> {dest}: {e}", file=sys.stderr)
            continue
    if not dry_run:
        try:
            source.rmdir()
        except OSError as e:
            # Non-fatal: leftovers stay visible instead of the whole batch being voided
            print(f"Warning: {source} not empty after dissolve ({e}); left in place",
                  file=sys.stderr)
    return moved


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def move_file(source: Path, target: Path, dry_run: bool = False) -> bool:
    if not source.exists():
        print(f"Error: source not found: {source}", file=sys.stderr)
        return False
    if dry_run:
        return True
    ensure_dir(target.parent)
    try:
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while target.exists():
                target = target.with_name(f"{stem}_{counter}{suffix}")
                counter += 1
        shutil.move(str(source), str(target))
        return True
    except (PermissionError, OSError) as e:
        print(f"Error moving {source} -> {target}: {e}", file=sys.stderr)
        return False


def move_to_trash(source: Path, trash_root: Path, dry_run: bool = False) -> Optional[Path]:
    if not source.exists():
        print(f"Error: source not found: {source}", file=sys.stderr)
        return None
    if dry_run:
        return trash_root / "_Duplicates_Delete" / source.name
    try:
        trash_dir = ensure_dir(trash_root / "_Duplicates_Delete")
        rel = source.relative_to(source.anchor) if source.is_absolute() else source
        target = trash_dir / rel
        ensure_dir(target.parent)
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while target.exists():
                target = target.with_name(f"{stem}_{counter}{suffix}")
                counter += 1
        shutil.move(str(source), str(target))
        return target
    except (PermissionError, OSError) as e:
        print(f"Error moving to trash {source}: {e}", file=sys.stderr)
        return None


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def log_move(csv_path: Path, record: Dict) -> None:
    ensure_dir(csv_path.parent)
    write_header = not csv_path.exists()
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "timestamp", "source_path", "target_path", "confidence",
                "source_hash", "action", "reason", "original_name"
            ])
        writer.writerow([
            record.get("timestamp", datetime.now().isoformat()),
            record.get("source_path", ""),
            record.get("target_path", ""),
            record.get("confidence", ""),
            record.get("source_hash", ""),
            record.get("action", "move"),
            record.get("reason", ""),
            record.get("original_name", ""),
        ])


def execute_moves(
    approved_moves: List[Dict],
    approved_deletions: List[Dict],
    nas_root: Path,
    csv_path: Path,
    trash_root: Path,
    dry_run: bool = False,
) -> Dict:
    moved = 0
    deleted = 0
    failed = 0

    for move in approved_moves:
        source = Path(move["source"])
        target = Path(move["target"])
        
        # Handle folder moves (is_folder = True)
        if move.get("is_folder"):
            if move.get("_dissolve"):
                records = _dissolve_folder(source, target, dry_run=dry_run)
                if not records:
                    failed += 1
                for rec in records:
                    moved += 1
                    log_move(csv_path, {
                        "source_path": rec["source"],
                        "target_path": rec["target"],
                        "confidence": move.get("confidence", ""),
                        "source_hash": rec["hash"],
                        "action": "move",
                        "reason": f"Dissolved from {source.name}",
                        "original_name": Path(rec["source"]).name,
                    })
                # Trace row only: its target is a SHARED category dir, so reverse_move
                # must refuse it (see the action whitelist in undo.reverse_move).
                log_move(csv_path, {
                    "source_path": str(source),
                    "target_path": str(target),
                    "confidence": move.get("confidence", ""),
                    "source_hash": "",
                    "action": "dissolve_summary",
                    "reason": f"dissolve of {len(records)} children",
                    "original_name": "",
                })
                continue
            # Records must name the REAL destination (collision already resolved),
            # or --undo looks for a path that never existed.
            actual = resolve_dir_target(target)
            records = folder_move_records(source, actual)
            ok = _move_folder(source, actual, dry_run=dry_run)
            if ok:
                moved += 1
            else:
                failed += 1
            for rec in records:
                log_move(csv_path, {
                    "source_path": rec["source"],
                    "target_path": rec["target"],
                    "confidence": move.get("confidence", ""),
                    "source_hash": rec["hash"],
                    "action": "move",
                    "reason": f"Moved with folder {source.name}",
                    "original_name": Path(rec["source"]).name,
                })
            log_move(csv_path, {
                "source_path": str(source),
                "target_path": str(target),
                "confidence": move.get("confidence", ""),
                "source_hash": "",
                "action": "folder_move",
                "reason": move.get("reason", f"Folder move ({move.get('file_count', '?')} files)"),
                "original_name": "",
            })
            continue
        
        source_hash = ""
        if source.exists() and not dry_run:
            try:
                source_hash = compute_file_hash(source)
            except (PermissionError, OSError) as e:
                print(f"Warning: cannot hash {source}: {e}", file=sys.stderr)

        ok = move_file(source, target, dry_run=dry_run)
        if ok:
            moved += 1
        else:
            failed += 1

        # Log original name if renamed
        suggested = move.get("suggested_name")
        original_name = Path(source).name if suggested else ""

        log_move(csv_path, {
            "source_path": str(source),
            "target_path": str(target),
            "confidence": move.get("confidence", ""),
            "source_hash": source_hash,
            "action": "move",
            "reason": move.get("reason", ""),
            "original_name": original_name,
        })

    for deletion in approved_deletions:
        path = Path(deletion["path"])
        trash_path = move_to_trash(path, trash_root, dry_run=dry_run)
        if trash_path is not None:
            deleted += 1
        else:
            failed += 1

        source_hash = ""
        if path.exists() and not dry_run:
            try:
                source_hash = compute_file_hash(path)
            except (PermissionError, OSError) as e:
                print(f"Warning: cannot hash {path}: {e}", file=sys.stderr)

        log_move(csv_path, {
            "source_path": str(path),
            "target_path": str(trash_path) if trash_path else "",
            "confidence": "",
            "source_hash": source_hash,
            "action": "delete",
            "reason": deletion.get("reason", ""),
        })

    return {"moved": moved, "deleted": deleted, "failed": failed, "csv_path": str(csv_path)}


def dry_run_summary(approved_moves: List[Dict], approved_deletions: List[Dict]) -> Dict:
    to_move = 0
    to_delete = len(approved_deletions)
    space_freed = 0
    for move in approved_moves:
        if move.get("is_folder"):
            # Folder moves: count files and use precomputed total_size
            to_move += move.get("file_count", 0)
            space_freed += move.get("total_size", 0)
        else:
            to_move += 1
            p = Path(move["source"])
            if p.exists():
                try:
                    space_freed += p.stat().st_size
                except OSError:
                    pass
    for d in approved_deletions:
        p = Path(d["path"])
        if p.exists():
            try:
                space_freed += p.stat().st_size
            except OSError:
                pass
    return {"to_move": to_move, "to_delete": to_delete, "space_freed_estimate": space_freed}