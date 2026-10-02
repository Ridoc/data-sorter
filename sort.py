#!/usr/bin/env python3
"""NAS AI File Sorter — CLI entry point.

Scans NAS mount, classifies files via local Ollama, interactive TUI review,
then moves files with CSV undo log. All operations reversible via --undo.

Usage:
  python sort.py                     # dry run → interactive → execute
  python sort.py --dry-run           # preview only
  python sort.py --undo              # revert last sort run
  python sort.py --config my.yaml    # custom config
"""

import sys
import re
import argparse
from pathlib import Path
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> Dict:
    import yaml
    with open(config_path) as f:
        return yaml.safe_load(f)


def resolve_paths(cfg: Dict) -> dict:
    nas_root = Path(cfg.get("nas", {}).get("root", "/mnt/NAS-Zeno/"))
    log_dir = cfg.get("logging", {}).get("csv_dir", ".sort_logs")
    csv_name = cfg.get("logging", {}).get("undo_csv", "sort_undo.csv")
    csv_path = nas_root / log_dir / csv_name
    trash_dir_name = cfg.get("dedup", {}).get("trash_dir", "_Duplicates_Delete")
    trash_root = nas_root / "Archives"
    return {
        "nas_root": nas_root,
        "csv_path": csv_path,
        "trash_root": trash_root,
        "trash_dir_name": trash_dir_name,
    }


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_scan(nas_root: Path, cfg: Dict, sub_path: Optional[str] = None) -> List:
    """Discover files on the NAS, respecting .sortignore."""
    from sorter.scanner import walk_nas, load_ignore_patterns, get_mime

    ignore_file = Path(".sortignore")
    patterns = load_ignore_patterns(ignore_file) if ignore_file.exists() else []
    max_depth = cfg.get("nas", {}).get("max_depth")
    exclude = cfg.get("nas", {}).get("exclude", [])

    # If --path given, scan only that sub-path for speed
    scan_root = Path(sub_path) if sub_path else nas_root

    all_files = walk_nas(scan_root, patterns, max_depth=max_depth)
    # Fix rel_path to be relative to nas_root (not scan_root)
    if sub_path:
        rel_prefix = str(scan_root.relative_to(nas_root))
        from sorter.scanner import FileEntry
        fixed = []
        for f in all_files:
            fixed.append(FileEntry(
                path=f.path, rel_path=f"{rel_prefix}/{f.rel_path}",
                name=f.name, mime=f.mime, size=f.size,
                mtime=f.mtime, is_dir=f.is_dir,
                ctime=f.ctime, atime=f.atime,
            ))
        all_files = fixed
    # Filter out exclude dirs at root level
    filtered = []
    for f in all_files:
        skip = False
        for ex in exclude:
            if str(nas_root / ex) in str(f.path):
                skip = True
                break
        if not skip:
            filtered.append(f)
    return filtered


def step_extract(entries: List) -> Dict[str, Dict]:
    """Extract content/metadata from files (parallel)."""
    from sorter.extractor import extract_batch
    return {e.rel_path: c for e, c in zip(entries, extract_batch(entries))}


def step_get_taxonomy(config_path: Path) -> str:
    """Load taxonomy as YAML string for prompts."""
    from sorter.taxonomy import get_taxonomy_yaml_string
    return get_taxonomy_yaml_string(config_path)


def _apply_dominant_fallback(results: List[Dict], folder_entries: List[Dict]) -> None:
    """Pin a deterministic category for folders the LLM did not classify.

    Mutates results in place. Only touches entries the LLM left unanswered
    (category _Unsorted_Review / confidence 0) AND whose extensions show a
    clear majority media kind — a last-resort net, never a second opinion.
    """
    from sorter.routing import dominant_media_kind

    kind_to_category = {
        "audio": "Media/Music",
        "video": "Media/Videos",
        "image": "Media/Photos",
        "doc": "Zeno/Documents",
        "design": "Zeno/Documents/Design",
        "binary": "Zeno/Downloads",
    }
    entries_by_path = {fe.get("path"): fe for fe in folder_entries}
    for r in results:
        if r.get("_nest_flagged"):
            continue  # our own guard wants this one reviewed by a human
        if r.get("category_path", "") != "_Unsorted_Review" and r.get("confidence", 0) > 0:
            continue  # LLM gave a verdict — respect it
        folder_entry = entries_by_path.get(r.get("path", ""))
        if not folder_entry:
            continue
        dom_kind = dominant_media_kind(folder_entry.get("top_extensions", {}), 0.5)
        override_cat = kind_to_category.get(dom_kind, "") if dom_kind else ""
        if not override_cat:
            continue
        r["category_path"] = override_cat
        r["confidence"] = 90
        r["reason"] = (r.get("reason") or "") + (
            f" [Det fallback: dominant {dom_kind} → {override_cat} (LLM failed)]"
        )
    for r in results:
        r.pop("_nest_flagged", None)


