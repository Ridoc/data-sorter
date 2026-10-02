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


def _move_folder(source: Path, target: Path, dry_run: bool = False) -> bool:
    """Move an entire directory tree, creating parent dirs as needed."""
    if not source.exists():
        print(f"Error: source folder not found: {source}", file=sys.stderr)
        return False
    if dry_run:
        return True
    ensure_dir(target.parent)
    try:
        if target.exists():
            stem = target.stem
            parent = target.parent
            counter = 1
            while target.exists():
                target = parent / f"{stem}_{counter}"
                counter += 1
        shutil.move(str(source), str(target))
        return True
    except (PermissionError, OSError) as e:
        print(f"Error moving folder {source} -> {target}: {e}", file=sys.stderr)
        return False


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
            ok = _move_folder(source, target, dry_run=dry_run)
            if ok:
                moved += 1
            else:
                failed += 1
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