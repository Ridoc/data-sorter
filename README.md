# NAS AI File Sorter

Organize your NAS files using a local LLM (Ollama). Scans, classifies, reviews, and moves files with full undo capability.

## Quick Start

```bash
# Prerequisites
ollama pull qwen2.5-coder:7b     # or use any model you prefer

# Run the sorter (dry-run first!)
python3 sort.py --dry-run         # Preview changes without moving anything

# Full run (interactive review → execute)
python3 sort.py

# Undo the last sort
python3 sort.py --undo
```

## How It Works

```
┌─────────┐    ┌──────────┐    ┌───────────┐    ┌────────┐    ┌─────────┐
│ Scanner │ →  │Extractor │ →  │Classifier │ →  │Reviewer│ →  │Executor │
│ (files) │    │(content) │    │  (Ollama) │    │  (TUI) │    │ (moves) │
└─────────┘    └──────────┘    └───────────┘    └────────┘    └─────────┘
                                                      ↑
                                               ┌──────────┐
                                               │  Deduper  │
                                               │(duplicates│
                                               └──────────┘
```

1. **Scanner** — discovers all files on the NAS, respects `.sortignore`
2. **Extractor** — extracts text (PDFs, Office docs) and metadata (EXIF, video/audio info)
3. **Classifier** — sends file info to Ollama in batches → LLM suggests target category + confidence
4. **Deduper** — detects duplicate files (SHA-256 exact + perceptual hash for images)
5. **Reviewer** — Rich TUI shows colored preview tree; you accept/modify/reject per file
6. **Executor** — moves files, logs everything to CSV for undo

## Safety Features

- **Mandatory dry run** — preview every change before anything moves
- **Confidence thresholds** — 🟢 green (≥85%) auto-accepted, 🟡 yellow (50-85%) user reviews, 🔴 red (<50%) queued for manual review
- **CSV undo log** — every move logged with SHA-256 hash; `--undo` reverses it
- **Staging trash** — deleted duplicates go to `Archives/_Duplicates_Delete/` never hard-deleted
- **Adobe Premiere temp files** — flagged for cleanup (Auto-Save, Preview Files are regeneratable)

## Installation

```bash
# Clone / enter the project directory
cd /mnt/NAS-Zeno/OpenCode/Data\ Sorter

# Use the system python3 — all dependencies are already installed.
# Do NOT create a virtualenv: the NAS filesystem rejects symlinks, so
# `python3 -m venv` yields a broken env with no bin/python.

# Verify the install
python3 -m pytest tests/ -q
python3 sort.py --help
```

To reinstall dependencies: `python3 -m pip install -r requirements.txt`

### Dependencies

- Python 3.11+
- Ollama running locally at `http://localhost:11434`
- Model: `qwen2.5-coder:7b` (or any model you configure)
- NAS mounted at `/mnt/NAS-Zeno/` (configurable)
- Optional: `ffprobe` (for video/audio metadata), `markitdown` auto-installed

## CLI Usage

| Command | Description |
|---------|-------------|
| `python sort.py` | Dry run → interactive review → execute |
| `python sort.py --dry-run` | Preview only, no changes |
| `python sort.py --undo` | Revert the last sort run |
| `python sort.py --execute` | Skip interactive review (auto-accept) |
| `python sort.py --path Subfolder` | Scan only a sub-path |
| `python sort.py --config my.yaml` | Use alternate config |

## Configuration

Edit `config.yaml` to customize:

```yaml
ollama:
  model: "qwen2.5-coder:7b"       # LLM model for classification
  batch_size: 8                    # files per request (KV cache reuse, lower latency)
  temperature: 0                   # deterministic classification
  seed: 42                         # reproducible LLM output
  timeout: 60                      # seconds

confidence:
  auto_accept: 85                  # 🟢 green threshold
  require_review: 50               # 🟡 yellow threshold

taxonomy:
  top_level:
    - Zeno
    - Family
    - Company
    - Projects
    - Media
    - Archives
    - _Unsorted_Review
  categories:
    Zeno:
      - Documents, Downloads, Bilder, Videos, Finances, Career, ...

embeddings:
  enabled: false              # opt-in semantic pre-classification (nomic-embed-text)
  model: "nomic-embed-text"
  similarity_threshold: 0.85  # centroid cosine threshold; LLM only runs on unmatched files
```

## Taxonomy (default)

Your NAS will be organized into:

```
/mnt/NAS-Zeno/
├── Zeno/              ← Your personal files
│   ├── Documents/     ← PDFs, Office docs, etc.
│   ├── Downloads/
│   ├── Bilder/
│   ├── Videos/
│   ├── Finances/
│   ├── Career/
│   └── Obsidian_Vault/
├── Family/
│   ├── Mom/
│   ├── Andreas/
│   └── Emily/
├── Company/
│   └── 5 Circles/     ← Your company
├── Projects/
│   ├── ERGO Paphos/
│   ├── FC Squad/
│   └── ... (all existing projects)
├── Media/
│   ├── DJ_Stuff/
│   ├── Music/
│   └── Photos/
├── Archives/
│   ├── Old_Projects/
│   ├── System/
│   └── _Duplicates_Delete/
└── _Unsorted_Review/  ← Low-confidence items
```

LLM can propose new subfolders under any top-level category if no existing one fits.

## Testing

```bash
python3 -m pytest tests/ -v
```

482 tests collected. **12 fail on a clean checkout** until the missing deps are installed:
`magic`, `questionary`, `imagehash`, `sklearn`, `markitdown`. Run
`python3 -m pip install -r requirements.txt` to get a green suite.

## Architecture

| Module | File | Responsibility |
|--------|------|----------------|
| Entry | `sort.py` | CLI, pipeline orchestration |
| Scanner | `sorter/scanner.py` | File discovery, MIME detection, ignore patterns |
| Extractor | `sorter/extractor.py` | Content/metadata extraction per file type |
| Classifier | `sorter/classifier.py` | Ollama REST client, batch inference (deterministic temp=0/seed=42) |
| Routing | `sorter/routing.py` | Deterministic extension→category pre-classifier with path-context guard + delete-protection for 3D models, design sources, media, documents |
| Taxonomy | `sorter/taxonomy.py` | Category tree, Adobe Premiere detection |
| Deduper | `sorter/deduper.py` | SHA-256 + perceptual hash + copy-suffix duplicate scanner |
| Reviewer | `sorter/reviewer.py` | Rich TUI interactive review |
| Executor | `sorter/executor.py` | File moves, CSV logging |
| Undo | `sorter/undo.py` | CSV reader, reverse file moves |
| Embedding | `sorter/embeddings.py` | nomic-embed-text embeddings, centroid matching, KMeans clustering (opt-in) |

## Design Doc

See `.docs/2026-09-17-nas-file-sorter-design.yaml` for full architecture and rationale.