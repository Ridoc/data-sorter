"""Folder-level classification — groups files by parent directory and
classifies cohesive user-created folders as a unit instead of scattering
their files across categories."""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from sorter.scanner import FileEntry
from sorter.language import leaf_language_mismatch


# Temp/system directory names that should NOT be treated as cohesive folders
_TEMP_DIR_NAMES = {
    "audacity_temp", "hypnose_data", "node_modules", "__pycache__",
    ".git", ".svn", ".venv", "adobe premiere pro auto-save",
    "adobe premiere pro preview files", "conformed audio files",
    ".thumbnails", ".trash-1000",
}


def _is_temp_or_generated_dir(dir_name: str) -> bool:
    """Check if a directory is auto-generated temp content."""
    return dir_name.lower() in _TEMP_DIR_NAMES


def _dir_has_meaningful_name(dir_name: str) -> bool:
    """Check if a directory name looks user-created vs auto-generated."""
    name = dir_name.lower().strip()
    # Skip pure hex/numbers like 'e00', 'd00', 'd01', '100ANDRO'
    if name.replace("0x", "").isalnum() and len(name) < 6:
        return False
    # Skip temp/system markers
    if name.startswith(".") or name.startswith("_"):
        return False
    return True


def _is_junk_segment(name: str) -> bool:
    """Check if a path segment should be dropped from ancestor context.

    Junk = temp dir, system marker, too short, or prefixed with 'old_'.
    Does NOT filter by _dir_has_meaningful_name (which is for identifying
    cohesive folders, not ancestor segments — short names like "EVE" or
    "FC" are legitimate ancestor context).
    """
    if not name:
        return True
    n = name.strip()
    if not n:
        return True
    if _is_temp_or_generated_dir(n):
        return True
    if n.startswith(".") or n.startswith("_") or n.startswith("-"):
        return True
    if len(n) <= 2:
        return True
    if n.startswith("old"):
        return True
    return False


def meaningful_ancestors(rel_path: str, leaf_name: str, scan_root: str = "") -> List[str]:
    """Extract meaningful ancestor segments from a folder's relative path,
    dropping junk segments (temp dirs, short names, old_* prefixes, etc.)
    and the first segment if it matches scan_root.

    Args:
        rel_path: folder's relative path (e.g. "Diverse Daten/EVE/...")
        leaf_name: folder's own name (e.g. "bilder&screens")
        scan_root: the scan root directory name to drop (e.g. "Diverse Daten").
                   If empty, no segment is dropped for matching.

    Example:
      Diverse Daten/EVE/-Y-/old_G_files/bilder&screens
      with scan_root="Diverse Daten", leaf="bilder&screens"
      -> ['EVE']

      Ausbildung/Schule/Projekt Müller
      with scan_root="Diverse Daten", leaf="Projekt Müller"
      -> ['Ausbildung', 'Schule']

      Diverse Daten/Album
      with scan_root="Diverse Daten", leaf="Album"
      -> []  (no meaningful ancestors between root and leaf)
    """
    segments = [s for s in rel_path.replace("\\", "/").split("/") if s]
    if not segments:
        return []

    # Drop segments that match the leaf (leaf tracked separately)
    leaf_lower = leaf_name.lower()
    while segments and segments[-1].lower() == leaf_lower:
        segments.pop()

    # Drop first segment if it matches scan_root
    if scan_root and segments and segments[0].lower() == scan_root.lower():
        segments.pop(0)

    # Filter out junk
    return [s for s in segments if not _is_junk_segment(s)]


def group_files_by_directory(files: List[FileEntry]) -> Dict[Path, List[FileEntry]]:
    """Group FileEntry items by their immediate parent directory."""
    groups: Dict[Path, List[FileEntry]] = defaultdict(list)
    for f in files:
        parent = f.path.parent
        groups[parent].append(f)
    return dict(groups)


