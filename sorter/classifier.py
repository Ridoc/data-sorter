import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from sorter.extractor import extract_content, can_extract_text
from sorter.scanner import FileEntry, age_string
from sorter.taxonomy import get_taxonomy_yaml_string, taxonomy_to_yaml
from sorter.context import context_hint, folder_chain, classify_age


# ── Rename validation ──

_ILLEGAL_CHARS = re.compile(r'[/\\:*?"<>|]')
_IMAGE_VIDEO_AUDIO = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff",
                      ".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav",
                      ".flac", ".ogg", ".au", ".aiff", ".m4a", ".wma", ".aac"}


def validate_suggested_name(suggested_name: str, original_name: str) -> str | None:
    """Validate a suggested name from the LLM.

    Returns the validated suggested_name, or None if invalid (keep original).
    """
    if not suggested_name or not isinstance(suggested_name, str):
        return None

    name = suggested_name.strip()
    if not name:
        return None

    # Preserve original extension
    orig_stem, orig_ext = (Path(original_name).stem, Path(original_name).suffix.lower())
    suggested_ext = Path(name).suffix.lower()

    if suggested_ext != orig_ext:
        # LLM dropped/changed extension — re-attach original
        name = Path(name).stem + orig_ext

    # Strip illegal filename characters
    name = _ILLEGAL_CHARS.sub("_", name)

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()

    # Reject if empty after sanitization
    if not name or not Path(name).stem:
        return None

    # Reject if name unchanged (same stem + ext)
    if Path(name).stem.lower() == orig_stem.lower() and Path(name).suffix.lower() == orig_ext:
        return None

    # Reject if too long
    if len(name) > 80:
        return None

    # Reject path separators (already stripped above, but double-check)
    if "/" in name or "\\" in name:
        return None

    return name


def should_skip_rename(mime: str) -> bool:
    """Check if a file type should never be renamed (images/video/audio)."""
    if not mime:
        return False
    mime_lower = mime.lower()
    return any(prefix in mime_lower for prefix in ["image/", "video/", "audio/"])


PROMPT_TEMPLATE = """You are a NAS file organizer. Given file metadata, folder context, and content, classify each file into the appropriate folder path.

EXISTING FOLDER TAXONOMY:
{taxonomy_yaml}

FILES TO CLASSIFY:
{file_entries}

For each file, respond with a JSON array:
[
  {{
    "path": "relative/file/path",
    "category_path": "Zeno/Documents",
    "confidence": 92,
    "reason": "Invoice from vendor XYZ"
  }}
]

Rules:
- Classify by SUBJECT first (file name + folder name reveal purpose), NOT by dominant file type.
- Image files do NOT always mean Media/Photos: images can be design artwork
  (logos, banners, graphics), scanned documents, screenshots, or web graphics.
  The file name and folder context reveal the purpose.
- Dominant file type is a secondary hint only.
- Logos, banners, buttons, icons, graphics, mockups and drafts are
  design assets even though they are image files -> Zeno/Documents/Design.
- Scanned certificates, invoices, letters and other documents ->
  Zeno/Documents, not Media/Photos (they are documents, not photos).
- If unsure whether images are photos or design, prefer Design when the
  file/folder names suggest artwork, branding or drafts.
- The design subfolder lives under Zeno/Documents/Design, never a top-level Zeno/Design.
- category_path must start with one of the top-level folders: Zeno, Family, Company, Projects, Media, Archives
- If no existing subfolder fits, propose a new subfolder under the best top-level (e.g. "Zeno/New_Folder")
- confidence 0-100: >85 = confident, 50-85 = unsure, <50 = uncertain
- MANDATORY LANGUAGE PRESERVATION: File names must NEVER be translated. Keep the original language.
  If a new subfolder is needed for non-English content, name it in the source content's language
  (e.g. for German files, propose a German-named subfolder, never an English one).

FILE TYPE RULES (use the Context hint and Folder context fields):
- Context hint AUDACITY_PROJECT: .aup/.aup3 project files, *_data/ folders with .au chunks, or audacity_temp/ files -> classify by the folder's subject (e.g., "Hypnose" -> Zeno/Documents/Hypnosis). NEVER mark .au files as delete — they are Audacity audio recordings.
- Context hint ADOBE_PREMIERE_TEMP: .prv extension or files in "Adobe Premiere Pro Auto-Save/", "Adobe Premiere Pro Preview Files/", "Conformed Audio Files/" directories -> category_path "DELETE" (these are safe to delete).
- Context hint NONE: classify normally by content and file type.
- .prtl files (Adobe Premiere title overlays) are graphics/design, NOT video files -> Zeno/Documents/Design.

RENAME RULE (optional, only for vague names):
- If the file's current name is vague or generic (e.g. "Kostentabelle", "Scan", "Unbenannt", "manager profile", "document"),
  propose a descriptive 3-6 word name derived from the file CONTENT, in the content's own language.
- Add the field "suggested_name" to the JSON object: e.g., "suggested_name": "Büro Minimalbetrieb Kosten.docx"
- Preserve the original file extension exactly in suggested_name.
- Omit suggested_name entirely if the file name is already descriptive and specific.
- NEVER rename images, video, or audio files (no content extraction reliable enough).

AGE RULES (use the Ages field):
- "old_temp_backup": file is 10+ years old AND from a temp/backup/generated folder -> category_path "DELETE" (candidate for deletion review)
- "old_user_content": file is 5+ years old but likely user content -> Archives/Old_Projects or the best category
- "recent": file is recent, classify normally by content

Return ONLY the JSON array, no other text"""