FOLDER_RULES = (
    '"- Classify by SUBJECT first (folder/filenames reveal purpose), NOT by dominant file type.\\n"\n'
    '"- Image files do NOT always mean Media/Photos. Decide by WHAT the images\\n"\n'
    '"  depict, not by the medium or the dominant file type.\\n"\n'
    '"- Branding, layouts, mockups and drafts (logos, banners, buttons, icons,\\n"\n'
    '"  graphics) -> Zeno/Documents/Design.\\n"\n'
    '"- Artwork of game characters, avatars or a project\'s visual identity\\n"\n'
    '"  -> Zeno/Documents/Design.\\n"\n'
    '"- Scanned papers (Zeugnis, Bescheinigung, Rechnung, Vertrag, letter,\\n"\n'
    '"  invoice, certificate) -> Zeno/Documents.\\n"\n'
    '"- Opaque camera filenames (09-09-05_1740.jpg, IMG_4821.jpg) reveal no\\n"\n'
    '"  subject at all — those are personal photos -> Media/Photos.\\n"\n'
    '"- Never route a folder to Design merely because it is uncertain;\\n"\n'
    '"  send it to \'_Unsorted_Review\' instead.\\n"\n'
    '"- The design subfolder lives under Zeno/Documents/Design, never a\\n"\n'
    '"  top-level Zeno/Design.\\n"\n'
    '"\\n"\n'
    '"NAMING — A folder named only after the medium (Bilder, bilder, pics,\\n"\n'
    '"Fotos, Photos, Screens, Bilder&screens) carries no subject of its own.\\n"\n'
    '"Do NOT reuse the medium word as the destination name. Name the\\n"\n'
    '"destination for its OWNER, EVENT or depicted SUBJECT instead:\\n"\n'
    '"  Tina/bilder     -> Media/Photos/Tina   (owner, from the parent path)\\n"\n'
    '"  zenos bilder    -> Media/Photos/Zeno   (owner, from the folder name)\\n"\n'
    '"  Realtreffenpics -> Media/Photos/Real Life Treffen   (the event)\\n"\n'
    '"  Char Bilder     -> Zeno/Documents/Design/Chars   (character artwork)\\n"\n'
    '"The owner or event may come from the PARENT path, not only the folder.\\n"\n'
    '"These descriptive owner/event names MAY be English or bilingual even\\n"\n'
    '"for a German source folder — the only exception to language preservation.\\n"\n'
    '"Whenever you name the destination this way, set own_target to true so\\n"\n'
    '"your leaf name is used verbatim instead of the source folder name.\\n"\n'
    '"If the folder name is purely a medium word and the category should hold\\n"\n'
    '"the files directly, return the bare category_path and set\\n"\n'
    '"dissolve to true:\\n"\n'
    '"  bilder&screens  -> category_path "Media/Photos", dissolve true\\n"\n'
    '"- category_path must start with a top-level folder (Zeno, Family, Company, Projects, Media, Archives)\\n"\n'
    '"- MANDATORY: If Context hint is AUDACITY_PROJECT, the folder MUST be routed to "\n'
    '"Projects/<ProjectName>/Audio/<source_folder_name>. "\n'
    '"The <ProjectName> is derived from the folder name (e.g. Hypnose -> Projects/Hypnose/Audio/Hypnose). "\n'
    '"ALWAYS use Projects/<Name>/Audio structure. NEVER route audio projects to Documents, Finance, or Career.\\n"\n'
    '"- Context hint AUDACITY_PROJECT -> classify by folder name/subject, NEVER delete\\n"\n'
    '"- Context hint ADOBE_PREMIERE_TEMP -> DELETE\\n"\n'
    '"- Age classification OLD_TEMP_BACKUP -> DELETE (untouched 10+ years)\\n"\n'
    '"- Age classification OLD_USER_CONTENT -> Archives/Old_Projects\\n"\n'
    '"- MANDATORY LANGUAGE PRESERVATION: The last segment of category_path MUST be the "\n'
    '"source folder\'s own name (verbatim, original language). NEVER translate or anglicize "\n'
    '"German/Greek folder names. (The NAMING owner/event rule above is the only "\n'
    '"carve-out.)\\n"\n'
    '"- If a new subfolder is needed for non-English content, name it in the source "\n'
    '"content\'s language (e.g. for German \'Entwürfe Logo\' use \'Entwürfe_Logo\', never "\n'
    '"\'Design\' or \'Logo_Drafts\').\\n"\n'
    '"- Only the top 1-2 taxonomy levels (e.g. Zeno/Documents, Media/Photos) may be in "\n'
    '"the primary (English) language.\\n"\n'
    '"- When in doubt about language, keep the source folder\'s exact name.\\n"\n'
    '"- If unsure, send to \'_Unsorted_Review\'\\n"\n'
    '"- is_folder must be true\\n"\n'
    '"- Return ONLY the JSON array"'
)


def _taxonomy_prefix(cat_path: str, tax_dict: Dict) -> str:
    """Longest leading part of cat_path that is a real taxonomy category.

    The LLM often pastes the source ancestry into category_path
    ("Media/Photos/EVE/Neues YW Forum - Grafik"); the destination leaf is ours
    to append, so only the genuine category prefix is kept.
    """
    categories = tax_dict.get("categories", {})
    kept: List[str] = []
    for seg in [s for s in (cat_path or "").replace("\\", "/").split("/") if s]:
        if kept:
            if seg not in categories.get(kept[0], []):
                break
        elif seg not in categories:
            break
        kept.append(seg)
    return "/".join(kept)


def _apply_medium_word_naming(folder_entry: Dict, classification: Dict, tax_dict: Dict) -> None:
    """Name the destination for medium-word folders, deterministically. In-place.

    WHY in code and not in the prompt: the model picks these destinations
    correctly but cannot reliably emit the own_target boolean that tells the
    pipeline to keep its leaf (measured 5/15 then 7/15 across two runs). Without
    the flag, resolve_folder_move injects the source ancestry a SECOND time,
    producing paths like "Media/Photos/Projekt Müller/Angebot/Ausbildung/Schule/
    Projekt Müller/Angebot/Gruppe 1/Bilder". The model still owns the CATEGORY;
    only the leaf and the dissolve decision are ours.
    """
    from sorter.folder_classifier import (
        is_medium_word_name, derive_medium_word_leaf, meaningful_ancestors,
    )
    source_name = Path(folder_entry.get("path", "")).name
    if not is_medium_word_name(source_name):
        return
    cat_path = classification.get("category_path", "")
    # A model-chosen leaf is genuine when its segments do NOT occur in the
    # source path ("Media/Photos/Zeno" for "zenos bilder" -> trust it). When they
    # DO ("Media/Photos/EVE/-Y-/old_G_files"), the model pasted the source
    # ancestry and we derive the name instead. This replaces the own_target
    # boolean, which the model emitted only 5/15 then 7/15 of the time.
    norm = lambda s: re.sub(r"\s+", " ", (s or "").lower().replace("_", " ")).strip()
    prefix = _taxonomy_prefix(cat_path, tax_dict)
    segs = [s for s in cat_path.replace("\\", "/").split("/") if s]
    extra = segs[len(prefix.split("/")):] if prefix else segs
    src_segs = {norm(s) for s in (folder_entry.get("path") or "").replace("\\", "/").split("/")}
    if (classification.get("own_target") and extra
            and not any(norm(e) in src_segs for e in extra)):
        return  # model named a genuine leaf AND flagged it — respect it
    leaf = derive_medium_word_leaf(source_name)
    if leaf is None:
        # Nothing but the medium in the name: the owner/event may live in the
        # parent. Only trust it when the parent is ITSELF a meaningful name —
        # "Tina/bilder" is Tina's photos, but "…/old_G_files/bilder&screens" is
        # a dump inside a junk container and must dissolve instead of being
        # named after whatever ancestor survived filtering ("EVE").
        rel_segs = [s for s in (folder_entry.get("path") or "").replace("\\", "/").split("/") if s]
        ancestors = meaningful_ancestors(
            folder_entry.get("path", ""), source_name,
            scan_root=rel_segs[0] if rel_segs else None)
        parent = rel_segs[-2] if len(rel_segs) >= 2 else ""
        norm = lambda s: re.sub(r"\s+", " ", (s or "").lower().replace("_", " ")).strip()
        leaf = ancestors[-1] if ancestors and norm(ancestors[-1]) == norm(parent) else None
    # Always normalize to the genuine taxonomy prefix — the model often pastes
    # the source ancestry onto category_path ("Media/Photos/EVE/-Y-/old_G_files").
    prefix = _taxonomy_prefix(classification.get("category_path", ""), tax_dict)
    if leaf:
        classification["category_path"] = f"{prefix}/{leaf}" if prefix else leaf
        classification["own_target"] = True
        # The model sometimes sets dissolve speculatively; a derived name means
        # the folder IS being named, so that flag would discard the name.
        classification["dissolve"] = False
    else:
        classification["category_path"] = prefix
        classification["own_target"] = False
        classification["dissolve"] = True


