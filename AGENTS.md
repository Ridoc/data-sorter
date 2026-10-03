# AGENTS.md — NAS AI File Sorter (project-local)

> Scope: this file carries ONLY what is unique to this repo.
> Global orchestration rules (tiers, plugins, skills, memory tree, agent roster)
> are auto-injected from `~/.config/opencode/AGENTS.md` — never duplicate them here.

PROJECT: "Python 3.11+ CLI that classifies/moves NAS files via local Ollama. This tool moves a user's real family photos — every change is data-destructive by nature."

## COMMANDS

```bash
python3 sort.py --dry-run          # ALWAYS preview first
python3 sort.py --undo             # rollback last run
python3 -m pytest tests/ -q        # 482 collected; 12 fail until deps are installed (see below)
```

NO_VENV: >
  Use the system `python3` directly. Do NOT create a virtualenv for this repo —
  the NAS filesystem rejects symlinks, so `python3 -m venv .venv` produces a broken
  env with no `bin/python`.

DEPS: >
  As of 2026-10-03 the system interpreter is INCOMPLETE — `magic`, `questionary`,
  `imagehash`, `sklearn`, `markitdown` are missing, causing 12 pre-existing test
  failures (test_reviewer, test_scanner, embeddings). Fix once with:
  `python3 -m pip install -r requirements.txt` (no sudo). Re-run the suite and
  update this line + the README test count to match reality.

BASELINE: >
  The 12 failures are NOT a regression — they reproduce on a clean checkout.
  Do not "fix" them by editing tests; install the deps.

## SAFETY_INVARIANTS

Non-negotiable. A diff that violates any of these is a bug regardless of test status.

1. NEVER hard-delete. The codebase contains zero `unlink`/`rmtree` — every file
   operation is `shutil.move`. Duplicates go to `Archives/_Duplicates_Delete/`.
   Keep it that way.
2. NEVER move a file without a CSV undo row. `--undo` is the ONLY rollback path.
   No log entry = unrecoverable user data loss.
3. NEVER translate folder or file names. `sorter/language.py` enforces this
   (German/Greek/English stay verbatim). Only the top 1-2 taxonomy levels are
   English. Do not "helpfully" normalize a name.
4. MANDATORY dry-run before any execute path. Never add a code path that moves
   files without an explicit preview stage.
5. `_DELETE_PROTECTED_EXT` in `sorter/routing.py` guards 3D models, design
   sources, media, documents from duplicate-deletion. Extending it is a
   user-data decision, not a refactor.

## MODULE_MAP

Pipeline: `scanner → extractor → classifier → folder_classifier → routing → reviewer → executor → undo`
`sort.py` = CLI entry + orchestration, incl. the per-folder confirmation gate (~line 574).

| Module | Role |
|---|---|
| `sorter/scanner.py` | file discovery, MIME detection, ignore patterns |
| `sorter/extractor.py` | text/metadata extraction (PDF, Office, EXIF, media) |
| `sorter/classifier.py` | Ollama REST client, batch inference (temp=0, seed=42) |
| `sorter/folder_classifier.py` | folder-level classification, subject-first verdicts |
| `sorter/routing.py` | deterministic ext→category rules, applied BEFORE the LLM |
| `sorter/language.py` | language-detection guard (no external libs) |
| `sorter/context.py` | deterministic context detection (Audacity, Premiere) |
| `sorter/taxonomy.py` | category tree, Adobe Premiere detection |
| `sorter/deduper.py` | SHA-256 + perceptual hash |
| `sorter/reviewer.py` | Rich TUI interactive review |
| `sorter/executor.py` | file moves, CSV logging, staging trash |
| `sorter/undo.py` | CSV reader, hash-verified reverse moves |
| `sorter/embeddings.py` | nomic-embed-text + centroid/KMeans (opt-in, off by default) |

## MEMORY

`.docs/index.yaml` is the router. `lessons.yaml` is append-only. Full architecture:
`.docs/2026-09-17-nas-file-sorter-design.yaml`.

OPEN_DEBT: 3 items in `.docs/tech-debt.yaml` — dead `retry_attempts` config,
untested folder-confirm gate, Design routing never verified against a live LLM.
Do not claim Design routing works until it has been dry-run on real data.

## SUBAGENTS

| Trigger | Agent | Command |
|---|---|---|
| AFTER every code change | qa-tester | `python3 -m pytest tests/ -q` (expect 482) |
| diff touches delete/move/symlink/routing | security-reviewer | review data-loss surface |
| END of every session | session-close | sync `.docs/**` + commit |

## GOTCHAS

- `.sortignore` excludes `OpenCode/`, `.git/`, `.Trash-*`, `@eaDir` — never scan those.
- Design Doc in README is the only human-facing spec; keep it in sync with `sorter/`.
