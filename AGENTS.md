# AGENTS.md — NAS AI File Sorter (project-local)

> Scope: this file carries ONLY what is unique to this repo.
> Global orchestration rules (tiers, plugins, skills, memory tree, agent roster)
> are auto-injected from `~/.config/opencode/AGENTS.md` — never duplicate them here.

PROJECT: "Python 3.11+ CLI that classifies/moves NAS files via local Ollama. This tool moves a user's real family photos — every change is data-destructive by nature."

## COMMANDS

```bash
DS=~/.venvs/data-sorter/bin/python     # persistent venv on LOCAL disk

$DS sort.py --dry-run         # ALWAYS preview first
$DS sort.py --undo            # rollback last run
$DS -m pytest tests/ -q -p no:cacheprovider   # 562 passed, 0 failed
```

WHY_THIS_VENV: >
  The project dir is a CIFS mount (`//192.168.1.200/Zeno`) that does NOT support
  symlinks — `python3 -m venv .venv` fails with `Errno 95` on the `lib64 -> lib`
  link, even with `--copies`. Never create a venv inside this repo.
  The venv lives at `~/.venvs/data-sorter` on local btrfs and persists across
  reboots. (An older `/tmp/data-sorter-venv` vanished because /tmp is tmpfs.)
  System `python3` is NOT usable: it lacks `magic`, `questionary`, `imagehash`,
  `sklearn`, `markitdown` — 12 tests fail there.

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
`sort.py` = CLI entry + orchestration, incl. the per-folder confirmation gate
(`_confirm_folder_moves()` — integration-tested in `tests/test_folder_gate.py`).

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

OPEN_DEBT: 16 open items in `.docs/tech-debt.yaml`; RESOLVED items live in
`.docs/resolved.yaml` (dead `retry_attempts` config, folder-confirm gate coverage,
and Design routing — the last now VERIFIED live via a 3273-file dry-run).
Suite baseline is 0 failures; the old "12 pre-existing failures" were missing deps in
the wrong interpreter, not real failures. Latest session: `session-log-009.yaml`.

SAFETY NOTE (2026-10-03): `--undo` after a dissolve once WOULD have relocated the whole
`Media/Photos` library. Folder-level ops now emit PER-FILE undo rows and `reverse_move`
whitelists only `move`/`delete`. Never reintroduce an aggregate directory-level row —
`reverse_move` cannot know whether such a destination was created by the run or already
held user data.

## SUBAGENTS

| Trigger | Agent | Command |
|---|---|---|
| AFTER every code change | qa-tester | `$DS -m pytest tests/ -q -p no:cacheprovider` (expect 562) |
| diff touches delete/move/symlink/routing | security-reviewer | review data-loss surface |
| END of every session | session-close | sync `.docs/**` + commit |

## GOTCHAS

- `.sortignore` excludes `OpenCode/`, `.git/`, `.Trash-*`, `@eaDir` — never scan those.
- Add `-p no:cacheprovider` to pytest: the CIFS mount rejects pytest's cache writes
  (`Errno 1`), which emits a spurious PytestCacheWarning on every run.
- Design Doc in README is the only human-facing spec; keep it in sync with `sorter/`.