class OllamaClient:
    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:7b",
        fallback_model: str = "qwen2.5:1.5b",
        timeout: int = 60,
        batch_size: int = 8,
        delete_candidate_years: float = 10.0,
        archive_years: float = 5.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.batch_size = batch_size
        self.delete_candidate_years = delete_candidate_years
        self.archive_years = archive_years

    def classify_batch(
        self, files: List[FileEntry], taxonomy_yaml: str
    ) -> List[Dict]:
        files_with_content: List[Tuple[FileEntry, Dict[str, Any]]] = []
        for f in files:
            if can_extract_text(f.mime):
                content = extract_content(f.path, f.mime)
            else:
                content = {"text": "", "metadata": {}}
            files_with_content.append((f, content))

        prompt = self._build_batch_prompt(files_with_content, taxonomy_yaml)

        try:
            response_text = self._call_ollama(prompt)
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Ollama not running at {self.endpoint}"
            ) from None
        except requests.exceptions.Timeout:
            try:
                response_text = self._call_ollama_fallback(prompt)
            except Exception:
                return self._fallback_results(files)
        except Exception:
            try:
                response_text = self._call_ollama_fallback(prompt)
            except Exception:
                return self._fallback_results(files)

        try:
            results = self._parse_response(response_text)
        except (json.JSONDecodeError, ValueError):
            # Parsing failed — retry with smaller batches
            return self._retry_with_smaller_batches(files_with_content, taxonomy_yaml)

        path_to_result = {r["path"]: r for r in results if "path" in r}
        merged: List[Dict] = []
        for f in files:
            if f.rel_path in path_to_result:
                merged.append(path_to_result[f.rel_path])
            else:
                merged.append(self._fallback_single(f.rel_path))
        return merged

    def _retry_with_smaller_batches(
        self,
        files_with_content: List[Tuple[FileEntry, Dict[str, Any]]],
        taxonomy_yaml: str,
    ) -> List[Dict]:
        """Retry classification with smaller batch sizes when JSON parsing fails.
        
        Splits the batch into halves, then single files if needed.
        """
        entries = files_with_content
        if len(entries) <= 1:
            # Single file already failed — try fallback model
            try:
                prompt = self._build_batch_prompt(entries, taxonomy_yaml)
                resp = self._call_ollama_fallback(prompt)
                results = self._parse_response(resp)
                path_map = {r["path"]: r for r in results}
                merged = []
                for f, _ in entries:
                    merged.append(path_map.get(f.rel_path, self._fallback_single(f.rel_path)))
                return merged
            except Exception:
                return [self._fallback_single(f.rel_path) for f, _ in entries]

        # Split in half and retry each half
        mid = len(entries) // 2
        left = self._retry_with_smaller_batches(entries[:mid], taxonomy_yaml)
        right = self._retry_with_smaller_batches(entries[mid:], taxonomy_yaml)
        return left + right

    def classify_single(
        self, file_entry: FileEntry, content: Dict, taxonomy_yaml: str
    ) -> Dict:
        file_info = self._format_file_entry(file_entry, content)
        prompt = self._build_batch_prompt(
            [(file_entry, content)], taxonomy_yaml
        )
        try:
            response_text = self._call_ollama(prompt)
        except Exception:
            return self._fallback_single(file_entry.rel_path)

        try:
            results = self._parse_response(response_text)
        except Exception:
            return self._fallback_single(file_entry.rel_path)

        for r in results:
            if r.get("path") == file_entry.rel_path:
                return r
        return self._fallback_single(file_entry.rel_path)

    def _build_batch_prompt(
        self,
        files_with_content: List[Tuple[FileEntry, Dict[str, Any]]],
        taxonomy_yaml: str,
    ) -> str:
        entries: List[str] = []
        for entry, content in files_with_content:
            entries.append(self._format_file_entry(entry, content))
        file_entries_str = "\n---\n".join(entries)
        return PROMPT_TEMPLATE.format(
            taxonomy_yaml=taxonomy_yaml, file_entries=file_entries_str
        )

    def _format_file_entry(self, entry: FileEntry, content: Dict) -> str:
        text_preview = (content.get("text") or "")[:200]
        meta = content.get("metadata") or {}
        summary = f"[{entry.mime}] {entry.size} bytes"
        if meta:
            summary += f" | meta: {json.dumps(meta)}"

        # Folder context
        chain = folder_chain(entry.rel_path)
        folder_str = " / ".join(chain) if chain else "(root)"

        # Context hint (deterministic)
        hint = context_hint(entry.rel_path)

        # Age info
        age_cls = classify_age(entry.mtime, entry.ctime, entry.atime,
                                delete_candidate_years=self.delete_candidate_years,
                                archive_years=self.archive_years)
        mtime_str = f"{age_string(entry.mtime)} ago" if entry.mtime else "unknown"
        ctime_str = f"{age_string(entry.ctime)} ago" if entry.ctime else "unknown"
        atime_str = f"{age_string(entry.atime)} ago" if entry.atime else "unknown"

        lines = [
            f"Path: {entry.rel_path}",
            f"Folder context: {folder_str}",
            f"Context hint: {hint}",
            f"Ages: modified {mtime_str} · created {ctime_str} · accessed {atime_str}",
            f"Age classification: {age_cls}",
            f"Info: {summary}",
        ]
        if text_preview:
            lines.append(f"Content: {text_preview}")
        return "\n".join(lines)

    def _call_ollama(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "seed": 42},
        }
        resp = requests.post(
            f"{self.endpoint}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    def _call_ollama_fallback(self, prompt: str) -> str:
        payload = {
            "model": self.fallback_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "seed": 42},
        }
        resp = requests.post(
            f"{self.endpoint}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    def _parse_response(self, response_text: str) -> List[Dict]:
        text = response_text.strip()

        if text.startswith("```"):
            blocks = re.findall(
                r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE
            )
            if blocks:
                text = blocks[0].strip()

        arr_match = re.search(r"(\[[\s\S]*\])", text)
        if arr_match:
            text = arr_match.group(1)

        text = re.sub(r",\s*]", "]", text)
        text = re.sub(r",\s*}", "}", text)

        # Attempt parse
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Repair attempt: extract individual objects with regex, parse each
            try:
                objects = re.findall(r'\{[^}]+\}', text)
                parsed = []
                for obj_str in objects:
                    # Fix common LLM errors
                    obj_str = re.sub(r",\s*}", "}", obj_str)
                    obj_str = re.sub(r"'", '"', obj_str)
                    obj_str = re.sub(r'(\w+):', r'"\1":', obj_str)  # unquoted keys
                    try:
                        parsed.append(json.loads(obj_str))
                    except json.JSONDecodeError:
                        continue
                # If no objects recovered, fall through to raise
                if not parsed:
                    raise json.JSONDecodeError("Per-object recovery: no valid objects", text, 0)
            except json.JSONDecodeError:
                raise

        if not isinstance(parsed, list):
            raise ValueError("Response is not a JSON array")

        results: List[Dict] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "path": item.get("path", ""),
                    "category_path": item.get("category_path", "_Unsorted_Review"),
                    "confidence": int(item.get("confidence", 0)),
                    "reason": item.get("reason", ""),
                    "suggested_name": item.get("suggested_name"),
                    "dissolve": bool(item.get("dissolve", False)),
                    "own_target": bool(item.get("own_target", False)),
                }
            )
        return results

    def _fallback_single(self, rel_path: str) -> Dict:
        return {
            "path": rel_path,
            "category_path": "_Unsorted_Review",
            "confidence": 0,
            "reason": "classification failed",
        }

    def _fallback_results(self, files: List[FileEntry]) -> List[Dict]:
        return [self._fallback_single(f.rel_path) for f in files]

    @classmethod
    def from_config(cls, config_path: Path) -> "OllamaClient":
        from sorter.taxonomy import load_taxonomy

        with open(config_path) as f:
            import yaml
            cfg = yaml.safe_load(f)
        ollama_cfg = cfg.get("ollama", {})
        age_cfg = cfg.get("age", {})
        return cls(
            endpoint=ollama_cfg.get("endpoint", "http://localhost:11434"),
            model=ollama_cfg.get("model", "qwen2.5-coder:7b"),
            fallback_model=ollama_cfg.get("fallback_model", "qwen2.5:1.5b"),
            timeout=ollama_cfg.get("timeout", 60),
            batch_size=ollama_cfg.get("batch_size", 15),
            delete_candidate_years=age_cfg.get("delete_candidate_years", 10.0),
            archive_years=age_cfg.get("archive_years", 5.0),
        )