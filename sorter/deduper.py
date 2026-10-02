"""Duplicate detection module — SHA-256 content hashing for exact dupes
and perceptual hashing for near-duplicate images. Moves dupes to staging trash."""

from pathlib import Path
from typing import List, Dict, Optional, Tuple
import hashlib
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

from sorter.scanner import FileEntry


_EXACT_MIN_SIZE = 1024       # 1 KB — skip tiny files for exact dedup
_IMAGE_MIN_SIZE = 5120       # 5 KB — skip tiny images for phash
_CHUNK_SIZE = 65536          # 64 KB
_DEFAULT_HAMMING = 8


class DedupScanner:
    def __init__(self, config: Dict):
        self.enabled = config.get('enabled', True)
        self.exact_hash = config.get('exact_hash', True)
        self.image_phash = config.get('image_phash', True)
        self.keep_newest = config.get('keep_newest', True)
        self.trash_dir = config.get('trash_dir', '_Duplicates_Delete')

    def scan_exact(self, files: List[FileEntry]) -> List[Dict]:
        if not self.enabled or not self.exact_hash:
            return []

        hash_map: Dict[str, List[FileEntry]] = defaultdict(list)

        for f in files:
            if self._should_skip(f.path, f.size, f.mime, min_size=_EXACT_MIN_SIZE):
                continue
            try:
                digest = self._sha256(f.path)
                hash_map[digest].append(f)
            except (PermissionError, OSError) as e:
                print(f"Warning: cannot hash {f.rel_path}: {e}", file=sys.stderr)

        return self._pairs_from_groups(hash_map, "exact")

    def scan_image_near_duplicates(self, files: List[FileEntry]) -> List[Dict]:
        if not self.enabled or not self.image_phash:
            return []

        image_exts = {'image/jpeg', 'image/png', 'image/heic', 'image/webp', 'image/gif'}
        image_files = [f for f in files if f.mime in image_exts
                       and not self._should_skip(f.path, f.size, f.mime, min_size=_IMAGE_MIN_SIZE)]

        if len(image_files) < 2:
            return []

        phash_map: Dict[str, List[FileEntry]] = defaultdict(list)
        for f in image_files:
            try:
                h = self._phash(f.path)
                if h is not None:
                    phash_map[h].append(f)
            except Exception:
                continue

        if len(phash_map) < 2:
            return []

        entries = list(phash_map.items())
        seen: set = set()
        pairs: List[Dict] = []

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                hash_a, files_a = entries[i]
                hash_b, files_b = entries[j]

                from imagehash import hex_to_hash
                try:
                    hamming = hex_to_hash(hash_a) - hex_to_hash(hash_b)
                except Exception:
                    continue

                if hamming <= _DEFAULT_HAMMING:
                    for fe_a in files_a:
                        for fe_b in files_b:
                            key = tuple(sorted([fe_a.rel_path, fe_b.rel_path]))
                            if key in seen:
                                continue
                            seen.add(key)
                            keep, delete = self._pick_keeper(fe_a, fe_b)
                            pairs.append({
                                "type": "near_image",
                                "keep": keep,
                                "delete": delete,
                                "reason": f"similar image (hamming: {hamming})",
                                "hamming": hamming,
                            })

        return pairs

    def scan_all(self, files: List[FileEntry]) -> List[Dict]:
        results: List[Dict] = []
        seen_delete: set = set()

        for pair in self.scan_exact(files):
            results.append(pair)
            seen_delete.add(pair["delete"].path)

        for pair in self.scan_image_near_duplicates(files):
            if pair["delete"].path not in seen_delete:
                results.append(pair)

        results.sort(key=lambda p: p["keep"].path)
        return results

    def format_for_review(self, pairs: List[Dict]) -> List[Dict]:
        formatted = []
        for p in pairs:
            keep_size = self._format_size(p["keep"].size)
            delete_size = self._format_size(p["delete"].size)
            keep_date = datetime.fromtimestamp(p["keep"].mtime).isoformat()
            delete_date = datetime.fromtimestamp(p["delete"].mtime).isoformat()
            formatted.append({
                "keep_path": p["keep"].rel_path,
                "delete_path": p["delete"].rel_path,
                "keep_size": keep_size,
                "delete_size": delete_size,
                "keep_date": keep_date,
                "delete_date": delete_date,
                **p,
            })
        return formatted

    def _sha256(self, filepath: Path) -> str:
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _phash(self, filepath: Path) -> Optional[str]:
        try:
            from PIL import Image
            import imagehash
            with Image.open(filepath) as img:
                img = img.convert('RGB')
                h = imagehash.phash(img)
                return str(h)
        except Exception:
            return None

    def _should_skip(self, path: Path, size: int, mime: str, min_size: int = _EXACT_MIN_SIZE) -> bool:
        if size < min_size:
            return True
        rel = str(path).replace('\\', '/')
        if '/_Duplicates_Delete/' in rel:
            return True
        return False

    def _pick_keeper(self, a: FileEntry, b: FileEntry) -> Tuple[FileEntry, FileEntry]:
        if self.keep_newest:
            if a.mtime >= b.mtime:
                return a, b
            return b, a
        return a, b

    def _pairs_from_groups(self, hash_map: Dict, dup_type: str) -> List[Dict]:
        pairs: List[Dict] = []
        for digest, group in hash_map.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda e: e.mtime, reverse=True)
            keeper = group[0]
            for dup in group[1:]:
                pairs.append({
                    "type": dup_type,
                    "keep": keeper,
                    "delete": dup,
                    "reason": "same content, older copy",
                })
        return pairs

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if abs(size) < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"


