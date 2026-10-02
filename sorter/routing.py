"""Deterministic extension→category routing — hard rules applied BEFORE LLM.

Maps file extensions and MIME types to fixed category paths.  Files that match
a hard rule get pinned category_path + confidence=95 and never reach the LLM.
This kills wrong-media routing (video→Photos, audio→MP3) and cascade issues.
"""
from pathlib import Path
from typing import Optional


# ── Extension → category mapping ──
# Format: extension -> (category_path, media_kind)
# media_kind used for folder-dominant-type detection (BATCH 2)

EXT_ROUTE: dict[str, tuple[str, str]] = {
    # Audio → Media/Music
    ".mp3": ("Media/Music", "audio"),
    ".wav": ("Media/Music", "audio"),
    ".flac": ("Media/Music", "audio"),
    ".ogg": ("Media/Music", "audio"),
    ".aac": ("Media/Music", "audio"),
    ".m4a": ("Media/Music", "audio"),
    ".wma": ("Media/Music", "audio"),
    ".aiff": ("Media/Music", "audio"),
    ".au": ("Media/Music", "audio"),  # Audacity chunks
    ".dss": ("Media/Music", "audio"),  # Dictation audio
    ".nri": ("Media/Music", "audio"),  # Nero music image
    ".mcx": ("Media/Music", "audio"),  # music export
    # Video → Media/Videos
    ".mp4": ("Media/Videos", "video"),
    ".mov": ("Media/Videos", "video"),
    ".avi": ("Media/Videos", "video"),
    ".mkv": ("Media/Videos", "video"),
    ".webm": ("Media/Videos", "video"),
    ".wmv": ("Media/Videos", "video"),
    ".mpeg": ("Media/Videos", "video"),
    ".mpg": ("Media/Videos", "video"),
    # Image → Media/Photos
    ".jpg": ("Media/Photos", "image"),
    ".jpeg": ("Media/Photos", "image"),
    ".png": ("Media/Photos", "image"),
    ".gif": ("Media/Photos", "image"),
    ".webp": ("Media/Photos", "image"),
    ".bmp": ("Media/Photos", "image"),
    ".tiff": ("Media/Photos", "image"),
    ".tif": ("Media/Photos", "image"),
    ".svg": ("Media/Photos", "image"),
    # Document → Zeno/Documents
    ".doc": ("Zeno/Documents", "doc"),
    ".docx": ("Zeno/Documents", "doc"),
    ".pdf": ("Zeno/Documents", "doc"),
    ".xls": ("Zeno/Documents", "doc"),
    ".xlsx": ("Zeno/Documents", "doc"),
    ".ppt": ("Zeno/Documents", "doc"),
    ".pptx": ("Zeno/Documents", "doc"),
    ".pps": ("Zeno/Documents", "doc"),
    ".odt": ("Zeno/Documents", "doc"),
    ".ods": ("Zeno/Documents", "doc"),
    ".txt": ("Zeno/Documents", "doc"),
    ".tex": ("Zeno/Documents", "doc"),
    ".md": ("Zeno/Documents", "doc"),
    ".csv": ("Zeno/Documents", "doc"),
    ".rtf": ("Zeno/Documents", "doc"),
    # Design → Zeno/Documents/Design
    ".psd": ("Zeno/Documents/Design", "design"),
    ".pspimage": ("Zeno/Documents/Design", "design"),
    ".xcf": ("Zeno/Documents/Design", "design"),
    ".ai": ("Zeno/Documents/Design", "design"),
    ".psp": ("Zeno/Documents/Design", "design"),
    # 3D models → Zeno/Documents/Design (user content, NEVER delete)
    ".skp": ("Zeno/Documents/Design", "design"),
    ".skb": ("Zeno/Documents/Design", "design"),
    ".dwg": ("Zeno/Documents/Design", "design"),
    ".dxf": ("Zeno/Documents/Design", "design"),
    ".3ds": ("Zeno/Documents/Design", "design"),
    ".blend": ("Zeno/Documents/Design", "design"),
    ".obj": ("Zeno/Documents/Design", "design"),
    ".fbx": ("Zeno/Documents/Design", "design"),
    ".max": ("Zeno/Documents/Design", "design"),
    ".c4d": ("Zeno/Documents/Design", "design"),
    # Binary/archive → Zeno/Downloads
    ".exe": ("Zeno/Downloads", "binary"),
    ".msi": ("Zeno/Downloads", "binary"),
    ".rar": ("Zeno/Downloads", "binary"),
    ".zip": ("Zeno/Downloads", "binary"),
    ".7z": ("Zeno/Downloads", "binary"),
    ".tar": ("Zeno/Downloads", "binary"),
    ".gz": ("Zeno/Downloads", "binary"),
    ".db": ("Zeno/Downloads", "binary"),
    ".r00": ("Zeno/Downloads", "binary"),
    ".r01": ("Zeno/Downloads", "binary"),
    ".r02": ("Zeno/Downloads", "binary"),
    ".r03": ("Zeno/Downloads", "binary"),
}

