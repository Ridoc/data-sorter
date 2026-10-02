"""Deterministic file context detection — identifies Audacity projects, Adobe
Premiere temp files, and other known file/folder patterns, independent of LLM.
Used to enrich the classification prompt with reliable context hints."""
from pathlib import Path
from typing import List, Optional

import time as _time_module


# ── Audacity detection ──

_AUDACITY_AUP = {".aup", ".aup3"}


def detect_audacity(rel_path: str) -> bool:
    """Check if a file belongs to an Audacity project.
    
    Audacity projects consist of:
    - .aup / .aup3 project file
    - A _data/ folder with .au audio chunks
    - audacity_temp/ for temporary files
    """
    parts = rel_path.replace("\\", "/").split("/")
    ext = Path(rel_path).suffix.lower()

    # Direct .aup project file
    if ext in _AUDACITY_AUP:
        return True

    # Under a *_data folder (e.g. hypnose_data/e00/d00/file.au)
    for i, part in enumerate(parts):
        if part.endswith("_data") and i < len(parts) - 1:
            return True
        if part == "audacity_temp":
            return True

    return False


# ── Adobe Premiere detection ──

_ADOBE_PREMIERE_DIRS = {
    "Adobe Premiere Pro Auto-Save",
    "Adobe Premiere Pro Preview Files",
    "Conformed Audio Files",
}

_ADOBE_PREMIERE_EXT = {".prv"}


def detect_adobe_premiere(rel_path: str) -> bool:
    """Check if a file is an Adobe Premiere temp file."""
    parts = rel_path.replace("\\", "/").split("/")
    ext = Path(rel_path).suffix.lower()

    if ext in _ADOBE_PREMIERE_EXT:
        return True

    for part in parts:
        if part in _ADOBE_PREMIERE_DIRS:
            return True

    return False


# ── Context hint ──

_CONTEXT_PRIORITY = [("ADOBE_PREMIERE_TEMP", detect_adobe_premiere),
                     ("AUDACITY_PROJECT", detect_audacity)]


def detect_audio_project_from_contents(ext_counts: dict) -> bool:
    """Check if a folder's file extensions indicate an Audacity/audio project.

    Folders full of .au (Audacity audio chunks) or containing audio file
    types without obvious other content are audio projects.
    """
    if not ext_counts:
        return False
    audio_exts = {".au", ".aup", ".aup3"}
    total = sum(ext_counts.values())
    if total == 0:
        return False
    audio_count = sum(ext_counts.get(e, 0) for e in audio_exts)
    # If 50%+ of files are Audacity/audio types → audio project
    return audio_count / total >= 0.5


def context_hint(rel_path: str, ext_counts: dict = None) -> str:
    """Return a context hint string for the file/folder:
    AUDACITY_PROJECT, ADOBE_PREMIERE_TEMP, or NONE.

    If ext_counts is provided (for folder entries), detect audio projects
    by file-type composition even when the path name doesn't contain
    Audacity markers.
    """
    for hint_name, detector in _CONTEXT_PRIORITY:
        if detector(rel_path):
            return hint_name
    if ext_counts and detect_audio_project_from_contents(ext_counts):
        return "AUDACITY_PROJECT"
    return "NONE"


# ── Folder chain ──


def folder_chain(rel_path: str) -> List[str]:
    """Return ancestor folder names from root to immediate parent.
    
    Example: "Berufliches/Hypnose/hypnose_data/e00/file.au"
    → ["Berufliches", "Hypnose", "hypnose_data", "e00"]
    """
    parts = Path(rel_path).parts if "\\" not in rel_path else Path(rel_path.replace("\\", "/")).parts
    if not parts:
        return []
    # All parts except the last (filename)
    return list(parts[:-1])


# ── Age classification ──


def classify_age(
    mtime: float, ctime: float, atime: float,
    delete_candidate_years: float = 10,
    archive_years: float = 5,
) -> str:
    """Classify a file's age for sorting decisions.
    
    Returns one of:
    - OLD_TEMP_BACKUP: file is old AND likely temp/backup → deletion candidate
    - OLD_USER_CONTENT: file is old but likely user content → consider archive
    - RECENT: file is recent, no age-based action needed
    """
    if mtime <= 0 and ctime <= 0 and atime <= 0:
        return "RECENT"

    now = _time_module.time()
    max_age_seconds = max(
        now - mtime if mtime > 0 else 0,
        now - ctime if ctime > 0 else 0,
        now - atime if atime > 0 else 0,
    )

    years_old = max_age_seconds / (365.25 * 86400)

    if years_old < archive_years:
        return "RECENT"
    if years_old < delete_candidate_years:
        return "OLD_USER_CONTENT"
    return "OLD_TEMP_BACKUP"  # > delete_candidate_years


