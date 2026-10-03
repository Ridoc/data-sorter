"""Undo module — reads CSV log and reverses file moves."""

from pathlib import Path
from typing import List, Dict, Optional
import csv
import sys
import hashlib

from sorter.executor import ensure_dir, compute_file_hash


_CHUNK_SIZE = 65536


def _within_root(path: Path, root: Path) -> bool:
    """True if path resolves inside root.

    WHY: the undo CSV is UNTRUSTED INPUT — a fixed, append-only filename anyone
    with write access to .sort_logs/ can append to. Without containment, a
    crafted row makes --undo move files to or from anywhere on the filesystem.
    """
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _verify_file_hash_impl(path: Path, expected_hash: str) -> bool:
    if not path.exists():
        return False
    try:
        actual = compute_file_hash(path)
        return actual == expected_hash
    except (PermissionError, OSError):
        return False


def parse_undo_log(csv_path: Path) -> List[Dict]:
    records: List[Dict] = []
    if not csv_path.exists():
        print(f"Warning: undo log not found: {csv_path}", file=sys.stderr)
        return records
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(dict(row))
    return records


def verify_file_hash(path: Path, expected_hash: str) -> bool:
    return _verify_file_hash_impl(path, expected_hash)


def reverse_move(record: Dict, dry_run: bool = False) -> bool:
    source_path = Path(record.get("source_path", ""))
    target_path = Path(record.get("target_path", ""))
    action = record.get("action", "move")

    # ONLY "move" and "delete" are reversible. Aggregate rows ("folder_move",
    # "dissolve_summary") point at a SHARED directory this run did not create —
    # reversing one would relocate an entire category, not restore a folder.
    if action not in ("move", "delete"):
        print(f"Warning: cannot undo action '{action}' (skipping {target_path})",
              file=sys.stderr)
        return False

    if action == "delete":
        if not target_path.exists():
            print(f"Warning: trash path not found: {target_path}", file=sys.stderr)
            return False
        if dry_run:
            return True
        ensure_dir(source_path.parent)
        try:
            shutil_move(str(target_path), str(source_path))
            return True
        except (PermissionError, OSError, ValueError) as e:
            print(f"Error undoing delete {target_path} -> {source_path}: {e}", file=sys.stderr)
            return False

    if not target_path.exists():
        print(f"Warning: target not found: {target_path}", file=sys.stderr)
        return False
    if dry_run:
        return True
    ensure_dir(source_path.parent)
    try:
        shutil_move(str(target_path), str(source_path))
        return True
    except (PermissionError, OSError, ValueError) as e:
        print(f"Error reversing move {target_path} -> {source_path}: {e}", file=sys.stderr)
        return False


def shutil_move(src: str, dst: str) -> None:
    import shutil as _shutil
    if Path(dst).exists():
        stem = Path(dst).stem
        suffix = Path(dst).suffix
        counter = 1
        while Path(dst).exists():
            dst = str(Path(dst).with_name(f"{stem}_{counter}{suffix}"))
            counter += 1
    _shutil.move(src, dst)


def undo_last_run(csv_path: Path, nas_root: Path, dry_run: bool = False) -> Dict:
    records = parse_undo_log(csv_path)
    reverted = 0
    skipped = 0
    failed = 0

    for record in reversed(records):
        # Aggregate trace rows (folder_move / dissolve_summary) have no per-item
        # counterpart to restore — counting them as failures would report a clean
        # undo as broken. reverse_move refuses them too (defence in depth).
        if record.get("action", "move") not in ("move", "delete"):
            skipped += 1
            continue

        target_path = Path(record.get("target_path", ""))
        expected_hash = record.get("source_hash", "")
        source_path = Path(record.get("source_path", ""))

        # Containment: the CSV is untrusted, so neither end of the move may leave
        # the NAS root. Reverse-symlink resolution happens inside _within_root.
        # source_path.PARENT must be contained too: shutil_move resolves collisions by
        # appending "_N" in the destination's own parent, so a row whose source_path IS
        # the root would otherwise land at <root>_1, one level outside it.
        if (not _within_root(target_path, nas_root)
                or not _within_root(source_path, nas_root)
                or not _within_root(source_path.parent, nas_root)):
            print(f"Warning: undo path escapes NAS root — refusing "
                  f"{source_path} <-> {target_path}", file=sys.stderr)
            skipped += 1
            continue

        # An empty or "." source_path makes shutil_move raise ValueError (Path('.') has
        # an empty name), which would abort the entire run. Reject it here instead.
        if not str(source_path).strip() or source_path.name in ("", "."):
            print(f"Warning: undo row has no usable source path — refusing "
                  f"{target_path}", file=sys.stderr)
            skipped += 1
            continue

        # A relocated FILE with no recorded hash has no integrity check: the user
        # may have edited it at its destination and undo would silently discard that.
        # Scoped to action="move" on purpose — trash restores ("delete") never carried
        # a hash, and are already covered by the containment check above. Dirs move as
        # units and legitimately carry "".
        if record.get("action", "move") == "move" and not expected_hash and target_path.is_file():
            print(f"Warning: no hash recorded for {target_path} — refusing to restore",
                  file=sys.stderr)
            skipped += 1
            continue

        if expected_hash and target_path.exists():
            match = verify_file_hash(target_path, expected_hash)
            if not match:
                print(f"Warning: file hash mismatch for {target_path} — file may have changed",
                      file=sys.stderr)
                skipped += 1
                continue

        ok = reverse_move(record, dry_run=dry_run)
        if ok:
            reverted += 1
        else:
            failed += 1

    return {"reverted": reverted, "skipped": skipped, "failed": failed}


def get_last_run_path(nas_root: Path) -> Optional[Path]:
    log_dir = nas_root / ".sort_logs"
    if not log_dir.exists():
        return None
    csv_files = sorted(log_dir.glob("sort_undo*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csv_files:
        return None
    return csv_files[0]