# MIME prefix → extension set (for fallback via MIME)
_MIME_EXT_MAP: dict[str, str] = {
    "audio/": "Media/Music",
    "video/": "Media/Videos",
    "image/": "Media/Photos",
    "text/": "Zeno/Documents",
}

# Extensions that route to a non-standard path (for the get_media_kind helper)
_EXT_TO_KIND: dict[str, str] = {ext: kind for ext, (_, kind) in EXT_ROUTE.items()}


# Taxonomy top-levels — if a file's path starts with any of these as a path
# segment, skip extension routing to preserve existing organization.
_TAXONOMY_SEGMENTS = {"zeno", "family", "company", "projects", "media", "archives"}

def route_by_extension(rel_path: str, mime: str = "") -> Optional[str]:
    """Return a deterministic category_path for a file based on extension,
    or None if no hard rule applies (file should go to LLM).

    Args:
        rel_path: relative path (e.g. "Diverse Daten/song.mp3")
        mime: MIME type string (used as fallback if extension not in table)

    Returns:
        category_path string like "Media/Music", or None to defer to LLM.
    """
    # ── Path-context guard ──
    # Skip extension routing if file is already under a taxonomy category
    # (preserves project affiliation, avoids stripping context from files
    #  that are already in a sensible organizational structure).
    path_lower = rel_path.replace("\\", "/").lower()
    for seg in _TAXONOMY_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return None  # Already organized → let LLM decide

    ext = Path(rel_path).suffix.lower()
    entry = EXT_ROUTE.get(ext)
    if entry:
        return entry[0]

    # MIME-based fallback for unknown extensions
    if mime:
        for prefix, category in _MIME_EXT_MAP.items():
            if mime.startswith(prefix):
                return category

    return None


def get_media_kind(rel_path: str) -> str:
    """Return the media kind string (audio/video/image/doc/design/binary)
    for extension-based routing, or empty string for unknown.
    Used for folder dominant-type detection (BATCH 2).
    """
    ext = Path(rel_path).suffix.lower()
    return _EXT_TO_KIND.get(ext, "")


def dominant_media_kind(ext_counts: dict[str, int], threshold: float = 0.5) -> str:
    """If >= threshold fraction of extensions belong to the same media kind,
    return that kind ('audio', 'video', 'image', 'doc', 'design', 'binary').
    Returns empty string if no single kind dominates.

    Used in _classify_folders to override LLM routing.
    """
    if not ext_counts:
        return ""
    kind_scores: dict[str, int] = {}
    total = sum(ext_counts.values())
    for ext, count in ext_counts.items():
        kind = _EXT_TO_KIND.get(ext.lower(), "")
        if kind:
            kind_scores[kind] = kind_scores.get(kind, 0) + count
    if not kind_scores:
        return ""
    best_kind = max(kind_scores, key=kind_scores.get)
    if kind_scores[best_kind] / total >= threshold:
        return best_kind
    return ""


# ── Delete protection ──
# File kinds that are ALWAYS user content — never safe to delete, even when
# old or in temp/backup folders. The age rule (OLD_TEMP_BACKUP → DELETE)
# must never fire on these. Protected files get rerouted to Archives
# instead of deletion candidates.
_DELETE_PROTECTED_EXT = {
    # 3D models
    ".skp", ".skb", ".dwg", ".dxf", ".3ds", ".blend", ".obj", ".fbx",
    ".max", ".c4d", ".stl", ".step", ".iges",
    # Design / image source
    ".psd", ".pspimage", ".xcf", ".ai", ".psp", ".svg", ".bmp", ".tiff",
    ".tif", ".raw", ".cr2", ".nef", ".arw",
    # Media (photos/video/audio are user content)
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi",
    ".mkv", ".wmv", ".mp3", ".wav", ".flac", ".aiff", ".m4a",
    # Document source (scans, personal papers)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".ods", ".aup", ".aup3", ".au",
    # Project files
    ".sql", ".emp", ".xmind",
    # SketchUp-specific generated files that are NOT safe (keep backups)
    ".skb",
}


def should_protect_from_delete(rel_path: str) -> bool:
    """Return True if a file's extension marks it as user content that must
    never be auto-deleted (3D models, design sources, media, documents).

    The age-based deletion rule (OLD_TEMP_BACKUP → DELETE) is overridden
    for these: they get rerouted to Archives/Old_Projects instead.
    """
    ext = Path(rel_path).suffix.lower()
    return ext in _DELETE_PROTECTED_EXT