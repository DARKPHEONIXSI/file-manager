#!/usr/bin/env python3
"""
organizer.py — Unified File Organizer (single-file, cross-platform)

Combines everything from the original multi-script project into one file:
  - scanner_v3.py   -> `scan` subcommand    (dry run, never touches files)
  - executor_v4.py  -> `execute` subcommand (performs the real moves)
  - undo_all.py     -> `undo` subcommand    (reverses a move_log.csv)
  - search.py       -> `search` subcommand  (indexes + searches files)
  - (new)           -> `dupes` subcommand   (find + remove duplicate files)

Works on Windows, macOS, and Linux. Nothing about your personal folders is
hardcoded — you tell it what to scan via a config file (organizer_config.json),
which this script will create for you the first time you run it.

USAGE
-----
  python organizer.py init-config        # create/inspect organizer_config.json
  python organizer.py scan               # Stage 1+2: build dry_run_results.txt
  python organizer.py execute --root "D:\\"   # Stage 4: perform real moves for one root
  python organizer.py undo               # Stage 5: reverse everything in move_log.csv
  python organizer.py search keyword     # Stage 6: search the file index
  python organizer.py search --reindex   # rebuild the search index
  python organizer.py dupes scan         # Stage 7: find duplicate files (read-only)
  python organizer.py dupes review       # review duplicates one group at a time
  python organizer.py dupes review --gui # review all duplicates in one checklist window
  python organizer.py dupes purge        # permanently delete quarantined duplicates

All commands accept --project-dir to change where the config and logs live
(default: the same folder this script is in).

SAFETY (unchanged from the original project's rules)
-----------------------------------------------------
  - `scan` NEVER moves or deletes anything. It only writes dry_run_results.txt
    and flagged_for_review.txt.
  - `execute` re-checks every file against the exclusion rules immediately
    before moving it (defense in depth), even though it already passed the
    scan. This catches a stale or hand-edited dry run file.
  - `execute` requires you to type "yes" after showing you exactly how many
    files are queued for the root you specified.
  - Every move is written to move_log.csv BEFORE it happens, never after.
  - Existing files are never overwritten — collisions get an auto-incremented
    suffix.
  - `undo` requires typing "yes" and replays moves most-recent-first.
  - `dupes scan` never touches a file. `dupes review` only MOVES chosen
    copies into a quarantine folder (reversible by hand) and logs every
    move before it happens. Only `dupes purge` actually deletes anything,
    and it requires a typed "yes" after showing you exactly what's queued.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import platform
import shutil
import sys

CONFIG_FILENAME = "organizer_config.json"
DRY_RUN_FILENAME = "dry_run_results.txt"
FLAGGED_FILENAME = "flagged_for_review.txt"
LOG_FILENAME = "move_log.csv"
INDEX_FILENAME = "search_index.json"
DUPLICATES_FILENAME = "duplicates.json"
DUPLICATE_LOG_FILENAME = "duplicate_log.csv"
QUARANTINE_DIRNAME = "_DuplicatesPendingDelete"
MAX_PATH_LEN = 260  # historical Windows limit; harmless as a soft check elsewhere
HASH_CHUNK_SIZE = 1024 * 1024

DEFAULT_CONFIG = {
    "_readme": (
        "Edit the 'roots' list below for THIS machine before running scan/execute. "
        "mode 'allowlist' = only scan the listed subfolders (use this for an OS/boot "
        "drive, e.g. C: on Windows or / on Mac/Linux). mode 'recursive' = scan the "
        "entire root (use this for a dedicated data drive/volume). "
        "Paths can be Windows ('D:\\\\') or POSIX ('/mnt/data', '/Volumes/External')."
    ),
    "roots": [
        {
            "path": "C:\\Users\\YOUR_USERNAME",
            "mode": "allowlist",
            "allowlist": ["Desktop", "Documents", "Pictures", "Videos", "Music"]
        },
        {
            "path": "D:\\",
            "mode": "recursive"
        }
    ],
    "exclusions": {
        "structural_keywords": [
            "cache", "shadercache", "shaderbytecode", "webcache", "savegame",
            "savegames", "saved games", ".git", ".venv", "node_modules",
            "__pycache__", "dist", "build", "target", ".next", "models", ".ollama"
        ],
        "bundle_extensions": [".musiclibrary", ".photoslibrary", ".tvlibrary"],
        "ownership_markers": [
            "manifest.json", "config.json", ".lock", "package.json", "requirements.txt"
        ],
        "app_data_parents": [
            "\\my games", "\\saved games", "\\appdata", "\\steam\\steamapps",
            "\\epic games", "/library/application support", "/.config", "/.steam"
        ],
        "config_extensions": [".cfg", ".ini", ".bin", ".bindings", ".peace"],
        "sys_vendors": ["hp", "intel", "dell", "nvidia", "amd"],
        "sys_folders": [
            "windows", "$recycle.bin", "system volume information", "recovery",
            "boot", "perflogs", "program files", "program files (x86)", "programdata"
        ],
        "fallback_list": [
            "\\adobe\\premiere pro", "\\adobe", "huggingface", "lm studio\\models",
            "comfyui\\models", "stable-diffusion-webui\\models",
            "dropbox\\.dropbox.cache",
            "onedrive\\.849c9593-d756-4e56-8d6e-42412f2a707b", "google drive"
        ]
    }
}


# --------------------------------------------------------------------------
# Config handling
# --------------------------------------------------------------------------

def get_project_dir(args):
    if args.project_dir:
        return os.path.abspath(args.project_dir)
    return os.path.dirname(os.path.abspath(__file__))


def config_path(project_dir):
    return os.path.join(project_dir, CONFIG_FILENAME)


def load_config(project_dir, create_if_missing=True):
    path = config_path(project_dir)
    if not os.path.exists(path):
        if not create_if_missing:
            return None
        os.makedirs(project_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"No config found. Created a starter config at:\n  {path}")
        print("Edit the 'roots' section for this machine, then run the command again.")
        sys.exit(0)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Shared helpers (used by scan AND execute, so the rules can never drift
# apart between the two stages the way executor_v4.py vs scanner_v3.py did)
# --------------------------------------------------------------------------

def is_hidden_or_system(filepath):
    name = os.path.basename(filepath.rstrip(os.sep)) or filepath
    if name.startswith("."):
        return True
    try:
        attrs = os.stat(filepath).st_file_attributes  # Windows only
        return bool(attrs & 2) or bool(attrs & 4)
    except AttributeError:
        return False  # non-Windows: st_file_attributes doesn't exist
    except Exception:
        return False


def get_week_range(date_obj):
    day = date_obj.day
    if day <= 7:
        return "Week 1 (1st-7th)"
    elif day <= 14:
        return "Week 2 (8th-14th)"
    elif day <= 21:
        return "Week 3 (15th-21st)"
    elif day <= 28:
        return "Week 4 (22nd-28th)"
    else:
        return "Week 5 (29th+)"


def configured_root_paths(config):
    """Every path that should be treated as a 'root' (no hidden-attr exclusion,
    no config-majority exclusion): each root itself, plus each allowlist entry."""
    roots = set()
    for r in config["roots"]:
        roots.add(os.path.normcase(os.path.normpath(r["path"])))
        if r.get("mode") == "allowlist":
            for sub in r.get("allowlist", []):
                roots.add(os.path.normcase(os.path.normpath(os.path.join(r["path"], sub))))
    return roots


def is_configured_root(folder_path, config):
    return os.path.normcase(os.path.normpath(folder_path)) in configured_root_paths(config)


def check_structural_rules(folder_path, visible_files, is_root_dir, excl):
    lower_path = folder_path.lower()
    parts = lower_path.replace("/", os.sep).replace("\\", os.sep).split(os.sep)

    for part in parts:
        if part in excl["sys_folders"]:
            return "Windows System / Program Files folder"
        if part in excl["sys_vendors"]:
            return "Hardware Vendor / Driver folder"

    if any(f.lower().endswith((".exe", ".dll")) for f in visible_files):
        return "Software Install Location (contains .exe/.dll)"

    for part in parts:
        for keyword in excl["structural_keywords"]:
            if keyword in part:
                return f"Structural Keyword Match ({keyword})"

    for part in parts:
        if any(part.endswith(ext) for ext in excl["bundle_extensions"]):
            return f"Media Bundle Match ({part})"

    if any(f.lower() in excl["ownership_markers"] for f in visible_files):
        return "Ownership Marker Present"

    if any(parent in lower_path for parent in excl["app_data_parents"]):
        return "App-Data Parent Match"

    if not is_root_dir and visible_files:
        config_count = sum(
            1 for f in visible_files
            if os.path.splitext(f)[1].lower() in excl["config_extensions"]
            or f.lower().endswith(".dotx-profile-style")
        )
        if config_count / len(visible_files) > 0.5:
            return "Config/Profile file majority"

    for fallback in excl["fallback_list"]:
        if fallback in lower_path:
            return f"Fallback List Match ({fallback})"

    return None


def check_exclusion_for_file(filepath, config):
    """Defense-in-depth re-check used by `execute` right before a move."""
    folder_path = os.path.dirname(filepath)
    is_root_dir = is_configured_root(folder_path, config)
    if not is_root_dir and is_hidden_or_system(folder_path):
        return "Folder is hidden/system"
    try:
        files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
        visible_files = [f for f in files if not is_hidden_or_system(os.path.join(folder_path, f))]
    except Exception:
        visible_files = []
    return check_structural_rules(folder_path, visible_files, is_root_dir, config["exclusions"])


# --------------------------------------------------------------------------
# Stage 1+2: scan
# --------------------------------------------------------------------------

def walk_and_process(start_path, project_dir, config, dry_run_results, flagged_folders):
    if not os.path.exists(start_path):
        print(f"  (skipping, not found: {start_path})")
        return
    norm_project_dir = os.path.normcase(os.path.normpath(project_dir))
    excl = config["exclusions"]

    for root, dirs, files in os.walk(start_path):
        is_root_dir = os.path.normcase(os.path.normpath(root)) == os.path.normcase(os.path.normpath(start_path))

        if not is_root_dir and is_hidden_or_system(root):
            flagged_folders.append(f"{root} - REASON: Hidden/system folder")
            dirs.clear()
            continue

        if os.path.normcase(os.path.normpath(root)).startswith(norm_project_dir):
            dirs.clear()
            continue

        if "Organized" in root.split(os.sep):
            # never re-organize our own output folders
            dirs.clear()
            continue

        visible_files = [f for f in files if not is_hidden_or_system(os.path.join(root, f))]

        reason = check_structural_rules(root, visible_files, is_root_dir, excl)
        if reason:
            flagged_folders.append(f"{root} - REASON: {reason}")
            dirs.clear()
            continue

        for f in visible_files:
            filepath = os.path.join(root, f)
            try:
                ctime = os.path.getctime(filepath)
                dt = datetime.datetime.fromtimestamp(ctime)
                year = dt.strftime("%Y")
                month = dt.strftime("%m-%B")
                week = get_week_range(dt)

                dest_folder = os.path.join(root, "Organized", year, month, week)
                dest_path = os.path.join(dest_folder, f)

                if len(filepath) > MAX_PATH_LEN or len(dest_path) > MAX_PATH_LEN:
                    flagged_folders.append(f"{root} - REASON: Path Length > {MAX_PATH_LEN} for file {f}")
                    continue

                dry_run_results.append(f"{filepath} -> {dest_path}")
            except Exception:
                pass


def get_scan_start_paths(config):
    """Expand config['roots'] into concrete folders to walk: allowlist roots
    become one start path per allowed subfolder, recursive roots become a
    single start path. Used by both `scan` and `dupes scan` so the two never
    drift apart on which folders are in scope."""
    start_paths = []
    for r in config["roots"]:
        root_path = r["path"]
        mode = r.get("mode", "recursive")
        if mode == "allowlist":
            for sub in r.get("allowlist", []):
                start_paths.append(os.path.join(root_path, sub))
        else:
            start_paths.append(root_path)
    return start_paths


def cmd_scan(args):
    project_dir = get_project_dir(args)
    config = load_config(project_dir)

    dry_run_results = []
    flagged_folders = []

    for start_path in get_scan_start_paths(config):
        print(f"Scanning {start_path}...")
        walk_and_process(start_path, project_dir, config, dry_run_results, flagged_folders)

    with open(os.path.join(project_dir, DRY_RUN_FILENAME), "w", encoding="utf-8") as f:
        for res in dry_run_results:
            f.write(res + "\n")

    with open(os.path.join(project_dir, FLAGGED_FILENAME), "w", encoding="utf-8") as f:
        for folder in sorted(flagged_folders):
            f.write(folder + "\n")

    print(f"\nDry run complete. {len(dry_run_results)} files queued, {len(flagged_folders)} folders flagged.")
    print(f"Review before executing:\n  {os.path.join(project_dir, DRY_RUN_FILENAME)}\n  {os.path.join(project_dir, FLAGGED_FILENAME)}")


# --------------------------------------------------------------------------
# Stage 4: execute
# --------------------------------------------------------------------------

def write_log(log_file, original_path, new_path):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f'"{original_path}","{new_path}","{timestamp}"\n')


def generate_safe_dest_path(dest_folder, filename):
    base_name, ext = os.path.splitext(filename)
    dest_path = os.path.join(dest_folder, filename)
    counter = 1
    while os.path.exists(dest_path):
        new_filename = f"{base_name}_{counter}{ext}"
        dest_path = os.path.join(dest_folder, new_filename)
        counter += 1
    return dest_path


def cmd_execute(args):
    if not args.root:
        print("Please provide --root, matching one of the 'path' values in organizer_config.json")
        sys.exit(1)

    project_dir = get_project_dir(args)
    config = load_config(project_dir)
    dry_run_file = os.path.join(project_dir, DRY_RUN_FILENAME)
    log_file = os.path.join(project_dir, LOG_FILENAME)

    if not os.path.exists(dry_run_file):
        print(f"No dry run found at {dry_run_file}. Run `organizer.py scan` first.")
        sys.exit(1)

    target_root = os.path.normcase(os.path.normpath(args.root))

    if not os.path.exists(log_file):
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("Original Path,New Path,Timestamp\n")

    # Pre-count what's queued for this root so the user knows what they're approving
    queued = []
    with open(dry_run_file, "r", encoding="utf-8") as f:
        for line in f:
            if " -> " in line:
                orig, dest = line.strip().split(" -> ")
                if os.path.normcase(os.path.normpath(orig)).startswith(target_root):
                    queued.append((orig, dest))

    if not queued:
        print(f"No queued files in {DRY_RUN_FILENAME} match root: {args.root}")
        sys.exit(0)

    print(f"{len(queued)} files are queued to move under: {args.root}")
    if not args.yes:
        confirm = input("Type 'yes' to execute these moves for real: ")
        if confirm.strip().lower() != "yes":
            print("Execution cancelled. No files were touched.")
            sys.exit(0)

    print(f"Starting execution for root {args.root}...")
    moved = skipped = errored = 0

    for orig, dest in queued:
        if not os.path.exists(orig):
            write_log(log_file, orig, "SKIPPED: Source file not found")
            skipped += 1
            continue

        try:
            with open(orig, "a"):
                pass
        except IOError:
            write_log(log_file, orig, "SKIPPED: File locked or permission denied")
            skipped += 1
            continue

        # Defense in depth: re-verify exclusion rules immediately before moving
        exclusion_reason = check_exclusion_for_file(orig, config)
        if exclusion_reason:
            write_log(log_file, orig, f"SKIPPED: Defense in Depth - {exclusion_reason}")
            skipped += 1
            continue

        dest_folder = os.path.dirname(dest)
        filename = os.path.basename(orig)

        try:
            os.makedirs(dest_folder, exist_ok=True)
            final_dest = generate_safe_dest_path(dest_folder, filename)
            write_log(log_file, orig, final_dest)  # log BEFORE move
            shutil.move(orig, final_dest)
            moved += 1
        except Exception as e:
            write_log(log_file, orig, f"ERROR: {str(e)}")
            errored += 1

    print(f"\n--- {args.root} SUMMARY ---")
    print(f"Successfully Moved: {moved}")
    print(f"Skipped (Safety rules/Locked): {skipped}")
    print(f"Errored: {errored}")


# --------------------------------------------------------------------------
# Stage 5: undo
# --------------------------------------------------------------------------

def cmd_undo(args):
    project_dir = get_project_dir(args)
    log_file = os.path.join(project_dir, LOG_FILENAME)

    if not os.path.exists(log_file):
        print(f"No {LOG_FILENAME} found in {project_dir}.")
        return

    if not args.yes:
        confirm = input(f"Type 'yes' to confirm undoing all successful moves in {LOG_FILENAME}: ")
        if confirm.strip().lower() != "yes":
            print("Undo cancelled.")
            return

    moves = []
    with open(log_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if len(row) >= 3:
                orig, new_path, ts = row[0], row[1], row[2]
                if not new_path.startswith("SKIPPED") and not new_path.startswith("ERROR") and new_path != "UNDO EXECUTED":
                    moves.append((orig, new_path))

    if not moves:
        print("No valid moves to undo.")
        return

    moves.reverse()  # most recent first, so nested moves unwind correctly
    undone = errors = 0

    for orig, new_path in moves:
        if os.path.exists(new_path):
            try:
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                shutil.move(new_path, orig)
                undone += 1

                dir_to_clean = os.path.dirname(new_path)
                while "Organized" in dir_to_clean:
                    try:
                        os.rmdir(dir_to_clean)
                        dir_to_clean = os.path.dirname(dir_to_clean)
                    except OSError:
                        break
            except Exception as e:
                print(f"Error restoring {new_path}: {e}")
                errors += 1
        else:
            print(f"File not found to undo: {new_path}")
            errors += 1

    print(f"Undo complete. {undone} files restored, {errors} errors.")

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["---", "UNDO EXECUTED", timestamp])


# --------------------------------------------------------------------------
# Stage 6: search
# --------------------------------------------------------------------------

def build_index(project_dir, config):
    index_file = os.path.join(project_dir, INDEX_FILENAME)
    print("Building search index... this may take a few minutes.")
    index = []

    def scan_path(start_path):
        if not os.path.exists(start_path):
            print(f"  (skipping, not found: {start_path})")
            return
        for root, _, files in os.walk(start_path):
            for f in files:
                index.append({
                    "name": f.lower(),
                    "ext": os.path.splitext(f)[1].lower(),
                    "path": os.path.join(root, f)
                })

    for start_path in get_scan_start_paths(config):
        print(f"Scanning {start_path}...")
        scan_path(start_path)

    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f)
    print(f"Index built with {len(index)} files.")


def search(project_dir, term=None, ext=None):
    index_file = os.path.join(project_dir, INDEX_FILENAME)
    if not os.path.exists(index_file):
        print("Index not found. Run: python organizer.py search --reindex")
        return

    with open(index_file, "r", encoding="utf-8") as f:
        index = json.load(f)

    results = []
    for item in index:
        match = True
        if term and term.lower() not in item["name"]:
            match = False
        if ext and ext.lower() != item["ext"]:
            match = False
        if match:
            results.append(item["path"])

    print(f"\nFound {len(results)} matching files:\n")
    for res in results[:100]:
        print(res)
    if len(results) > 100:
        print(f"... and {len(results) - 100} more.")


def cmd_search(args):
    project_dir = get_project_dir(args)
    config = load_config(project_dir)

    if args.reindex:
        build_index(project_dir, config)
    elif args.term or args.ext:
        search(project_dir, args.term, args.ext)
    else:
        print("Provide a search term, --ext, or --reindex. See --help.")


# --------------------------------------------------------------------------
# Stage 7: duplicate detection & removal
#
# Two-stage, reversible-by-default deletion, consistent with the rest of
# this project's safety rules:
#   `dupes scan`   -> finds duplicate groups, never touches a file, writes
#                     duplicates.json
#   `dupes review` -> you choose what to remove, one group at a time (CLI)
#                     or all at once in a checklist window (--gui). Either
#                     way, chosen files are MOVED to a quarantine folder,
#                     never deleted outright, and every move is logged
#                     BEFORE it happens, same as `execute`.
#   `dupes purge`  -> the only irreversible step. Permanently deletes
#                     everything sitting in the quarantine folder, after a
#                     typed "yes" confirmation showing exactly what's about
#                     to go.
# --------------------------------------------------------------------------

def file_hash(filepath):
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def walk_for_dupes(start_path, project_dir, config, size_map):
    if not os.path.exists(start_path):
        print(f"  (skipping, not found: {start_path})")
        return
    norm_project_dir = os.path.normcase(os.path.normpath(project_dir))
    excl = config["exclusions"]

    for root, dirs, files in os.walk(start_path):
        is_root_dir = os.path.normcase(os.path.normpath(root)) == os.path.normcase(os.path.normpath(start_path))

        if not is_root_dir and is_hidden_or_system(root):
            dirs.clear()
            continue

        norm_root = os.path.normcase(os.path.normpath(root))
        if norm_root.startswith(norm_project_dir):
            dirs.clear()  # never hash our own logs/config/quarantine folder
            continue

        visible_files = [f for f in files if not is_hidden_or_system(os.path.join(root, f))]

        # Reuse the same structural exclusion rules as `scan` so caches,
        # .git, node_modules, game saves, etc. never get hashed (slow and
        # pointless) or offered up for deletion.
        reason = check_structural_rules(root, visible_files, is_root_dir, excl)
        if reason:
            dirs.clear()
            continue

        for f in visible_files:
            filepath = os.path.join(root, f)
            try:
                size = os.path.getsize(filepath)
                if size == 0:
                    continue  # empty files aren't worth flagging as "duplicates"
                size_map.setdefault(size, []).append(filepath)
            except Exception:
                pass


def cmd_dupes_scan(args):
    project_dir = get_project_dir(args)
    config = load_config(project_dir)

    size_map = {}
    for start_path in get_scan_start_paths(config):
        print(f"Scanning {start_path} for duplicates...")
        walk_for_dupes(start_path, project_dir, config, size_map)

    # Cheap first pass: only files sharing an exact size can possibly match.
    candidates = {size: paths for size, paths in size_map.items() if len(paths) > 1}
    total_candidates = sum(len(p) for p in candidates.values())
    print(f"\n{total_candidates} files share a size with at least one other file. Hashing those now...")

    hash_map = {}
    hashed = 0
    for size, paths in candidates.items():
        for p in paths:
            h = file_hash(p)
            hashed += 1
            if hashed % 500 == 0:
                print(f"  hashed {hashed}/{total_candidates}...")
            if h:
                hash_map.setdefault((size, h), []).append(p)

    groups = []
    for (size, h), paths in hash_map.items():
        if len(paths) < 2:
            continue
        files_info = []
        for p in paths:
            try:
                ctime = os.path.getctime(p)
            except Exception:
                ctime = 0
            files_info.append({"path": p, "ctime": ctime})
        files_info.sort(key=lambda x: x["ctime"])  # oldest first = recommended keeper
        groups.append({"hash": h, "size": size, "files": files_info})

    groups.sort(key=lambda g: g["size"] * (len(g["files"]) - 1), reverse=True)  # biggest reclaimable space first

    with open(os.path.join(project_dir, DUPLICATES_FILENAME), "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=2)

    total_files = sum(len(g["files"]) for g in groups)
    reclaimable = sum(g["size"] * (len(g["files"]) - 1) for g in groups)
    print(f"\nDuplicate scan complete. {len(groups)} duplicate groups, {total_files} files involved.")
    print(f"Reclaimable space if one copy is kept per group: {reclaimable / (1024*1024):.1f} MB")
    print(f"Review with: organizer.py dupes review   (add --gui for the checklist window)")


def duplicate_write_log(project_dir, original_path, action, detail=""):
    log_file = os.path.join(project_dir, DUPLICATE_LOG_FILENAME)
    is_new = not os.path.exists(log_file)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        if is_new:
            f.write("Original Path,Action,Detail,Timestamp\n")
        f.write(f'"{original_path}","{action}","{detail}","{timestamp}"\n')


def quarantine_file(filepath, project_dir):
    """Move (never delete) a file into the quarantine folder. Reversible:
    the user can just move it back manually any time before `purge`."""
    quarantine_dir = os.path.join(project_dir, QUARANTINE_DIRNAME)
    os.makedirs(quarantine_dir, exist_ok=True)
    filename = os.path.basename(filepath)
    dest = generate_safe_dest_path(quarantine_dir, filename)
    try:
        duplicate_write_log(project_dir, filepath, "QUARANTINED", dest)
        shutil.move(filepath, dest)
        return True, dest
    except Exception as e:
        duplicate_write_log(project_dir, filepath, "ERROR", str(e))
        return False, str(e)


def load_duplicates(project_dir):
    path = os.path.join(project_dir, DUPLICATES_FILENAME)
    if not os.path.exists(path):
        print(f"No {DUPLICATES_FILENAME} found. Run `organizer.py dupes scan` first.")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_dupes_review_cli(project_dir, groups):
    total_quarantined = 0
    for gi, group in enumerate(groups):
        files = group["files"]
        print(f"\n--- Group {gi + 1}/{len(groups)} — {len(files)} identical copies, {group['size'] / 1024:.1f} KB each ---")
        for i, finfo in enumerate(files):
            tag = "KEEP (oldest)" if i == 0 else f"  copy {i}"
            ts = datetime.datetime.fromtimestamp(finfo["ctime"]).strftime("%Y-%m-%d") if finfo["ctime"] else "unknown date"
            print(f"  [{i}] {tag:14s} {finfo['path']}  ({ts})")

        choice = input(f"Keep which number? (default 0, 's' to skip this group, 'q' to stop reviewing): ").strip().lower()
        if choice == "q":
            print("Stopped reviewing. Remaining groups left untouched.")
            break
        if choice == "s":
            continue
        keep_index = 0
        if choice.isdigit() and 0 <= int(choice) < len(files):
            keep_index = int(choice)

        to_quarantine = [f["path"] for i, f in enumerate(files) if i != keep_index]
        print(f"  Will quarantine {len(to_quarantine)} file(s), keeping [{keep_index}].")
        confirm = input("  Type 'y' to confirm this group: ").strip().lower()
        if confirm != "y":
            print("  Skipped.")
            continue

        for p in to_quarantine:
            ok, result = quarantine_file(p, project_dir)
            if ok:
                total_quarantined += 1
                print(f"  Quarantined: {p}")
            else:
                print(f"  ERROR quarantining {p}: {result}")

    print(f"\nDone. {total_quarantined} files moved to {QUARANTINE_DIRNAME}\\ (not deleted yet).")
    print(f"Run `organizer.py dupes purge` when you're ready to permanently delete them.")


def launch_gui_review(project_dir, groups):
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("tkinter isn't available in this Python install, so the --gui review")
        print("window can't open. Use `organizer.py dupes review` (no --gui) instead,")
        print("which reviews the same duplicates.json one group at a time in the terminal.")
        return

    root = tk.Tk()
    root.title("Duplicate File Review")
    root.geometry("960x640")

    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scroll_frame = ttk.Frame(canvas)

    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    check_rows = []  # (BooleanVar, filepath, size)

    for gi, group in enumerate(groups):
        files = group["files"]
        header = ttk.Label(
            scroll_frame,
            text=f"Group {gi + 1} — {len(files)} copies, {group['size'] / 1024:.1f} KB each",
            font=("Segoe UI", 10, "bold"),
        )
        header.pack(anchor="w", pady=(12, 2), padx=10)
        for i, finfo in enumerate(files):
            var = tk.BooleanVar(value=(i != 0))  # recommend keeping the oldest, check the rest
            ts = datetime.datetime.fromtimestamp(finfo["ctime"]).strftime("%Y-%m-%d") if finfo["ctime"] else "unknown date"
            label = ("KEEP (oldest) — " if i == 0 else "delete — ") + f"{finfo['path']}  ({ts})"
            cb = ttk.Checkbutton(scroll_frame, text=label, variable=var)
            cb.pack(anchor="w", padx=30)
            check_rows.append((var, finfo["path"], group["size"]))

    bottom = ttk.Frame(root)
    bottom.pack(fill="x", side="bottom", pady=8)

    summary_var = tk.StringVar()

    def update_summary(*_):
        selected = [(p, s) for v, p, s in check_rows if v.get()]
        total_mb = sum(s for _, s in selected) / (1024 * 1024)
        summary_var.set(f"{len(selected)} files selected — {total_mb:.1f} MB would be quarantined")

    for v, _, _ in check_rows:
        v.trace_add("write", update_summary)
    update_summary()

    ttk.Label(bottom, textvariable=summary_var).pack(side="left", padx=10)

    def select_all():
        for v, _, _ in check_rows:
            v.set(True)

    def select_none():
        for v, _, _ in check_rows:
            v.set(False)

    def do_quarantine():
        selected_paths = [p for v, p, _ in check_rows if v.get()]
        if not selected_paths:
            messagebox.showinfo("Nothing selected", "No files were checked.")
            return
        if not messagebox.askyesno(
            "Confirm",
            f"Move {len(selected_paths)} files to the quarantine folder?\n\n"
            "Nothing is permanently deleted yet — you can still move files back "
            "out of the quarantine folder by hand, or run `dupes purge` later "
            "when you're sure.",
        ):
            return
        quarantined = 0
        for p in selected_paths:
            ok, _ = quarantine_file(p, project_dir)
            if ok:
                quarantined += 1
        messagebox.showinfo(
            "Done",
            f"{quarantined} files moved to {QUARANTINE_DIRNAME}.\n"
            "Run `organizer.py dupes purge` when you're ready to permanently delete them.",
        )
        root.destroy()

    ttk.Button(bottom, text="Select All", command=select_all).pack(side="left", padx=5)
    ttk.Button(bottom, text="Select None", command=select_none).pack(side="left", padx=5)
    ttk.Button(bottom, text="Quarantine Selected", command=do_quarantine).pack(side="right", padx=10)

    root.mainloop()


def cmd_dupes_review(args):
    project_dir = get_project_dir(args)
    groups = load_duplicates(project_dir)
    if not groups:
        if groups is not None:
            print("No duplicate groups found — nothing to review.")
        return

    if args.gui:
        launch_gui_review(project_dir, groups)
    else:
        cmd_dupes_review_cli(project_dir, groups)


def cmd_dupes_purge(args):
    project_dir = get_project_dir(args)
    quarantine_dir = os.path.join(project_dir, QUARANTINE_DIRNAME)
    if not os.path.exists(quarantine_dir):
        print(f"No {QUARANTINE_DIRNAME} folder found — nothing to purge.")
        return

    files = []
    for root, _, filenames in os.walk(quarantine_dir):
        for f in filenames:
            files.append(os.path.join(root, f))

    if not files:
        print(f"{QUARANTINE_DIRNAME} is empty — nothing to purge.")
        return

    total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    print(f"{len(files)} files in {QUARANTINE_DIRNAME}, totaling {total_size / (1024*1024):.1f} MB.")
    print("THIS PERMANENTLY DELETES THEM. This cannot be undone by this tool.")
    if not args.yes:
        confirm = input("Type 'yes' to permanently delete everything in the quarantine folder: ")
        if confirm.strip().lower() != "yes":
            print("Purge cancelled. Nothing was deleted.")
            return

    deleted = errors = 0
    for f in files:
        try:
            duplicate_write_log(project_dir, f, "PERMANENTLY DELETED")
            os.remove(f)
            deleted += 1
        except Exception as e:
            duplicate_write_log(project_dir, f, "ERROR", str(e))
            errors += 1

    # clean up now-empty subfolders under quarantine
    for root, dirs, filenames in os.walk(quarantine_dir, topdown=False):
        if not dirs and not filenames and root != quarantine_dir:
            try:
                os.rmdir(root)
            except OSError:
                pass

    print(f"\nPurge complete. {deleted} files permanently deleted, {errors} errors.")


# --------------------------------------------------------------------------
# init-config
# --------------------------------------------------------------------------

def cmd_init_config(args):
    project_dir = get_project_dir(args)
    path = config_path(project_dir)
    if os.path.exists(path) and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return
    os.makedirs(project_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    print(f"Wrote starter config to:\n  {path}")
    print("Edit the 'roots' section for this machine before running scan/execute.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Unified File Organizer — scan, execute, undo, search (all in one file)."
    )
    parser.add_argument("--project-dir", help="Where config/logs live (default: this script's folder)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", help="Create organizer_config.json")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")
    p_init.set_defaults(func=cmd_init_config)

    p_scan = sub.add_parser("scan", help="Stage 1+2: dry run only, writes dry_run_results.txt")
    p_scan.set_defaults(func=cmd_scan)

    p_exec = sub.add_parser("execute", help="Stage 4: perform real moves for one configured root")
    p_exec.add_argument("--root", required=True, help="A root path from organizer_config.json, e.g. \"D:\\\\\" or /mnt/data")
    p_exec.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    p_exec.set_defaults(func=cmd_execute)

    p_undo = sub.add_parser("undo", help="Stage 5: reverse every successful move in move_log.csv")
    p_undo.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    p_undo.set_defaults(func=cmd_undo)

    p_search = sub.add_parser("search", help="Stage 6: search the file index, or --reindex to rebuild it")
    p_search.add_argument("term", nargs="?", help="Filename search term (case-insensitive)")
    p_search.add_argument("--ext", help="Filter by exact extension, e.g. .pdf")
    p_search.add_argument("--reindex", action="store_true", help="Rebuild the index across all configured roots")
    p_search.set_defaults(func=cmd_search)

    p_dupes = sub.add_parser("dupes", help="Stage 7: find and remove duplicate files")
    dupes_sub = p_dupes.add_subparsers(dest="dupes_command", required=True)

    p_dupes_scan = dupes_sub.add_parser("scan", help="Find duplicate files (read-only), writes duplicates.json")
    p_dupes_scan.set_defaults(func=cmd_dupes_scan)

    p_dupes_review = dupes_sub.add_parser(
        "review", help="Review duplicates.json and quarantine the copies you choose to remove"
    )
    p_dupes_review.add_argument(
        "--gui", action="store_true",
        help="Open a checklist window to review/select all groups at once, instead of one-by-one in the terminal"
    )
    p_dupes_review.set_defaults(func=cmd_dupes_review)

    p_dupes_purge = dupes_sub.add_parser(
        "purge", help="Permanently delete everything currently in the quarantine folder (irreversible)"
    )
    p_dupes_purge.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    p_dupes_purge.set_defaults(func=cmd_dupes_purge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
