"""Undo module — reads CSV log and reverses file moves."""

from pathlib import Path
from typing import List, Dict, Optional
import csv
import sys
import hashlib

from sorter.executor import ensure_dir, compute_file_hash


_CHUNK_SIZE = 65536


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
        except (PermissionError, OSError) as e:
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
    except (PermissionError, OSError) as e:
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
        target_path = Path(record.get("target_path", ""))
        expected_hash = record.get("source_hash", "")

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