# ── Age string (re-export from scanner) ──

from sorter.scanner import age_string  # noqa: F401


# ── Project-root detection ──

_AUDACITY_PROJECT_MARKERS = {
    "audacity_temp",
}


def _is_data_folder(name: str) -> bool:
    """Check if folder name ends with _data (Audacity data folder pattern)."""
    return name.endswith("_data") and len(name) > 5


def project_key(rel_path: str) -> Optional[str]:
    """Compute a project grouping key from a relative path.
    
    For files in an Audacity project (e.g., hypnose_data/e00/d00/file.au),
    returns the path to the project root folder (e.g., 'Berufliches/Hypnose').
    For files NOT inside a known project structure, returns None.
    
    This is used to group ALL files from one project into a single classification
    unit, regardless of nesting depth (the '1 folder = 1 unit' principle).
    """
    parts = rel_path.replace("\\", "/").split("/")
    if not parts:
        return None

    ext = Path(rel_path).suffix.lower()

    # If the file IS an Audacity project file (.aup/.aup3), the project root
    # is its parent folder
    if ext in _AUDACITY_AUP and len(parts) > 1:
        return "/".join(parts[:-1])

    # If we're inside a *_data folder, the project root is the ancestor
    # (e.g., hypnose_data's parent = Hypnose)
    for i, part in enumerate(parts):
        if _is_data_folder(part) and i > 0:
            return "/".join(parts[:i])
        if part in _AUDACITY_PROJECT_MARKERS and i > 0:
            return "/".join(parts[:i])
        if part in _ADOBE_PREMIERE_DIRS and i > 0:
            return "/".join(parts[:i])

    return None


# ── Canonicalize target naming ──


def _is_name_variant(a: str, b: str) -> bool:
    """Check if two names are spelling variants of each other."""
    a_low, b_low = a.lower(), b.lower()
    if a_low == b_low:
        return True
    # Levenshtein-like: if they share >= 3 prefix chars and small edit distance
    prefix_len = 0
    for ca, cb in zip(a_low, b_low):
        if ca == cb:
            prefix_len += 1
        else:
            break
    if prefix_len < 3:
        return False
    # Simple edit distance: different length by at most 3, and similar enough
    if abs(len(a_low) - len(b_low)) > 3:
        return False
    # Count differing chars
    diffs = sum(1 for ca, cb in zip(a_low, b_low) if ca != cb)
    diffs += abs(len(a_low) - len(b_low))
    return diffs <= 3


def canonicalize_path(llm_category_path: str, source_unit_name: str) -> str:
    """Post-process LLM category_path to preserve the source folder name exactly.
    
    Hypnosis → Hypnose (spelling variant)
    Documents → Documents (no change if subfolder was dropped entirely —
      the canonicalizer doesn't blindly change unrelated segments)
    """
    if not source_unit_name or "/" in source_unit_name:
        return llm_category_path
    
    parts = llm_category_path.split("/")
    if len(parts) < 2:
        return llm_category_path
    
    last_segment = parts[-1]
    
    # If the last segment is a spelling variant of the source name → fix it
    if _is_name_variant(last_segment, source_unit_name) and last_segment != source_unit_name:
        parts[-1] = source_unit_name
        return "/".join(parts)
    
    return llm_category_path

    ext = Path(rel_path).suffix.lower()

    # If the file IS an Audacity project file (.aup/.aup3), the project root
    # is its parent folder
    if ext in _AUDACITY_AUP and len(parts) > 1:
        return "/".join(parts[:-1])

    # If we're inside a *_data folder, the project root is the grandparent
    # (e.g., Hypnose from Hypnose/hypnose_data/e00/...)
    for i, part in enumerate(parts):
        if _is_data_folder(part) and i > 0:
            return "/".join(parts[:i])
        if part in _AUDACITY_PROJECT_MARKERS and i > 0:
            return "/".join(parts[:i])
        if part in _ADOBE_PREMIERE_DIRS and i > 0:
            return "/".join(parts[:i])

    return None