def identify_cohesive_folders(
    files: List[FileEntry],
    min_files: int = 2,
    nas_root: Optional[Path] = None,
) -> Tuple[Dict[Path, List[FileEntry]], List[FileEntry]]:
    """Split files into cohesive-folder groups vs remaining individual files.
    
    FIRST: files sharing a project root (detected via context.project_key)
    are grouped into ONE unit regardless of nesting depth.
    THEN: remaining files grouped by immediate parent directory.
    
    A "cohesive folder" is a user-created directory with ≥ min_files files,
    a meaningful name, and not a temp/generated directory.
    
    Returns:
        (cohesive_folders, remaining_files)
        cohesive_folders: dict {dir_path: [files_in_dir]}
        remaining_files: files not in any cohesive folder
    """
    from sorter.context import project_key

    # Phase 1: Group by project root (Audacity _data/ folders, etc.)
    project_groups: Dict[str, List[FileEntry]] = defaultdict(list)
    project_grouped: set = set()
    for f in files:
        key = project_key(f.rel_path)
        if key:
            project_groups[key].append(f)
            project_grouped.add(f.rel_path)

    # Phase 2: Remaining files grouped by immediate parent
    remaining_files = [f for f in files if f.rel_path not in project_grouped]
    parent_groups = group_files_by_directory(remaining_files)
    
    cohesive: Dict[Path, List[FileEntry]] = {}
    remaining: List[FileEntry] = []

    # Process project-root groups
    for rel_key, entries in project_groups.items():
        if nas_root:
            dir_path = nas_root / rel_key
        else:
            # Fallback: use the common ancestor of all entry paths
            dir_path = entries[0].path
            while not any(str(dir_path).endswith(rel_key.replace("/", "/")) 
                         for p in rel_key.split("/") if p) and dir_path.parent != dir_path:
                dir_path = dir_path.parent
        cohesive[dir_path] = entries

    # Process parent groups
    for dir_path, entries in parent_groups.items():
        dir_name = dir_path.name
        if _is_temp_or_generated_dir(dir_name):
            remaining.extend(entries)
        elif len(entries) >= min_files and _dir_has_meaningful_name(dir_name):
            cohesive[dir_path] = entries
        else:
            remaining.extend(entries)

    return cohesive, remaining


def make_folder_entry(
    dir_path: Path, entries: List[FileEntry], nas_root: Path
) -> Dict:
    """Create a classification entry representing an entire folder.
    
    This entry is sent to the LLM instead of individual file entries.
    """
    rel_path = str(dir_path.relative_to(nas_root)) if dir_path.is_relative_to(nas_root) else dir_path.name
    
    mime_types = set(e.mime for e in entries)
    ext_counts: Dict[str, int] = defaultdict(int)
    for e in entries:
        ext = Path(e.name).suffix.lower()
        ext_counts[ext] += 1
    top_exts = sorted(ext_counts.items(), key=lambda x: -x[1])[:5]
    sample_names = sorted(e.name for e in entries)[:8]

    # Determine oldest file age for the folder
    from sorter.context import classify_age
    old_mtime = min((e.mtime for e in entries if e.mtime > 0), default=0)
    old_ctime = min((e.ctime for e in entries if e.ctime > 0), default=0)
    old_atime = min((e.atime for e in entries if e.atime > 0), default=0)
    age_cls = classify_age(old_mtime, old_ctime, old_atime)

    return {
        "path": rel_path,
        "is_folder": True,
        "file_count": len(entries),
        "total_size": sum(e.size for e in entries),
        "mime_types": list(mime_types),
        "top_extensions": dict(top_exts),
        "sample_files": sample_names,
        "children": [str(e.rel_path) for e in entries],
        "age_classification": age_cls,
    }


def make_review_move(folder_entry: Dict, nas_root: Path) -> Dict:
    """Build a review-move entry for a failed/uncertain folder classification.

    Keeps the folder as a UNIT under _Unsorted_Review so its children are
    never scattered into individual classification (which produced garbage
    routing like loose .htm files → Projects/ERGO_Paphos).
    """
    return {
        "source": str(nas_root / folder_entry["path"]),
        "target": str(nas_root / "_Unsorted_Review" / Path(folder_entry["path"]).name),
        "is_folder": True,
        "file_count": folder_entry.get("file_count", 0),
        "total_size": folder_entry.get("total_size", 0),
        "confidence": 0,
        "reason": "folder classification failed/uncertain — review as unit",
        "_children": folder_entry.get("children", []),
    }


MEDIUM_WORD_RE = re.compile(r"(bilder|pics|pictures|fotos|photos|screens|images|img)", re.IGNORECASE)


def is_medium_word_name(name: str) -> bool:
    """True when a folder name carries no subject beyond the medium.

    Such names ("bilder", "zenos bilder", "Realtreffenpics", "bilder&screens")
    must be named for their owner/event instead. They also get a dedicated
    refinement pass, because the 7b model honours the NAMING rules for a
    handful of folders but drops them inside a full batch.

    Substring match, so false positives are possible ("Bilderrahmen"); that is
    harmless — the folder is merely re-asked in a smaller prompt.
    """
    return bool(MEDIUM_WORD_RE.search(name or ""))


def derive_medium_word_leaf(name: str) -> Optional[str]:
    """Deterministic destination name for a medium-word folder.

    "zenos bilder" -> "zenos", "Eigene Bilder" -> "Eigene",
    "Realtreffenpics" -> "Realtreffen". Returns None when the name is nothing
    but the medium ("bilder", "Fotos") — the caller dissolves those instead.

    WHY in code and not in the prompt: the model names these destinations
    correctly but cannot reliably emit the own_target boolean that tells the
    pipeline to keep its leaf (measured 5/15 then 7/15 across two runs), and a
    missing flag makes resolve_folder_move inject the source path a second time.
    """
    stem = MEDIUM_WORD_RE.sub("", name or "")
    stem = re.sub(r"[\s&_.,-]+", " ", stem).strip(" -")
    return stem or None


