"""File discovery module — walks NAS directory tree, respects ignore patterns,
returns structured FileEntry records with MIME type detection."""

from typing import List, NamedTuple, Optional, Pattern
from pathlib import Path
import os
import re
import sys


class FileEntry(NamedTuple):
    """Represents a discovered file with metadata."""
    path: Path
    rel_path: str
    name: str
    mime: str
    size: int
    mtime: float          # last modification time (epoch)
    is_dir: bool
    ctime: float = 0.0    # creation time (epoch)
    atime: float = 0.0    # last access time (epoch)


def age_string(timestamp: float, now: Optional[float] = None) -> str:
    """Human-readable age from epoch timestamp, e.g. '12.4 years'."""
    if timestamp <= 0:
        return "unknown"
    if now is None:
        now = __import__('time').time()
    seconds = now - timestamp
    if seconds < 0:
        return "future"
    years = seconds / (365.25 * 86400)
    if years >= 2:
        return f"{years:.1f} years"
    days = seconds / 86400
    if days >= 2:
        return f"{int(days)} days"
    hours = seconds / 3600
    if hours >= 2:
        return f"{int(hours)} hours"
    return f"{int(seconds)} seconds"


_EXT_MIME = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.doc': 'application/msword',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.heic': 'image/heic',
    '.mp4': 'video/mp4',
    '.mov': 'video/quicktime',
    '.avi': 'video/x-msvideo',
    '.wmv': 'video/x-ms-wmv',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.flac': 'audio/flac',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.yaml': 'text/yaml',
    '.yml': 'text/yaml',
    '.json': 'application/json',
    '.py': 'text/x-python',
    '.html': 'text/html',
    '.csv': 'text/csv',
    '.vcf': 'text/vcard',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.xls': 'application/vnd.ms-excel',
    '.zip': 'application/zip',
    '.aup': 'application/octet-stream',
    '.prproj': 'application/octet-stream',
}


def _glob_to_regex(pattern: str) -> Pattern:
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '*':
            parts.append('[^/]*')
        elif c == '?':
            parts.append('[^/]')
        elif c == '.':
            parts.append('\\.')
        else:
            parts.append(re.escape(c))
        i += 1
    return re.compile('^' + ''.join(parts) + '$')


def load_ignore_patterns(ignore_file: Path) -> List[Pattern]:
    if not ignore_file.exists():
        return []

    patterns: List[Pattern] = []
    try:
        text = ignore_file.read_text(encoding='utf-8', errors='replace')
    except PermissionError:
        print(f"Warning: cannot read ignore file {ignore_file}", file=sys.stderr)
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            patterns.append(_glob_to_regex(line))
        except re.error:
            print(f"Warning: invalid ignore pattern {line!r}", file=sys.stderr)
    return patterns


def get_mime(path: Path) -> str:
    ext = path.suffix.lower()
    known = _EXT_MIME.get(ext)
    if known:
        return known
    try:
        import magic
        return magic.from_file(str(path), mime=True)
    except Exception:
        pass
    return 'application/octet-stream'


def matches_ignore(path: Path, patterns: List[Pattern]) -> bool:
    rel = str(path).replace('\\', '/')
    parts = rel.split('/')
    for pat in patterns:
        for segment in parts:
            if pat.search(segment):
                return True
        if pat.search(rel):
            return True
    return False


def walk_nas(root: Path, ignore_patterns: List[Pattern], max_depth: Optional[int] = None) -> List[FileEntry]:
    root = root.resolve()
    results: List[FileEntry] = []

    root_str = str(root).replace('\\', '/')
    for dirpath_str, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirpath = Path(dirpath_str)

        rel_dir = dirpath_str.replace('\\', '/')
        if rel_dir.startswith(root_str):
            rel_dir = rel_dir[len(root_str):].lstrip('/')
        else:
            rel_dir = ''

        depth = 0 if rel_dir == '' else rel_dir.count('/') + 1

        if max_depth is not None and depth > max_depth:
            dirnames.clear()
            continue

        filtered_dirs = []
        for d in dirnames:
            sub_path = dirpath / d
            if ignore_patterns and matches_ignore(sub_path, ignore_patterns):
                continue
            filtered_dirs.append(d)
        dirnames[:] = filtered_dirs

        for fname in filenames:
            fpath = dirpath / fname
            if ignore_patterns and matches_ignore(fpath, ignore_patterns):
                continue

            try:
                stat = fpath.stat()
                mime = get_mime(fpath)
                if rel_dir:
                    rel_path = f'{rel_dir}/{fname}'
                else:
                    rel_path = fname
                entry = FileEntry(
                    path=fpath.resolve(),
                    rel_path=rel_path,
                    name=fname,
                    mime=mime,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    is_dir=False,
                    ctime=getattr(stat, 'st_ctime', 0.0),
                    atime=getattr(stat, 'st_atime', 0.0),
                )
                results.append(entry)
            except PermissionError:
                print(f"Warning: permission denied {fpath}", file=sys.stderr)
            except OSError as e:
                print(f"Warning: skipping {fpath}: {e}", file=sys.stderr)

    results.sort(key=lambda e: e.path)
    return results