def _build_folder_prompt(entries_str: str, taxonomy_str: str) -> str:
    """Folder classification prompt — shared by the first pass and the retry.

    The retry MUST reuse these rules: a bare re-ask made the model fall back
    to type-based guesses (logo folders -> Media/Photos).
    """
    return (
        "You are a NAS file organizer. Given folder summaries, classify each folder "
        "into the appropriate location.\n\n"
        f"EXISTING FOLDER TAXONOMY:\n{taxonomy_str}\n\n"
        "FOLDERS TO CLASSIFY:\n" + entries_str + "\n\n"
        "For each folder, respond with a JSON array:\n"
        '[{"path": "relative/folder/path", "category_path": "Zeno/Documents/FolderName", '
        '"is_folder": true, "confidence": 85, '
        '"reason": "Coherent folder of hypnosis audio files", "dissolve": false, "own_target": false}]\n\n'
        "Rules:\n" + FOLDER_RULES
    )


def _call_folder_batch(client, prompt: str, batch: List[Dict]) -> List[Dict]:
    """Call the LLM for one folder batch; never raises.

    Returns only the folders the model actually answered for — a dropped
    entry is left out so the caller can retry it instead of receiving a
    fake review result.
    """
    try:
        resp = client._call_ollama(prompt)
    except Exception:
        return []
    try:
        results = client._parse_response(resp)
    except Exception:
        return []
    for r in results:
        # The model often echoes the folder path with a trailing slash
        # ("Diverse Daten/EVE/BO Logo/") — normalize or the result never
        # matches its folder and a good verdict gets thrown away.
        r["path"] = (r.get("path") or "").strip().rstrip("/")
        r["is_folder"] = True
    return results


def _classify_folders(
    folder_entries: List[Dict], config_path: Path, cfg: Dict, taxonomy_str: str
) -> List[Dict]:
    """Classify folder entries (cohesive directories) via Ollama."""
    from sorter.classifier import OllamaClient
    from sorter.folder_classifier import format_folder_for_prompt
    from sorter.taxonomy import resolve_category, load_taxonomy
    from sorter.context import context_hint

    client = OllamaClient.from_config(config_path)
    taxonomy = load_taxonomy(config_path)
    batch_size = cfg.get("ollama", {}).get("batch_size", 15)
    all_results = []

    # Pre-compute context hints for each folder entry (for deterministic override)
    folder_hints: Dict[str, str] = {}
    for fe in folder_entries:
        fpath = fe.get("path", "")
        folder_hints[fpath] = context_hint(fpath, fe.get("top_extensions", {}))

    for i in range(0, len(folder_entries), batch_size):
        batch = folder_entries[i : i + batch_size]
        # Build folder-specific prompt
        entries_str = "\n---\n".join(format_folder_for_prompt(fe) for fe in batch)
        prompt = _build_folder_prompt(entries_str, taxonomy_str)
        all_results.extend(_call_folder_batch(client, prompt, batch))

    # ── Retry folders the LLM omitted ──
    # The model silently drops entries when a batch is large (observed: 6 of
    # ~100 folders came back with no result at all). Re-ask for just those,
    # one small batch, so a dropped folder does not end up in review.
    answered = {r.get("path", "") for r in all_results}
    missing = [fe for fe in folder_entries if fe["path"] not in answered]
    if missing:
        for i in range(0, len(missing), batch_size):
            retry_batch = missing[i : i + batch_size]
            entries_str = "\n---\n".join(format_folder_for_prompt(fe) for fe in retry_batch)
            retry_prompt = (
                _build_folder_prompt(entries_str, taxonomy_str)
                + f"\n\nIMPORTANT: answer for ALL {len(retry_batch)} folder(s) listed above. "
                "Return one JSON object per folder, using each folder's exact path "
                "as given, and no others."
            )
            all_results.extend(_call_folder_batch(client, retry_prompt, retry_batch))

    # ── Deterministic override for audio project folders ──
    # If the LLM ignored the AUDACITY_PROJECT routing rule, override it.
    for r in all_results:
        fpath = r.get("path", "")
        hint = folder_hints.get(fpath, "NONE")
        if hint == "AUDACITY_PROJECT":
            cat = r.get("category_path", "")
            if not cat.startswith("Projects/"):
                parts = [p for p in fpath.split("/") if p]
                folder_name = parts[-1] if parts else "Project"
                r["category_path"] = f"Projects/{folder_name}/Audio/{folder_name}"
                r["reason"] = (r.get("reason") or "") + f" [Det: audio project → Projects/{folder_name}/Audio]"
                r["confidence"] = max(r.get("confidence", 0), 85)

    # ── Self-nesting guard & cascade prevention ──
    # If the LLM routes a folder to a category_path that contains the source
    # folder name as a non-leaf element, it's cascade-routing (the folder
    # is being classified as one of its own children).
    for r in all_results:
        fpath = r.get("path", "")
        if not fpath:
            continue
        source_name = Path(fpath).name.lower()
        cat_path = r.get("category_path", "")
        if not cat_path:
            continue
        cat_segments = [s.lower() for s in cat_path.split("/") if s]
        top_levels = {"zeno", "family", "company", "projects", "media", "archives"}
        content_segments = [s for s in cat_segments if s not in top_levels]
        # Cascade: source folder name appears in middle of category path
        # (not as the last content segment)
        if source_name in content_segments and content_segments[-1] != source_name:
            r["category_path"] = "_Unsorted_Review"
            r["confidence"] = min(r.get("confidence", 50), 30)
            r["_nest_flagged"] = True  # keep out of the dominant-type fallback
            r["reason"] = (r.get("reason") or "") + f" [Nest guard: self-nesting flagged]"

    # ── Re-key onto input order (LLM returns results in arbitrary order) ──
    # Callers zip folder_entries with these results, so a positional mismatch
    # hands each folder another folder's category (e.g. "Rammstein 2010"
    # routed to "Sunset Villa"). Match on the path the LLM echoed back;
    # anything missing/duplicated becomes a review entry so count and order
    # always line up.
    by_path: Dict[str, Dict] = {}
    for r in all_results:
        rpath = r.get("path", "")
        if rpath and rpath not in by_path:
            by_path[rpath] = r
    ordered: List[Dict] = []
    for fe in folder_entries:
        r = by_path.get(fe["path"])
        if r is None:
            r = {"path": fe["path"], "category_path": "_Unsorted_Review",
                 "is_folder": True, "confidence": 0, "reason": "no result for folder"}
        r["is_folder"] = True
        ordered.append(r)

    # ── Medium-word naming refinement ──
    # The main pass packs ~15 folders per prompt; the model applies the NAMING
    # rules (owner/event, own_target, dissolve) to a handful of folders but
    # drops them in a full batch. Re-ask ONLY the medium-word-named folders as
    # one small batch so those rules actually land.
    from sorter.folder_classifier import is_medium_word_name
    med_idx = [i for i, fe in enumerate(folder_entries)
               if is_medium_word_name(Path(fe["path"]).name)]
    if med_idx and len(med_idx) < len(folder_entries):
        refined = _classify_folders(
            [folder_entries[i] for i in med_idx], config_path, cfg, taxonomy
        )
        replaced = 0
        for i, r in zip(med_idx, refined):
            if r.get("category_path") != "_Unsorted_Review":
                ordered[i] = r
                replaced += 1
        if replaced:
            print(f"     🔁 refined {replaced}/{len(med_idx)} medium-word folder names",
                  file=sys.stderr)

    # ── Dominant-type fallback (LLM-failure only) ──
    # Runs AFTER re-keying so it also covers folders the LLM never answered
    # for. Subject-first LLM judgment wins (content-understanding):
    # deterministic dominant-type routing fires ONLY when the LLM
    # failed/abstained — never override a confident (or even low-confidence)
    # verdict; low-confidence results go to user review anyway.
    _apply_dominant_fallback(ordered, folder_entries)

    return ordered