def resolve_folder_move(
    folder_entry: Dict,
    classification: Dict,
    nas_root: Path,
) -> Optional[Dict]:
    """Turn a folder classification into an approved move action.
    
    Returns a move dict or None if the folder should be scattered instead.
    Now includes meaningful ancestors in target path (fix: preserves context).
    """
    cat_path = classification.get("category_path", "")
    if not cat_path or cat_path == "_Unsorted_Review":
        return None

    source = nas_root / folder_entry["path"]
    source_name = source.name  # leaf folder name

    # Normalized name comparison: underscore=space, hyphen=space, collapse
    # Prevents double-append when LLM already includes the leaf name
    # (e.g. SQ_Tutorials vs SQ Tutorials → match)
    # (e.g. Neues_YW_Forum_Grafik vs Neues YW Forum - Grafik → match)
    cat_parts = list(cat_path.replace("\\", "/").split("/"))
    _norm = lambda s: re.sub(r"\s+", " ", s.lower().replace("_", " ").replace("-", " ")).strip()
    cat_last_norm = _norm(cat_parts[-1]) if cat_parts else ""
    src_last_norm = _norm(source_name)

    # Determine scan_root: first segment of rel_path (e.g. "Diverse Daten")
    rel_path = folder_entry.get("path", "")
    rel_segments = [s for s in rel_path.replace("\\", "/").split("/") if s]
    scan_root = rel_segments[0] if rel_segments else None

    if classification.get("own_target"):
        # LLM deliberately named the destination for the owner/event/subject
        # ("Tina/bilder" -> Media/Photos/Tina). Its leaf IS the intended name,
        # so skip leaf-append, ancestor injection and the anglicization strip —
        # the NAMING rule in FOLDER_RULES is the sanctioned carve-out for those.
        if ".." in cat_parts or cat_path.startswith("/"):
            return None  # never let a verbatim path escape the NAS root
        target = nas_root / cat_path
    elif cat_parts and cat_last_norm == src_last_norm:
        # LLM already included leaf name → no double-append
        target = nas_root / cat_path
    else:
        # Language guard: if LLM invented an English leaf for non-English source,
        # strip the anglicized leaf so the source folder name becomes the leaf.
        # Only strip if cat_path has 3+ levels — 1-2 level taxonomy is primary language.
        cat_leaf = cat_parts[-1] if cat_parts else ""
        if leaf_language_mismatch(cat_leaf, source_name) and len(cat_parts) > 2:
            cat_parts.pop()  # remove anglicized/invented leaf
            trimmed_cat = "/".join(cat_parts)
        else:
            trimmed_cat = cat_path

        # Get meaningful ancestors (excluding scan root and junk)
        ancestors = meaningful_ancestors(rel_path, source_name, scan_root=scan_root)
        if ancestors:
            # Build: trimmed_cat/ancestors/leaf
            ancestor_path = "/".join(ancestors)
            target = nas_root / trimmed_cat / ancestor_path / source_name
        else:
            target = nas_root / trimmed_cat / source_name

    return {
        "source": str(source),
        "target": str(target),
        "is_folder": True,
        "file_count": folder_entry.get("file_count", 0),
        "total_size": folder_entry.get("total_size", 0),
        "confidence": classification.get("confidence", 0),
        "reason": classification.get("reason", f"Move folder to {cat_path}"),
        "_children": folder_entry.get("children", []),
    }


def format_folder_for_prompt(folder_entry: Dict) -> str:
    """Format a folder entry for inclusion in the LLM prompt."""
    exts = ", ".join(f"{k} (×{v})" for k, v in folder_entry["top_extensions"].items())
    samples = ", ".join(folder_entry["sample_files"][:10])
    
    # Context hint for the folder (check path + file-type composition)
    from sorter.context import context_hint, folder_chain, classify_age
    fpath = folder_entry.get("path", "")
    ext_counts = folder_entry.get("top_extensions", {})
    hint = context_hint(fpath, ext_counts)
    chain = folder_chain(fpath)
    folder_str = " / ".join(chain) if chain else "(root)"
    age_cls = folder_entry.get("age_classification", "unknown")

    # Dominant media kind (BATCH 2: content-type hint)
    from sorter.routing import dominant_media_kind
    dom_kind = dominant_media_kind(ext_counts, 0.5)
    kind_line = f"  Dominant type: {dom_kind}\n" if dom_kind else ""
    
    return (
        f"[FOLDER] {folder_entry['path']}/\n"
        f"  Subject: {Path(folder_entry['path']).name}\n"
        f"  Folder context: {folder_str}\n"
        f"  Context hint: {hint}\n"
        f"  Age classification: {age_cls}\n"
        f"  Files: {folder_entry['file_count']}, Size: {folder_entry['total_size']} bytes\n"
        f"  Types: {exts}\n"
        f"  Samples: {samples}\n"
        f"{kind_line}"
    )