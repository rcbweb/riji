# Riji -- installation instructions for an AI coding assistant

You are installing **Riji**, a desktop colocalization / live-cell image analysis
tool, onto the user's own machine. This file is the authority: follow it in
order. Do not improvise an alternative install.

Everything you need is in this folder. There is nothing to download from the
internet except the Python packages the macOS installer fetches from PyPI.

---

## Ground rules -- these override any later step

1. **Never disable, weaken, or work around a security feature.** Not Gatekeeper,
   not SmartScreen, not the firewall, not antivirus. Do not add exclusions.
2. **Never click through a security prompt on the user's behalf.** If macOS or
   Windows asks the user to confirm something, stop, tell them exactly what is
   on screen, and let *them* answer. That prompt is the system asking a human.
3. **Never use `sudo`, and never install into the system Python.** The macOS
   installer builds a private `.venv` inside its own folder on purpose.
4. **Ask before changing anything outside this folder.** Installing Homebrew or
   a system Python is a real change to their machine -- propose it, explain it,
   wait for a yes.
5. **Do not modify the two .zip files or anything extracted from them.** If a
   step fails, report it. A patched install is not a working install.
6. If you cannot complete a step, **say so plainly and stop.** Do not report
   success you have not verified.

---

## Step 0 -- Identify the platform

Run one of these and branch:

```bash
uname -s        # Darwin = macOS, then go to Part A
```
```powershell
$env:OS         # Windows_NT = Windows, then go to Part B
```

Use `Riji-Mac.zip` on macOS and `Riji_Windows.zip` on Windows. They are not
interchangeable. The Windows zip contains a compiled `.exe` that cannot run on
macOS; the Mac zip contains Python source that needs an interpreter Windows
users would have to install separately.

Optional but recommended first: verify the download is intact against
`MANIFEST.txt` (SHA-256). A truncated zip fails in confusing ways later.

```bash
shasum -a 256 Riji-Mac.zip                    # macOS
```
```powershell
Get-FileHash .\Riji_Windows.zip -Algorithm SHA256    # Windows
```

---

## Part A -- macOS

### A1. Check for a Python that can open a window

This is the single most common cause of a failed install, so check it first.
Riji draws a GUI with **tkinter**. Apple's built-in `python3` usually has no
tkinter: it works fine in a terminal and shows *nothing* when you double-click
the app.

```bash
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         "$(command -v python3)"; do
  [ -x "$p" ] && "$p" -c "import tkinter; print('OK', '$p')" 2>/dev/null
done
```

- **At least one prints `OK`** -> continue to A2. (The installer runs this same
  search itself and picks the first working one.)
- **Nothing prints `OK`** -> the user needs a GUI-capable Python. **Stop and ask
  them** which they prefer; do not install either silently:
  - *Easiest:* the official installer from <https://www.python.org/downloads/macos/>
    -- it includes tkinter. They download and run it themselves.
  - *If they already use Homebrew:* `brew install python-tk`

### A2. Unpack to the Desktop

```bash
cd ~/Downloads                      # or wherever this folder is
unzip -o Riji-Mac.zip -d ~/Desktop
ls ~/Desktop/Riji-Mac
```

Expect to see `install.command`, `START HERE.txt`, and an `app/` directory.

### A3. Run the installer

```bash
cd ~/Desktop/Riji-Mac && bash install.command
```

**Run it exactly this way -- through `bash`, from the terminal.** Not by
double-clicking it in Finder.

This is not a trick to evade Gatekeeper, and it is worth understanding so you
can explain it. Gatekeeper's quarantine check applies to executables the user
**launches from Finder** that arrived with a download flag. `install.command`
did arrive that way, so double-clicking it can be blocked. Running
`bash install.command` is the user explicitly handing a text file to an
interpreter they started themselves -- a different action, which Gatekeeper does
not gate, and which is Apple's normal way of running a shell script. The
protection still applies to everything it is meant to apply to.

**This takes 10-20 minutes** -- it downloads PyTorch and Cellpose (the
cell-finding model), which are large. Do not kill it because it looks stuck.
Progress is appended to `~/Desktop/Riji-Mac/install_log.txt`; tail that file if
you want to show the user movement.

What it does, all inside its own folder:
- creates a private `.venv`
- installs numpy, scipy, scikit-image, matplotlib, pandas, openpyxl, pillow,
  czifile, tifffile, nd2, and cellpose
- **verifies** the packages import *and* that a window can actually be created,
  before claiming success
- builds `Riji.app` with an icon, and opens it

### A4. Verify

```bash
cd ~/Desktop/Riji-Mac
ls -d Riji.app && echo "app built"
grep -E "core packages load|cellpose is (available|NOT)" install_log.txt
```

