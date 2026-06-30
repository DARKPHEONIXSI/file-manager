# VERIFY.md — Mandatory Executable Verification Checklist

**Rule: an executable is not "done" until every step below has been run and
its real output pasted/shown — not summarized, not assumed. `--help` working
and `init-config` working are NOT sufficient proof the build is good — the
last build proved exactly that and was still a non-functional stub.**

Run this in PowerShell from `D:\coding\file manager`.

---

## Step 0 — Confirm the source file is correct BEFORE building

```powershell
Get-Content .\organizer.py | Select-String "def cmd_scan|def cmd_execute|def cmd_undo|def build_index" 
```

**Expected:** 4 lines found, one for each function name.
**If 0 or fewer than 4 lines are found:** STOP. You are about to build the
wrong file. Do not run PyInstaller.

```powershell
(Get-Item .\organizer.py).Length
```

**Expected:** roughly 24,000 bytes (±2,000). If it's under 5,000 bytes, it's
the stub again — STOP.

---

## Step 1 — Clean rebuild

```powershell
Remove-Item -Recurse -Force .\dist, .\build, .\organizer.spec -ErrorAction SilentlyContinue
pyinstaller --onefile organizer.py
```

---

## Step 2 — Set up an isolated test sandbox (never test against real C:/D: data)

```powershell
New-Item -ItemType Directory -Force -Path .\verify_sandbox\project | Out-Null
New-Item -ItemType Directory -Force -Path .\verify_sandbox\data\loose_stuff | Out-Null
New-Item -ItemType Directory -Force -Path .\verify_sandbox\data\node_modules\pkg | Out-Null
New-Item -ItemType Directory -Force -Path ".\verify_sandbox\data\My Games\SomeGame\SaveGame" | Out-Null

"test file 1" | Out-File .\verify_sandbox\data\loose_stuff\report.txt
"test file 2" | Out-File ".\verify_sandbox\data\My Games\SomeGame\SaveGame\save1.dat"
"test file 3" | Out-File .\verify_sandbox\data\node_modules\pkg\index.js
```

This creates one file that SHOULD get organized (`report.txt`) and two that
SHOULD be excluded and left alone (`save1.dat` under `My Games`, `index.js`
under `node_modules`).

---

## Step 3 — init-config and edit it to point at the sandbox

```powershell
.\dist\organizer.exe --project-dir .\verify_sandbox\project init-config
```

Open `.\verify_sandbox\project\organizer_config.json` and replace the
`"roots"` list with:

```json
"roots": [
  { "path": "D:\\coding\\file manager\\verify_sandbox\\data", "mode": "recursive" }
]
```

---

## Step 4 — Run scan and check the actual output files

```powershell
.\dist\organizer.exe --project-dir .\verify_sandbox\project scan
Get-Content .\verify_sandbox\project\dry_run_results.txt
Get-Content .\verify_sandbox\project\flagged_for_review.txt
```

**Pass condition — all of these must be true:**
- `dry_run_results.txt` contains exactly one line, and it's `report.txt`
  with an `-> ... Organized\2026\...` destination.
- `flagged_for_review.txt` contains the `node_modules` and `SaveGame`
  folders with REASON text next to each.
- If `dry_run_results.txt` is empty or doesn't exist: STOP, the build is
  still broken.

---

## Step 5 — Run execute and check the real filesystem, not just the printed summary

```powershell
.\dist\organizer.exe --project-dir .\verify_sandbox\project execute --root "D:\coding\file manager\verify_sandbox\data" --yes
Get-ChildItem -Recurse .\verify_sandbox\data | Select-Object FullName
Get-Content .\verify_sandbox\project\move_log.csv
```

**Pass condition:**
- `report.txt` is now physically inside an `Organized\2026\06-June\...`
  subfolder (check the real path, don't trust the printed "Moved: 1").
- `save1.dat` and `index.js` are still in their original locations,
  untouched.
- `move_log.csv` has a row for `report.txt` with old path, new path, and a
  timestamp.

---

## Step 6 — Run undo and confirm it actually reverses

```powershell
echo yes | .\dist\organizer.exe --project-dir .\verify_sandbox\project undo
Get-ChildItem -Recurse .\verify_sandbox\data | Select-Object FullName
```

**Pass condition:** `report.txt` is back in `loose_stuff\`, not in an
`Organized\` subfolder anymore.

---

## Step 7 — Run search and confirm it actually finds the file

```powershell
.\dist\organizer.exe --project-dir .\verify_sandbox\project search --reindex
.\dist\organizer.exe --project-dir .\verify_sandbox\project search report
```

**Pass condition:** the second command prints a path ending in
`loose_stuff\report.txt`. "Found 0 matching files" is a fail.

---

## Step 8 — Clean up the sandbox

```powershell
Remove-Item -Recurse -Force .\verify_sandbox
```

---

## Only after Steps 4–7 all pass with real output shown

You — or Antigravity, if it's running this — may report the build as
verified. The report must quote the actual file contents/listings seen in
each step above, not a paraphrase of them. If any step fails, fix
`organizer.py` (or the build), then start over from Step 0.
