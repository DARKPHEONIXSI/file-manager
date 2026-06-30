#!/usr/bin/env python3
"""
organizer.py — Unified File Organizer (single-file, cross-platform)

Combines everything from the original multi-script project into one file:
  - scanner_v3.py   -> `scan` subcommand    (dry run, never touches files)
  - executor_v4.py  -> `execute` subcommand (performs the real moves)
  - undo_all.py     -> `undo` subcommand    (reverses a move_log.csv)
  - search.py       -> `search` subcommand  (indexes + searches files)

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
"""

import argparse
import csv
import datetime
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
MAX_PATH_LEN = 260  # historical Windows limit; harmless as a soft check elsewhere

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


def cmd_scan(args):
    project_dir = get_project_dir(args)
    config = load_config(project_dir)

    dry_run_results = []
    flagged_folders = []

    for r in config["roots"]:
        root_path = r["path"]
        mode = r.get("mode", "recursive")
        print(f"Scanning {root_path} ({mode})...")
        if mode == "allowlist":
            for sub in r.get("allowlist", []):
                sub_path = os.path.join(root_path, sub)
                walk_and_process(sub_path, project_dir, config, dry_run_results, flagged_folders)
        else:
            walk_and_process(root_path, project_dir, config, dry_run_results, flagged_folders)

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

    for r in config["roots"]:
        root_path = r["path"]
        mode = r.get("mode", "recursive")
        if mode == "allowlist":
            for sub in r.get("allowlist", []):
                print(f"Scanning {os.path.join(root_path, sub)}...")
                scan_path(os.path.join(root_path, sub))
        else:
            print(f"Scanning {root_path}...")
            scan_path(root_path)

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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
