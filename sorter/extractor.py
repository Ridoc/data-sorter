from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from sorter.scanner import FileEntry

TEXT_TRUNCATE = 2000

_MARKITDOWN_AVAILABLE: Optional[bool] = None


def _markitdown_available() -> bool:
    global _MARKITDOWN_AVAILABLE
    if _MARKITDOWN_AVAILABLE is not None:
        return _MARKITDOWN_AVAILABLE
    try:
        from markitdown import MarkItDown  # noqa: F401

        _MARKITDOWN_AVAILABLE = True
    except ImportError:
        _MARKITDOWN_AVAILABLE = False
    return _MARKITDOWN_AVAILABLE


OFFICE_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}

TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/x-python",
    "text/html",
    "text/csv",
    "text/yaml",
    "text/vcard",
    "application/json",
}

IMAGE_MIMES = {"image/jpeg", "image/png", "image/heic", "image/gif", "image/webp", "image/tiff"}

VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/x-ms-wmv", "video/mpeg"}

AUDIO_MIMES = {"audio/mpeg", "audio/wav", "audio/flac", "audio/ogg", "audio/mp4"}


def _extract_markitdown(file_path: Path) -> Dict[str, Any]:
    from markitdown import MarkItDown

    md = MarkItDown()
    result = md.convert(str(file_path))
    text = (result.text_content or "")[:TEXT_TRUNCATE]
    return {"text": text, "metadata": {}, "error": None}


def _extract_pdf(file_path: Path) -> Dict[str, Any]:
    if not _markitdown_available():
        return {"text": "", "metadata": {"pages": None}, "error": "markitdown not installed"}
    try:
        result = _extract_markitdown(file_path)
        result["metadata"] = {"pages": None}
        return result
    except Exception as e:
        return {"text": "", "metadata": {"pages": None}, "error": str(e)}


def _extract_office(file_path: Path, mime: str) -> Dict[str, Any]:
    if not _markitdown_available():
        return {"text": "", "metadata": {}, "error": "markitdown not installed"}
    try:
        return _extract_markitdown(file_path)
    except Exception as e:
        return {"text": "", "metadata": {}, "error": str(e)}


def _extract_image(file_path: Path) -> Dict[str, Any]:
    try:
        img = Image.open(file_path)
        w, h = img.size
        exif_data: Dict[str, Any] = {}
        try:
            raw_exif = img._getexif()
            if raw_exif:
                tag_map = {
                    271: "make",
                    272: "model",
                    306: "datetime_original",
                    36867: "datetime_original",
                    34855: "iso",
                    37377: "shutter_speed",
                    37378: "aperture",
                    37379: "focal_length",
                }
                for tag, key in tag_map.items():
                    if tag in raw_exif:
                        val = raw_exif[tag]
                        if isinstance(val, bytes):
                            val = val.decode(errors="replace").strip("\x00").strip()
                        exif_data[key] = val
                gps_info = raw_exif.get(34853)
                if gps_info and isinstance(gps_info, dict):
                    exif_data["gps_info"] = {
                        str(k): str(v) if isinstance(v, bytes) else v
                        for k, v in gps_info.items()
                    }
        except Exception:
            pass
        metadata = {"width": w, "height": h, "exif": exif_data}
        return {"text": "", "metadata": metadata, "error": None}
    except Exception as e:
        return {"text": "", "metadata": {}, "error": str(e)}


def _run_ffprobe(file_path: Path) -> Optional[Dict[str, Any]]:
    if not shutil.which("ffprobe"):
        return None
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _extract_video(file_path: Path) -> Dict[str, Any]:
    data = _run_ffprobe(file_path)
    if data is None:
        return {"text": "", "metadata": {}, "error": "ffprobe not available or failed"}
    metadata: Dict[str, Any] = {}
    try:
        fmt = data.get("format", {})
        if fmt.get("duration"):
            metadata["duration"] = float(fmt["duration"])
        if fmt.get("tags", {}).get("creation_time"):
            metadata["creation_time"] = fmt["tags"]["creation_time"]
    except Exception:
        pass
    try:
        streams = data.get("streams", [])
        for s in streams:
            if s.get("codec_type") == "video":
                metadata["codec"] = s.get("codec_name", metadata.get("codec"))
                if s.get("width"):
                    metadata["width"] = s["width"]
                if s.get("height"):
                    metadata["height"] = s["height"]
                break
    except Exception:
        pass
    return {"text": "", "metadata": metadata, "error": None}