# ── Copy-suffix dedup (name vs name (n).ext) ──
_COPY_SUFFIX_RE = re.compile(r"^(.+) \((\d+)\)$")


def find_copy_duplicates(files: List[FileEntry]) -> List[Dict]:
    """Find Windows 'copy suffix' duplicates: name.ext and name (2).ext.

    Groups files by parent directory, matches stem-(n) pairs by exact stem.
    Returns list of pair dicts in the same format as scan_exact.
    """
    groups: Dict[Path, List[FileEntry]] = defaultdict(list)
    for f in files:
        groups[f.path.parent].append(f)

    seen: set = set()
    pairs: List[Dict] = []

    for parent, group in groups.items():
        # Build map: base_stem → list of entries
        base_map: Dict[str, List[FileEntry]] = defaultdict(list)
        copy_map: Dict[str, List[FileEntry]] = defaultdict(list)

        for entry in group:
            m = _COPY_SUFFIX_RE.match(Path(entry.name).stem)
            if m:
                base_stem = m.group(1)
                copy_map[base_stem].append(entry)
            else:
                base_map[Path(entry.name).stem].append(entry)

        for base_stem, originals in base_map.items():
            if base_stem not in copy_map:
                continue
            copies = copy_map[base_stem]
            for orig in originals:
                for copy_entry in copies:
                    if copy_entry.size == orig.size or copy_entry.mtime == orig.mtime:
                        # Same content → dedup
                        key = tuple(sorted([orig.rel_path, copy_entry.rel_path]))
                        if key in seen:
                            continue
                        seen.add(key)
                        # Keep the original (no "(n)" suffix), delete the copy
                        from sorter.executor import compute_file_hash
                        pairs.append({
                            "type": "copy_suffix",
                            "keep": orig,
                            "delete": copy_entry,
                            "reason": f"copy suffix: {copy_entry.name} is duplicate of {orig.name}",
                        })
                    # Also flag (n) copies without exact-size match as potential dedup
                    else:
                        # Different size — just note as possible same-content
                        key = tuple(sorted([orig.rel_path, copy_entry.rel_path]))
                        if key in seen:
                            continue
                        seen.add(key)
                        pairs.append({
                            "type": "copy_suffix_size_diff",
                            "keep": orig,
                            "delete": copy_entry,
                            "reason": f"copy suffix (size differs): {copy_entry.name} vs {orig.name}",
                        })

    return pairs