def step_classify(entries: List, config_path: Path, cfg: Dict) -> List[Dict]:
    """Classify files via Ollama in batches."""
    from sorter.classifier import OllamaClient
    from sorter.taxonomy import get_taxonomy_yaml_string, resolve_category

    client = OllamaClient.from_config(config_path)
    taxonomy = get_taxonomy_yaml_string(config_path)
    taxonomy_dict = None  # loaded on demand in resolve loop

    # Classify in batches
    all_results: List[Dict] = []
    batch_size = cfg.get("ollama", {}).get("batch_size", 15)
    for i in range(0, len(entries), batch_size):
        batch = entries[i : i + batch_size]
        batch_results = client.classify_batch(batch, taxonomy)
        all_results.extend(batch_results)

    # Resolve categories
    from sorter.taxonomy import load_taxonomy
    taxonomy_dict = load_taxonomy(config_path)

    resolved = []
    for entry, result in zip(entries, all_results):
        result["path"] = entry.rel_path  # ensure path is set
        resolved_entry = resolve_category(result, taxonomy_dict)

        # Validate suggested_name (BATCH 2: content-renaming)
        suggested = resolved_entry.get("suggested_name")
        if suggested:
            from sorter.classifier import validate_suggested_name, should_skip_rename
            if should_skip_rename(entry.mime):
                resolved_entry["suggested_name"] = None
            else:
                validated = validate_suggested_name(suggested, entry.name)
                resolved_entry["suggested_name"] = validated

        resolved.append(resolved_entry)
    return resolved


def step_dedup(entries: List, cfg: Dict) -> List[Dict]:
    """Find duplicate files — copy-suffix, exact hash, then image near-dupes."""
    from sorter.deduper import DedupScanner, find_copy_duplicates
    dedup_cfg = cfg.get("dedup", {})

    # Phase 1: Copy-suffix dedup (name vs name (2).ext)
    copy_pairs_raw = find_copy_duplicates(entries)
    copy_pairs = [p for p in copy_pairs_raw if p.get("type") == "copy_suffix"]

    # Track paths already flagged as duplicate
    seen_delete: set = set()
    for p in copy_pairs:
        seen_delete.add(p["delete"].path)

    # Phase 2: SHA-256 + phash dedup (skip already-flagged)
    remaining = [e for e in entries if e.path not in seen_delete]
    scanner = DedupScanner(dedup_cfg)
    hash_pairs = scanner.scan_all(remaining)

    # Phase 3: Size-differing copy suffix pairs (flag but don't auto-dup)
    size_diff_pairs = [p for p in copy_pairs_raw if p.get("type") == "copy_suffix_size_diff"]

    all_pairs = copy_pairs + hash_pairs + size_diff_pairs
    return scanner.format_for_review(all_pairs)


def step_review(classifications: List[Dict], dedup_pairs: List[Dict],
                nas_root: Path, dry_run: bool):
    """Interactive review via Rich TUI."""
    from sorter.reviewer import ReviewSession
    session = ReviewSession(classifications, dedup_pairs, nas_root, dry_run=dry_run)
    return session.run()


def _classify_with_progress(
    entries: List, config_path: Path, cfg: Dict,
    console, batch_size: int,
) -> List[Dict]:
    """Classify files via Ollama with per-file live output.
    
    Shows analyzing progress bar + per-file results as each batch completes.
    """
    from sorter.classifier import OllamaClient
    from sorter.taxonomy import get_taxonomy_yaml_string, resolve_category, load_taxonomy

    client = OllamaClient.from_config(config_path)
    taxonomy = get_taxonomy_yaml_string(config_path)
    taxonomy_dict = load_taxonomy(config_path)
    all_results: List[Dict] = []
    total = len(entries)
    total_batches = (total + batch_size - 1) // batch_size
    done = 0

    # Progress bar for the analyzing phase
    from rich.progress import (Progress, BarColumn, TextColumn,
                               TimeElapsedColumn, TimeRemainingColumn)
    from rich.table import Table

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("<"),
        TimeRemainingColumn(),
        TextColumn("• {task.completed}/total files"),
        console=console,
    ) as prog:
        task = prog.add_task(f"Analyzing {total} files with {cfg.get('ollama',{}).get('model','qwen2.5-coder:7b')}...",
                             total=total, completed=0)

        for i in range(0, len(entries), batch_size):
            batch = entries[i : i + batch_size]
            batch_num = i // batch_size + 1

            batch_results = client.classify_batch(batch, taxonomy)
            all_results.extend(batch_results)

            # Resolve and display per-file results for this batch
            for entry, result in zip(batch, batch_results):
                result["path"] = entry.rel_path
                resolved = resolve_category(result, taxonomy_dict)
                # Update result in-place with resolved values
                result.update(resolved)
                confidence = result.get("confidence", 0)
                cat = result.get("category_path", "_Unsorted_Review")
                reason = result.get("reason", "")
                fname = Path(entry.rel_path).name
                if len(fname) > 45:
                    fname = fname[:42] + "..."

                # Color confidence
                if confidence >= 85:
                    color = "green"
                elif confidence >= 50:
                    color = "yellow"
                else:
                    color = "red"

                # Color confidence and truncate reason
                reason_short = reason[:60] if reason else ""
                if result.get("action") == "delete" or cat == "_Unsorted_Review":
                    if result.get("action") == "delete":
                        console.print(f"  [red]🗑️  {fname}[/] → [red]_{cat}[/] [dim]({confidence}%)[/]")
                    else:
                        console.print(f"  [dim]{fname}[/] → [red]_{cat}[/] [dim]({confidence}%)[/] {reason_short}")
                else:
                    console.print(f"  [bold]{fname}[/] → [cyan]{cat}[/] [dim]({confidence}%)[/] {reason_short}")

                # Show rename suggestion if present
                suggested = result.get("suggested_name")
                if suggested:
                    og = Path(entry.rel_path).name
                    if Path(suggested).name.lower() != og.lower():
                        detected_lang = "?"
                        reason = result.get("reason", "")
                        lang_match = __import__('re').search(r'(detected|language|lang)[:\s]+([a-z]{2})', reason, __import__('re').IGNORECASE)
                        if lang_match:
                            detected_lang = lang_match.group(2)
                        console.print(f"     [dim]📝 rename → [green]{suggested}[/] (lang: {detected_lang})[/]")

                prog.update(task, advance=1)
                done += 1

    return all_results