def _extract_audio(file_path: Path) -> Dict[str, Any]:
    data = _run_ffprobe(file_path)
    if data is None:
        return {"text": "", "metadata": {}, "error": "ffprobe not available or failed"}
    metadata: Dict[str, Any] = {}
    try:
        fmt = data.get("format", {})
        if fmt.get("duration"):
            metadata["duration"] = float(fmt["duration"])
        tags = fmt.get("tags", {})
        for key, val in [("artist", "artist"), ("album", "album"), ("title", "title")]:
            if tags.get(key):
                metadata[val] = tags[key]
        streams = data.get("streams", [])
        for s in streams:
            if s.get("codec_type") == "audio":
                metadata["codec"] = s.get("codec_name", metadata.get("codec"))
                break
    except Exception:
        pass
    return {"text": "", "metadata": metadata, "error": None}


def _extract_text_file(file_path: Path) -> Dict[str, Any]:
    try:
        text = file_path.read_text(errors="replace")[:TEXT_TRUNCATE]
        line_count = text.count("\n") + 1
        if len(text) == TEXT_TRUNCATE:
            line_count = text[:TEXT_TRUNCATE].count("\n") + 1
        return {"text": text, "metadata": {"line_count": line_count}, "error": None}
    except Exception as e:
        return {"text": "", "metadata": {}, "error": str(e)}


def extract_content(file_path: Path, mime: str) -> Dict[str, Any]:
    mime = mime.lower()
    try:
        if mime == "application/pdf":
            return _extract_pdf(file_path)
        if mime in OFFICE_MIMES:
            return _extract_office(file_path, mime)
        if mime in IMAGE_MIMES:
            return _extract_image(file_path)
        if mime in VIDEO_MIMES:
            return _extract_video(file_path)
        if mime in AUDIO_MIMES:
            return _extract_audio(file_path)
        if mime in TEXT_MIMES or mime.startswith("text/"):
            return _extract_text_file(file_path)
        return {"text": "", "metadata": {}, "error": None}
    except Exception as e:
        return {"text": "", "metadata": {}, "error": str(e)}


def extract_batch(entries: List[FileEntry], max_workers: int = 4) -> List[Dict[str, Any]]:
    results: List[Optional[Dict[str, Any]]] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(extract_content, e.path, e.mime): i for i, e in enumerate(entries)}
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                results[idx] = {"text": "", "metadata": {}, "error": str(e)}
    return results


def can_extract_text(mime: str) -> bool:
    mime = mime.lower()
    if mime == "application/pdf":
        return True
    if mime in OFFICE_MIMES:
        return True
    if mime in TEXT_MIMES or mime.startswith("text/"):
        return True
    return False


def _format_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def get_file_summary(entry: FileEntry, content: Optional[Dict[str, Any]]) -> str:
    parts = [entry.mime.split("/")[0].upper()]
    parts.append(_format_bytes(entry.size))
    if content and content.get("metadata"):
        meta = content["metadata"]
        if "pages" in meta:
            parts.append(f"{meta['pages'] or '?'} pages")
        if "width" in meta and "height" in meta:
            parts.append(f"{meta['width']}x{meta['height']}")
        if "duration" in meta:
            secs = float(meta["duration"])
            m, s = divmod(int(secs), 60)
            parts.append(f"{m}:{s:02d}")
        if "line_count" in meta:
            parts.append(f"{meta['line_count']} lines")
        if meta.get("exif", {}).get("datetime_original"):
            parts.append(f"taken {meta['exif']['datetime_original']}")
    return ", ".join(parts)