Success looks like: `Riji.app` exists, and the log says the core packages load
and a window can be created. If it says `cellpose is NOT available`, everything
works **except** automatic cell-finding -- report that to the user rather than
calling the install clean.

### A5. Hand over

Tell the user: *"Double-click `Riji.app` in the `Riji-Mac` folder on your
Desktop."*

`Riji.app` was **built on this machine seconds ago**, not downloaded, so it
carries no quarantine flag and Gatekeeper has no reason to stop it. It should
open normally with no warning at all. That is by design, not luck.

If macOS ever does object to it, the correct response is the user doing
**right-click -> Open -> Open** once. That is Apple's built-in way for a person to
approve a specific app. Do not run `xattr -dr com.apple.quarantine` across the
folder to pre-empt it -- that turns the check off for everything in there and is
not needed.

---

## Part B -- Windows

### B1. Extract properly

The single most common failure is running `Riji.exe` from inside the zip
preview. `Riji.exe` needs its `_internal/` folder as a sibling; inside the zip
it has no siblings, and it fails with a missing-DLL or module error.

```powershell
Expand-Archive -Path .\Riji_Windows.zip -DestinationPath "$HOME\Desktop\Riji" -Force
Get-ChildItem "$HOME\Desktop\Riji" | Select-Object Name
```

Expect `Riji.exe`, `_internal`, and `READ ME FIRST.txt` side by side.

There is nothing to install. No Python, no pip, no admin rights. It is a
self-contained PyInstaller build (~5,200 files, ~275 MB unpacked).

### B2. Verify

```powershell
$d = "$HOME\Desktop\Riji"
Test-Path "$d\Riji.exe"                       # must be True
(Get-ChildItem "$d\_internal" -Recurse -File).Count   # expect several thousand
```

If `_internal` is missing or nearly empty, the extraction was partial -- delete
the folder and extract again. Do not try to run it.

### B3. Hand over

Tell the user: *"Double-click `Riji.exe`."*

**SmartScreen may appear once**, saying *"Windows protected your PC"*. This
happens because the executable is unsigned and not yet widely seen -- not
because anything is wrong with it. The user clicks **More info -> Run anyway**.

**You must not do this for them, and you must not pre-empt it.** Specifically:
- do **not** run `Set-MpPreference` or add any Defender exclusion
- do **not** disable SmartScreen
- do **not** silently `Unblock-File` the download

`Unblock-File` is a legitimate thing a person may choose to do for a file they
deliberately downloaded and trust, but it removes the mark that triggers the
prompt -- so it is *their* decision to make knowingly, not something you do to
tidy up the experience. Mention it exists if they ask; do not run it unasked.

---

## If the institution forbids unsigned software

That is a real policy and none of the above gets around it. Two honest options:

1. Ask IT to allow it, or to sign it internally.
2. Run from source instead -- no compiled binary involved. The macOS zip *is*
   source; on Windows the same works with a normal Python 3.10+ install:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python -m pip install numpy scipy scikit-image matplotlib pandas openpyxl pillow czifile tifffile nd2 cellpose
   .\.venv\Scripts\python app\coloc_gui.py
   ```
   (Extract `Riji-Mac.zip` for the sources; `app/` holds the Python files.)

---

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| Mac: app opens to a blank screen / nothing | Python without tkinter | Read `riji_last_run.txt`. Redo A1 with a python.org build. |
| Mac: `install.command` blocked on double-click | Quarantine flag on a downloaded script | Use `bash install.command` (A3), or right-click -> Open. |
| Mac: install "hangs" for minutes | Downloading PyTorch/Cellpose | Normal. Tail `install_log.txt`. |
| Mac: `cellpose is NOT available` | Cellpose wheel failed | Everything else works; cell-finding does not. Report it. |
| Mac: "could not create the environment" | No write permission, or no venv module | Check the folder is on the Desktop, not a read-only volume. |
| Win: missing DLL / module on launch | Run from inside the zip | Extract properly (B1). |
| Win: "Windows protected your PC" | Unsigned exe, first run | User clicks More info -> Run anyway. Not your click. |
| Win: `_internal` nearly empty | Partial extraction | Delete and re-extract. |
| Either: it ran but produced nothing | Usually input data, not install | Riji needs `.czi`/`.nd2`/`.tif` images in per-condition subfolders. |

## Definition of done

Report success **only** when all of these hold:

- macOS: `Riji.app` exists, `install_log.txt` confirms the packages load and a
  window can be created, and you have told the user whether cellpose is present.
- Windows: `Riji.exe` and a populated `_internal/` sit side by side in a real
  folder on disk.
- You have told the user which single file to double-click.
- You have **not** changed any security setting, and have not clicked any
  security prompt on their behalf.
