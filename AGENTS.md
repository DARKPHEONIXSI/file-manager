# Project Rules — File Organizer System

## Project location
- Confirmed: this project lives at `D:\coding\file manager`. All code, AGENTS.md, and logs (`move_log.csv`, `dry_run_results.txt`, `flagged_for_review.txt`) stay there.

## REDESIGN: Scope & Safety — Drive-Specific Strategies
- Files must NEVER move to a different drive than the one they're currently on.
- **In-Place Organization:** When organizing a folder, if it contains loose files directly inside it, create a subfolder named "Organized" INSIDE that exact same folder, with the structure: `Organized\Year\MM-Month\Week N (date range)\`. Move only that folder's own direct loose files into it, sorted by Created date.
- A folder's own "Organized" subfolder (once created) is never treated as a source of files to reorganize again — skip it on repeat runs.

### General OS vs. Data Drive Rule
- **Data Drives (e.g., D:, E:):** Full recursive in-place organization. Check every folder at every depth. If it contains loose files, organize them locally.
- **OS/Boot Drives (e.g., C:):** If a drive has `\Windows` or vendor folders like `\hp`, `\Intel`, `\Dell`, `\NVIDIA`, `\AMD` at its root, it is an OS drive. **Default to ALLOWLIST scope (only known personal folders) rather than blacklist scope.**

### Drive Scopes
- **C: Drive (OS Drive):** STRICT ALLOWLIST SCOPE. Scan exclusively inside: `C:\Users\<me>\Desktop`, `Downloads`, `Documents`, `Pictures`, `Videos`, `Music`. Do not recurse into `AppData` or any folder outside of `C:\Users\<me>\`. The previous "scan all of C: except known bad folders" approach is discarded as unsafe.
- **D: and E: Drives (Data Drives):** Full recursive in-place organization is approved.
- Do NOT include network drives or cloud-sync folders (OneDrive, Google Drive, Dropbox) unless explicitly approved by name.

## EXCLUSION LOGIC — STRUCTURAL RULE
Before queuing ANY folder for reorganization, check it against this signature-based heuristic. If ANY signal is present, exclude the folder and its contents, and write it to `flagged_for_review.txt` with the matched signal as the reason.

A folder is treated as app-managed (not personal/loose files) if it matches any of:
1. Folder name (anywhere in the path, case-insensitive) is or contains: Cache, ShaderCache, ShaderByteCode, WebCache, SaveGame, SaveGames, Saved Games, .git, .venv, node_modules, __pycache__, dist, build, target, .next, models, .ollama
2. Folder name ends in a known bundle extension: .musiclibrary, .photoslibrary, .tvlibrary
3. Folder contains a root-level ownership marker: manifest.json, config.json, .lock, package.json, requirements.txt
4. Folder sits under a known app-data parent: `\My Games\<app>\`, `\Saved Games\<app>\`, `\AppData\`, `\Steam\steamapps\`, `\Epic Games\`
5. Folder contains config/profile/settings file types as the majority of its contents: .cfg, .ini, .bin, .bindings, .peace, .dotx-profile-style files
6. Path length would exceed 260 characters after the Organized\ prefix is added
7. Folder that is or resembles a Windows system folder (`\Windows`, `\$Recycle.Bin`, `\System Volume Information`, `\Recovery`, `\Boot`, `\PerfLogs`), or anything with the hidden/system attribute.
8. Program Files (`\Program Files`, `\Program Files (x86)`, `\ProgramData`, or contains .exe/.dll).
9. Vendor/Driver folders (`hp`, `Intel`, `Dell`, `NVIDIA`, `AMD`).

## FALLBACK LIST (secondary)
`\Adobe\Premiere Pro\`, `\Adobe\*\Profile-*\`, `HuggingFace`, `LM Studio\models`, `ComfyUI\models`, `stable-diffusion-webui\models`, `Dropbox\.dropbox.cache`, `OneDrive\.849C9593-D756-4E56-8D6E-42412F2A707B`, `Google Drive\*\.tmp.drivedownload`

## PIPELINE — STRICT STAGE GATES
- **Stage 1 — Scan & Propose:** build file list per in-scope drive only.
- **Stage 2 — Dry Run:** write `dry_run_results.txt` and `flagged_for_review.txt`. No file is touched.
- **Stage 3 — Human Review Gate:** STOP. Do not proceed to Stage 4 without explicit approval referencing the specific dry run.
- **Stage 4 — Execute:** write to `move_log.csv` BEFORE each move. Auto-rename on collision. Process one drive at a time; STOP after each drive.
- **Stage 5 — Undo (on request):** replay move_log.csv in reverse.
- **Stage 6 — Search tool:** index by filename/extension.

## VERIFICATION REQUIREMENTS
- Never report a summary-only completion. Claims of N files moved must match `move_log.csv`.
- Before claiming a drive is "complete," confirm 0 entries under any excluded folder appear in `dry_run_results.txt`.
- Maintain a per-drive status ledger.

## Data safety
- Never overwrite an existing file. On filename collision, auto-rename with a numeric suffix and log it.
- Every file move must be logged to `move_log.csv` (original path, new path, timestamp) BEFORE the move happens, not after.
- Never run a real move without the user first reviewing and approving an Artifact (dry run / plan) showing exactly what will happen.
- If unsure whether a folder is OS-critical, exclude it and flag it to the user rather than guessing.

I am a total beginner with code — explain every Artifact and plan in plain language, not just technical task names.