def step_execute(approved_moves: List[Dict], approved_deletions: List[Dict],
                 nas_root: Path, csv_path: Path, trash_root: Path,
                 dry_run: bool) -> Dict:
    """Execute approved moves and deletions."""
    from sorter.executor import execute_moves, dry_run_summary
    if dry_run:
        summary = dry_run_summary(approved_moves, approved_deletions)
        from rich.console import Console
        console = Console()
        console.print(f"\n[bold cyan]📊 DRY RUN Summary:[/]")
        console.print(f"  Files to move:   {summary['to_move']}")
        console.print(f"  Files to delete: {summary['to_delete']}")
        if summary['space_freed_estimate']:
            from sorter.deduper import DedupScanner
            freed = DedupScanner._format_size(summary['space_freed_estimate'])
            note = " (deletions free disk; moves reorganize)" if summary['to_delete'] and summary['to_move'] else \
                   " (moves reorganize, don't free disk)" if summary['to_move'] else ""
            console.print(f"  Est. volume: {freed}{note}")
        return {"moved": 0, "deleted": 0, "failed": 0, "csv_path": str(csv_path)}
    return execute_moves(approved_moves, approved_deletions, nas_root, csv_path, trash_root)


def step_undo(cfg: Dict, nas_root: Path, csv_path: Path, dry_run: bool):
    """Undo the last sort run."""
    from sorter.undo import undo_last_run, get_last_run_path
    if not csv_path.exists():
        alt = get_last_run_path(nas_root)
        if alt:
            csv_path = alt
        else:
            print("No undo log found. Nothing to undo.")
            return {"reverted": 0, "skipped": 0, "failed": 0}
    result = undo_last_run(csv_path, nas_root, dry_run=dry_run)
    from rich.console import Console
    console = Console()
    console.print(f"\n[bold yellow]↩ Undo Summary:[/]")
    console.print(f"  Reverted: {result['reverted']}")
    console.print(f"  Skipped:  {result['skipped']}")
    console.print(f"  Failed:   {result['failed']}")
    if dry_run:
        console.print("[dim](dry run — no files changed)[/]")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="NAS AI File Sorter — organize your NAS files with a local LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sort.py                     Dry run → interactive review → execute
  python sort.py --dry-run           Preview only, no changes
  python sort.py --undo              Revert last sort run
  python sort.py --config my.yaml    Use alternate config
        """,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — no files will be moved or deleted")
    parser.add_argument("--undo", action="store_true",
                        help="Revert the last sort run (reads CSV undo log)")
    parser.add_argument("--execute", action="store_true",
                        help="Skip interactive review, auto-accept all")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last interrupted session")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to configuration YAML (default: config.yaml)")
    parser.add_argument("--path", default=None,
                        help="Scan a specific sub-path instead of full NAS root")
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return 1
    cfg = load_config(config_path)
    paths = resolve_paths(cfg)
    nas_root = paths["nas_root"]
    csv_path = paths["csv_path"]
    trash_root = paths["trash_root"]

    # Handle --undo
    if args.undo:
        step_undo(cfg, nas_root, csv_path, dry_run=args.dry_run)
        return 0

    from rich.console import Console
    from rich.progress import (Progress, SpinnerColumn, TextColumn,
                               BarColumn, TimeElapsedColumn)
    console = Console()

    # ── Scan ──
    scan_target = args.path or str(nas_root)
    console.print(f"\n[bold blue]🔍 Scanning[/] [dim]{scan_target}[/]")
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        console=console, transient=True,
    ) as prog:
        task = prog.add_task("Scanning directories...", total=None)
        all_files = step_scan(nas_root, cfg, sub_path=args.path)
        prog.update(task, completed=True)
    files = [f for f in all_files if not f.is_dir]
    dir_count = len(all_files) - len(files)
    console.print(f"   [green]Found {len(files)} files[/] in {dir_count} directories")

    # ── Group cohesive folders ──
    from sorter.folder_classifier import identify_cohesive_folders, make_folder_entry
    cohesive_folders, remaining_files = identify_cohesive_folders(files, min_files=2, nas_root=nas_root)
    # Exclude the scan root itself from folder grouping (root must never be
    # classified as a folder unit — e.g. "Diverse Daten/ → individual").
    # Files directly under the root are returned to individual classification.
    scan_root_path = Path(scan_target).resolve()
    root_entries = cohesive_folders.pop(scan_root_path, None)
    if root_entries is not None:
        remaining_files.extend(root_entries)
    if cohesive_folders:
        console.print(f"\n[bold cyan]📁 Cohesive folders:[/]")
        for dir_path, entries in cohesive_folders.items():
            console.print(f"   📁 [bold]{dir_path.name}/[/] ({len(entries)} files)")
        folder_entries = [
            make_folder_entry(dp, ents, nas_root)
            for dp, ents in cohesive_folders.items()
        ]
    else:
        folder_entries = []

    # ── Classify folders ──
    folder_classifications = []
    if folder_entries:
        console.print(f"\n[bold blue]🧠 Classifying folders[/] [dim]{cfg.get('ollama',{}).get('model','qwen2.5-coder:7b')}[/]")
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console, transient=True,
        ) as prog:
            task = prog.add_task(f"Classifying {len(folder_entries)} folder(s)...", total=len(folder_entries))
            taxonomy = step_get_taxonomy(config_path)
            folder_results = _classify_folders(folder_entries, config_path, cfg, taxonomy)
            folder_classifications = folder_results
            prog.update(task, completed=len(folder_entries))

    # Resolve folder moves (with name canonicalization)
    from sorter.folder_classifier import resolve_folder_move, make_review_move
    from sorter.context import canonicalize_path
    approved_folder_moves = []
    from sorter.scanner import FileEntry
    from sorter.taxonomy import load_taxonomy
    tax_dict = load_taxonomy(config_path)
    for fe, fc in zip(folder_entries, folder_classifications):
        # Canonicalize: preserve exact source folder name (Hypnosis → Hypnose)
        source_unit = Path(fe["path"]).name if fe["path"] else ""
        orig_cat = fc.get("category_path", "")
        fc["category_path"] = canonicalize_path(orig_cat, source_unit)

        # Case-normalize second-level child (Media/videos → Media/Videos)
        cat_path = fc.get("category_path", "")
        if cat_path:
            cat_segs = cat_path.split("/")
            if len(cat_segs) >= 2:
                top = cat_segs[0]
                known_children = tax_dict.get("categories", {}).get(top, [])
                child = cat_segs[1]
                canonical_child = next((k for k in known_children if k.lower() == child.lower()), None)
                if canonical_child and canonical_child != child:
                    cat_segs[1] = canonical_child
                    fc["category_path"] = "/".join(cat_segs)

        # ── Medium-word folders: name the destination deterministically ──
        _apply_medium_word_naming(fe, fc, tax_dict)

        move = resolve_folder_move(fe, fc, nas_root)
        if move and fc.get("confidence", 0) >= cfg.get("confidence", {}).get("require_review", 50):
            if fc.get("dissolve"):
                # Medium-word folder ("bilder&screens"): drop it into the category
                # itself so no media-word subfolder survives. Bypass
                # resolve_folder_move's leaf-append + language guard, which would
                # both re-add the source name we are trying to discard.
                move["_dissolve"] = True
                move["target"] = nas_root / fc["category_path"]
                approved_folder_moves.append(move)
                console.print(f"     📁 [bold cyan]{fe['path']}/[/] → [green]{fc.get('category_path', '?')}/[/] (dissolved, {fc.get('confidence', 0)}%)")
            else:
                approved_folder_moves.append(move)
                console.print(f"     📁 [bold cyan]{fe['path']}/[/] → [green]{fc.get('category_path', '?')}[/] ({fc.get('confidence', 0)}%)")
        else:
            # Failed/uncertain folder → keep as a UNIT in _Unsorted_Review.
            # Never scatter children into individual classification: that
            # produced garbage routing (loose .htm → Projects/ERGO_Paphos).
            # User reviews ONE folder entry instead of N scattered files.
            approved_folder_moves.append(make_review_move(fe, nas_root))
            console.print(f"     📁 [yellow]{fe['path']}/ → _Unsorted_Review (review as unit)[/]")

    # Files to classify individually (folder failures stay as units — see above)
    individual_targets = remaining_files

    # ── Pre-classify by deterministic extension rules ──
    routing_classifications: Dict[str, Dict] = {}
    for entry in individual_targets:
        from sorter.scanner import FileEntry
        if not isinstance(entry, FileEntry):
            continue
        from sorter.routing import route_by_extension
        cat = route_by_extension(entry.rel_path, entry.mime)
        if cat:
            from sorter.taxonomy import resolve_category, load_taxonomy
            tax_dict = load_taxonomy(config_path)
            result = resolve_category({"path": entry.rel_path, "category_path": cat,
                                       "confidence": 95, "reason": f"[Det] extension rule"},
                                      tax_dict)
            routing_classifications[entry.rel_path] = result

    # Drop extension-routed from LLM batch
    individual_targets = [
        e for e in individual_targets
        if isinstance(e, dict) and e.get("is_folder")
        or (isinstance(e, FileEntry) and e.rel_path not in routing_classifications)
    ]
    if routing_classifications:
        console.print(f"  [bold green]⚡ Extension rules: {len(routing_classifications)} files[/]")

    # ── Pre-classify by centroid matching ──
    centroid_classifications = {}
    emb_cfg = cfg.get("embeddings", {})
    if emb_cfg.get("enabled", False) and len(individual_targets) > 0:
        from sorter.embeddings import (EmbeddingCache, build_folder_centroids,
                                       embed_batch, match_folder, text_for_embedding)
        from sorter.taxonomy import load_taxonomy
        cache = EmbeddingCache(nas_root)
        tax_dict = load_taxonomy(config_path)
        centroids = build_folder_centroids(nas_root, tax_dict, cache, force_rebuild=False)
        if centroids:
            threshold = emb_cfg.get("similarity_threshold", 0.85)
            model = emb_cfg.get("model", "nomic-embed-text")
            endpoint = cfg.get("ollama", {}).get("endpoint", "http://localhost:11434")
            console.print(f"\n[bold yellow]🔗 Centroid matching {len(individual_targets)} files[/] [dim]threshold={threshold}[/]")

            # Batch-embed files that aren't cached yet
            from sorter.scanner import FileEntry
            to_embed = [
                e for e in individual_targets
                if isinstance(e, FileEntry) and not cache.get(e)
            ]
            if to_embed:
                embed_strs = [text_for_embedding(e, "") for e in to_embed]
                embeddings = embed_batch(embed_strs, model, endpoint)
                for entry, emb in zip(to_embed, embeddings):
                    if emb and any(x != 0.0 for x in emb):
                        cache.set(entry, emb)
                cache.flush()

            # Match all files against centroids
            matched_count = 0
            for entry in individual_targets:
                if isinstance(entry, dict) and entry.get("is_folder"):
                    continue
                if not isinstance(entry, FileEntry):
                    continue
                emb = cache.get(entry)
                if not emb:
                    continue
                folder, score = match_folder(emb, centroids, threshold)
                if folder:
                    centroid_classifications[entry.rel_path] = {
                        "path": entry.rel_path,
                        "category_path": folder,
                        "confidence": int(min(score * 100, 99)),
                        "reason": f"Centroid: {folder} ({score:.2f})",
                    }
                    matched_count += 1

            # Drop centroid-matched from LLM batch
            individual_targets = [
                e for e in individual_targets
                if isinstance(e, dict) and e.get("is_folder")
                or (isinstance(e, FileEntry) and e.rel_path not in centroid_classifications)
            ]
            console.print(f"       Centroid matches: {matched_count} files | LLM: {len(individual_targets)} remaining")
        else:
            console.print(f"\n[dim]No folder centroids computed — skipping centroid match[/]")

    # ── Classify individual files ──
    file_classifications = []
    if individual_targets and not all(
        isinstance(e, dict) and e.get("is_folder") for e in individual_targets
    ):
        console.print(f"\n[bold blue]🧠 Analyzing {len(individual_targets)} files[/] [dim]{cfg.get('ollama',{}).get('model','qwen2.5-coder:7b')}[/]")
        batch_size = cfg.get("ollama", {}).get("batch_size", 15)
        try:
            file_classifications = _classify_with_progress(
                individual_targets, config_path, cfg, console, batch_size
            )
        except ConnectionError as e:
            console.print(f"\n[red]Error: {e}[/]")
            console.print("Start Ollama: [bold]ollama serve[/] or check endpoint in config.yaml")
            return 1
    else:
        console.print("[dim]No individual files to classify[/]")

    # ── Merge routing + centroid + LLM classifications ──
    if routing_classifications:
        file_classifications = list(routing_classifications.values()) + file_classifications
    if centroid_classifications:
        file_classifications = list(centroid_classifications.values()) + file_classifications

    # ── Merge classifications ──
    all_classifications = file_classifications + [
        # A dissolved folder's target IS the category, so don't take .parent
        # (which would report "Media" for a target of "Media/Photos").
        {"path": fm["source"],
         "category_path": str((Path(fm["target"]) if fm.get("_dissolve") else Path(fm["target"]).parent)
                              .relative_to(nas_root)),
         "confidence": fm["confidence"], "reason": fm["reason"], "action": "move",
         "is_folder": True, "_folder_move": fm}
        for fm in approved_folder_moves
    ]

    # ── Delete protection guard ──
    # User content (3D models, design sources, media, documents) must NEVER
    # be auto-deleted even when old/temp. Reroute protected files to
    # Archives/Old_Projects for manual review instead of deletion.
    from sorter.routing import should_protect_from_delete, dominant_media_kind
    protected_count = 0
    for c in all_classifications:
        fpath = c.get("path", "")
        if c.get("is_folder"):
            # Folder-level: check dominant file type
            fm = c.get("_folder_move", {})
            children = fm.get("_children", [])
            if children and c.get("action") == "delete":
                # If 50%+ of folder files are protected types → don't delete
                protected = sum(1 for ch in children if should_protect_from_delete(ch))
                if protected / max(len(children), 1) >= 0.5:
                    c["action"] = "move"
                    c["category_path"] = "Archives/Old_Projects"
                    c["reason"] = (c.get("reason") or "") + " [Delete-guard: user content → archive]"
                    c["confidence"] = min(c.get("confidence", 0), 85)
                    protected_count += 1
        else:
            # File-level
            if c.get("action") == "delete" and should_protect_from_delete(fpath):
                c["action"] = "move"
                c["category_path"] = "Archives/Old_Projects"
                c["reason"] = (c.get("reason") or "") + " [Delete-guard: user content → archive]"
                c["confidence"] = min(c.get("confidence", 0), 85)
                protected_count += 1
    if protected_count:
        console.print(f"  [bold yellow]🛡️  Delete-guard: {protected_count} item(s) rerouted to Archives (user content)[/]")

    # ── Classification summary table ──
    from collections import Counter, defaultdict
    cat_counts: Counter = Counter()
    # Track folder move stats for the summary
    folder_stats: List[Dict] = []
    for c in all_classifications:
        cat = c.get("category_path", "_Unsorted_Review")
        confidence = c.get("confidence", 0)
        action = c.get("action", "move")
        if c.get("is_folder"):
            fm = c.get("_folder_move", {})
            # Folders shown only in the dedicated "Folder moves" table below
            # (not in cat_counts — avoids double-listing each folder)
            folder_stats.append({
                "source": fm.get("source", ""),
                "target": fm.get("target", ""),
                "file_count": fm.get("file_count", 0),
                "total_size": fm.get("total_size", 0),
                "confidence": fm.get("confidence", confidence),
            })
            continue
        if action == "delete" or confidence < cfg.get("confidence", {}).get("require_review", 50):
            cat = "_Unsorted_Review"
        cat_counts[cat] += 1

    console.print(f"\n[bold]📊 Classification Summary[/]")
    from rich.table import Table
    tbl = Table(box=None)
    tbl.add_column("Category", style="cyan")
    tbl.add_column("Files", justify="right")
    tbl.add_column("Note", style="dim")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        if cat == "_Unsorted_Review":
            tbl.add_row(f"  🗑️  {cat}", str(cnt), "needs review / flagged for deletion")
        else:
            tbl.add_row(f"  📁 {cat}", str(cnt))
    console.print(tbl)

    # Folder moves detail
    if folder_stats:
        console.print(f"\n[bold]📁 Folder moves (as units):[/]")
        f_tbl = Table(box=None)
        f_tbl.add_column("Source", style="cyan")
        f_tbl.add_column("Target", style="green")
        f_tbl.add_column("Files", justify="right")
        f_tbl.add_column("Size", justify="right")
        for fs in folder_stats:
            src_name = str(Path(fs["source"]).relative_to(nas_root)) + "/" if fs["source"] else ""
            tgt = fs["target"]
            if tgt:
                tgt = str(Path(tgt).relative_to(nas_root))
            from sorter.deduper import DedupScanner
            size_str = DedupScanner._format_size(fs["total_size"]) if fs["total_size"] else "0 B"
            f_tbl.add_row(
                f"  {src_name}",
                f"  {tgt}",
                str(fs["file_count"]),
                size_str,
            )
        console.print(f_tbl)

    # -- Dedup --
    # Dedup over ALL files, including those inside cohesive folder groups.
    # Audacity _data chunks are unique (sequential segments), so SHA-256 won't
    # false-match them.
    dedup_files = list(files)
    dedup_pairs = []
    if dedup_files:
        console.print(f"\n[bold blue]🔁 Dedup[/] [dim]{len(dedup_files)} files[/]")
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console, transient=True,
        ) as prog:
            prog.add_task("Hashing files for duplicates...", total=None)
            dedup_pairs = step_dedup(dedup_files, cfg)
        if dedup_pairs:
            console.print(f"   [yellow]Found {len(dedup_pairs)} duplicate pair(s)[/]")
        else:
            console.print("   [dim]No duplicates found[/]")

    # ── Folder-move confirmation gate ──
    # After analysis + summary, give the user a chance to approve/reject each
    # folder move before we touch anything.  File moves use the existing
    # confidence gate (no individual prompts).
    if approved_folder_moves and not args.dry_run and not args.execute:
        console.print("\n[bold]📁 Folder moves require confirmation:[/]")
        for idx, fm in enumerate(approved_folder_moves, 1):
            src = fm.get("source", "")
            tgt = fm.get("target", "")
            fcount = fm.get("file_count", 0)
            tsize = fm.get("total_size", 0)
            from sorter.deduper import DedupScanner
            size_str = DedupScanner._format_size(tsize) if tsize else "0 B"
            # Display target relative to nas_root
            tgt_rel = str(Path(tgt).relative_to(nas_root)) if tgt else "?"
            is_review = fm.get("confidence", 0) == 0
            prefix = "⚠️ [REVIEW] " if is_review else ""
            console.print(
                f"  [{idx}] {prefix}[cyan]{Path(src).relative_to(nas_root)}[/] → [green]{tgt_rel}[/] "
                f"({fcount} files, {size_str}) [dim]conf={fm.get('confidence',0)}%[/]"
            )
        console.print(
            "  Approve folders (e.g. '1 3' or 'all' or 'none'): ",
            end="",
            style="bold",
        )
        resp = input().strip()
        if resp.lower() == "none":
            approved_folder_moves = []
        elif resp.lower() != "all":
            # Parse selected indices
            try:
                indices = set(int(x) for x in resp.split())
            except ValueError:
                indices = set()
            approved_folder_moves = [
                fm for i, fm in enumerate(approved_folder_moves, 1) if i in indices
            ]
        # else: 'all' keeps them as-is
    elif approved_folder_moves and args.execute:
        # --execute: auto-approve ONLY confident folder moves. Review-moves
        # (failed/uncertain folders, confidence 0) must NOT be moved silently
        # — they exist for the user to review as a unit.
        approved_folder_moves = [
            fm for fm in approved_folder_moves if fm.get("confidence", 0) > 0
        ]

    # ── Proceed prompt or auto-execute ──
    from sorter.reviewer import CONF_REQUIRE_REVIEW
    # File moves: individual files with confidence >= require_review
    file_move_count = sum(1 for c in all_classifications
                          if not c.get("is_folder")
                          and c.get("action") != "delete"
                          and c.get("confidence", 0) >= CONF_REQUIRE_REVIEW)
    # Folder moves: each contributes file_count files
    folder_move_count = sum(fm.get("file_count", 0) for fm in approved_folder_moves)
    # Review-moves (failed/uncertain folders going to _Unsorted_Review)
    review_folder_count = sum(
        fm.get("file_count", 0) for fm in approved_folder_moves
        if fm.get("confidence", 0) == 0
    )
    total_moves = file_move_count + folder_move_count
    total_deletions = sum(1 for c in all_classifications
                          if c.get("action") == "delete" or
                          (not c.get("is_folder") and c.get("confidence", 0) < CONF_REQUIRE_REVIEW))

    if not total_moves and not total_deletions:
        console.print("[yellow]Nothing to do. Exiting.[/]")
        return 0

    if not args.dry_run and not args.execute:
        try:
            review_note = f" (+{review_folder_count} to _Unsorted_Review review)" if review_folder_count else ""
            resp = input(f"\n[bold]READY TO MOVE {total_moves} FILES?{review_note}"
                         f"{' (+delete ' + str(total_deletions) + ')' if total_deletions else ''} [Y/n]: ")
            if resp.lower() in ("n", "no"):
                console.print("[yellow]Aborted by user.[/]")
                return 0
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Aborted.[/]")
            return 0

    # ── Build approved move/deletion lists ──
    from sorter.reviewer import CONF_REQUIRE_REVIEW
    approved_moves = list(approved_folder_moves)
    approved_deletions = []
    for c in all_classifications:
        if c.get("is_folder"):
            continue
        if c.get("action") == "delete":
            approved_deletions.append({
                "path": str(nas_root / c["path"]),
                "type": "adobe_temp",
                "reason": c.get("reason", ""),
            })
        elif c.get("confidence", 0) >= CONF_REQUIRE_REVIEW:
            suggested = c.get("suggested_name")
            source_path = Path(c["path"])
            target_dir = Path(c["category_path"])
            target_name = suggested or source_path.name
            approved_moves.append({
                "source": str(nas_root / source_path),
                "target": str(nas_root / target_dir / target_name),
                "confidence": c.get("confidence", 0),
                "reason": c.get("reason", ""),
                "suggested_name": suggested,
            })

    if not approved_moves and not approved_deletions:
        console.print("[yellow]No changes approved. Exiting.[/]")
        return 0

    # ── Execute with per-file progress ──
    if args.dry_run:
        from sorter.executor import dry_run_summary
        summary = dry_run_summary(approved_moves, approved_deletions)
        console.print(f"\n[bold yellow]⚠️  DRY RUN — Summary[/]")
        console.print(f"  Files to move:   [green]{summary['to_move']}[/]")
        console.print(f"  Files to delete: [red]{summary['to_delete']}[/]")
        if summary['space_freed_estimate']:
            from sorter.deduper import DedupScanner
            freed = DedupScanner._format_size(summary['space_freed_estimate'])
            note = " (deletions free disk; moves reorganize)" if summary['to_delete'] and summary['to_move'] else \
                   " (moves reorganize, don't free disk)" if summary['to_move'] else ""
            console.print(f"  Est. volume: {freed}{note}")
        return 0

    total_ops = len(approved_moves) + len(approved_deletions)
    console.print(f"\n[bold blue]📦 Moving {total_ops} items...[/]")
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("• {task.completed}/{task.total} •"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("Moving...", total=total_ops, completed=0)

        # Wrap the executor to update progress per item
        from sorter.executor import execute_moves
        result = {"moved": 0, "deleted": 0, "failed": 0}
        for move in approved_moves:
            from sorter.executor import _move_file, _move_folder, log_move, ensure_dir
            source = Path(move["source"])
            target = Path(move["target"])

            if move.get("is_folder"):
                ok = _move_folder(source, target, dry_run=False)
                if ok:
                    result["moved"] += 1
                else:
                    result["failed"] += 1
                log_move(csv_path, {"source_path": str(source), "target_path": str(target),
                                    "confidence": move.get("confidence", ""), "source_hash": "",
                                    "action": "folder_move", "reason": move.get("reason", "")})
            else:
                source_hash = ""
                ok = _move_file(source, target, csv_path, source_hash,
                                move.get("confidence"), move.get("reason", ""))
                if ok:
                    result["moved"] += 1
                else:
                    result["failed"] += 1

            prog.update(task, advance=1,
                        desc=f"Moving: {Path(source).name} → {Path(target).parent.name}/")

        for deletion in approved_deletions:
            src = Path(deletion["path"])
            from sorter.executor import move_to_trash, log_move
            ok = move_to_trash(src, nas_root / "Archives" / "_Duplicates_Delete", ensure_parent=True)
            if ok:
                result["deleted"] += 1
            else:
                result["failed"] += 1
            log_move(csv_path, {"source_path": str(src), "target_path": "TRASH",
                                "confidence": "", "source_hash": "",
                                "action": "delete", "reason": deletion.get("reason", "")})
            prog.update(task, advance=1,
                        desc=f"Deleting: {src.name}")

    # ── Results table ──
    from rich.table import Table
    tbl = Table(title="[bold green]✅ Complete[/]", box=None)
    tbl.add_column("Metric", style="bold")
    tbl.add_column("Count")
    tbl.add_row("Files moved", str(result['moved']))
    tbl.add_row("Files deleted", str(result['deleted']))
    tbl.add_row("Failed", str(result.get('failed', 0)))
    tbl.add_row("Undo log", str(csv_path))
    console.print(f"\n")
    console.print(tbl)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. No files were changed.")
        sys.exit(130)
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        print("Run with --dry-run to preview without consequences.", file=sys.stderr)
        sys.exit(1)