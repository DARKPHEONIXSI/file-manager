# File Organizer — single-file edition

Everything from the original project (`scanner_v3.py`, `executor_v4.py`,
`undo_all.py`, `search.py`) is now combined into one file: **`organizer.py`**.
It runs on Windows, macOS, or Linux — nothing about your personal folders is
hardcoded into the script anymore. You tell it what to scan via a small
config file that it creates for you.

## 1. First-time setup

```
python organizer.py --project-dir "D:\coding\file manager" init-config
```

(`--project-dir` is optional — if you skip it, the config and logs are
stored in whatever folder `organizer.py` itself is sitting in. Pass it once
and reuse the same value for every command below so everything stays
together. **It must come before the subcommand**, e.g.
`organizer.py --project-dir X scan`, not `organizer.py scan --project-dir X`.)

This creates `organizer_config.json` next to your logs. Open it and edit the
`"roots"` list for the machine you're running it on:

```json
"roots": [
  { "path": "C:\\Users\\Simar", "mode": "allowlist",
    "allowlist": ["Desktop", "Documents", "Pictures", "Videos", "Music"] },
  { "path": "D:\\", "mode": "recursive" }
]
```

- **`allowlist` mode** — only scans the listed subfolders. Use this for an
  OS/boot drive (Windows `C:`, or `/` / `/home/you` on Mac/Linux).
- **`recursive` mode** — scans the entire root. Use this for a dedicated
  data drive or external volume (works the same for `D:\`, `/mnt/data`,
  `/Volumes/External`, etc.).

The exclusion rules (shader caches, `.git`, `node_modules`, media library
bundles, AppData, Program Files, vendor folders, and so on) are already
filled in from the original `AGENTS.md` rules and don't need editing unless
you find a new app leaking through — add it to the matching list inside
`"exclusions"`.

## 2. Commands

```
python organizer.py --project-dir "D:\coding\file manager" scan
```
Stage 1+2. Never touches a file. Writes `dry_run_results.txt` (what would
move) and `flagged_for_review.txt` (what was excluded and why). **Always
read both before executing.**

```
python organizer.py --project-dir "D:\coding\file manager" execute --root "D:\"
```
Stage 4. Performs the real moves for everything queued under that one root
in the last `dry_run_results.txt`. It will:
- show you the count and ask you to type `yes` before touching anything
  (skip the prompt with `--yes` if you're scripting it)
- re-check every file against the exclusion rules again immediately before
  moving it, independent of what the dry run said
- log every move to `move_log.csv` *before* it happens
- never overwrite an existing file (auto-renames on collision)

Run it once per root, the same way the original staged pipeline worked.

```
python organizer.py --project-dir "D:\coding\file manager" undo
```
Stage 5. Reverses every successful move in `move_log.csv`, most recent
first, and asks you to type `yes` first.

```
python organizer.py --project-dir "D:\coding\file manager" search keyword
python organizer.py --project-dir "D:\coding\file manager" search --ext .pdf
python organizer.py --project-dir "D:\coding\file manager" search --reindex
```
Stage 6. Rebuild the index with `--reindex` after running `execute`, since
file locations change.

## 3. What's different from the original multi-script version

- **One file, no hardcoded drive letters or usernames.** Everything machine-
  specific lives in `organizer_config.json`, so the same `organizer.py`
  works unmodified on a different computer or OS — just point it at a new
  (or edited) config.
- **Scan and execute now share the exact same exclusion-rule code**, instead
  of two separate copies that could drift apart (the discrepancy that caused
  the Round 1 bug in the original project, where the executor's rules
  lagged behind the scanner's).
- **`execute` now asks for explicit confirmation** showing the file count
  for that root, on top of the dry-run defense-in-depth re-check, since
  combining everything into one file makes it easier to fire off a stage
  by accident.
- Hidden/system-file detection works cross-platform (dotfiles on
  Mac/Linux, hidden/system attributes on Windows) instead of relying on a
  Windows-only API.

## 4. Tested behavior

I built a throwaway test folder with loose files alongside a `.git` repo, a
`node_modules` folder, and a `My Games\...\SaveGame` folder, then ran
`scan` → `execute` → `undo` → `search --reindex` → `search` against it.
Loose files were correctly queued and moved into `Organized\Year\Month\Week`
subfolders; the `.git`, `node_modules`, and save-game folders were correctly
left untouched and flagged; `undo` restored everything to its original
location; `search` found the file by name. This confirms the merged logic
behaves the same as the original separate scripts.
