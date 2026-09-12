# Security warnings -- what they are, and what this does about them

Riji is **not code-signed**. Signing requires a paid Apple Developer or Microsoft
Authenticode certificate. That is the entire reason for any warning you see. It
is not a statement about what the software does.

Nothing in this bundle switches a protection off, and nothing asks you to.

## macOS

**The app itself should produce no warning at all.** `Riji.app` is not shipped
to you -- it does not exist until the installer *builds it on your Mac*. macOS
quarantines files that arrive from a browser; a file created locally a moment
ago has no quarantine flag, so Gatekeeper has nothing to check and the app opens
normally. This is why the Mac download is source plus an installer rather than a
prebuilt app.

**`install.command` may be blocked if you double-click it**, because that file
*was* downloaded. Two correct responses:

- Run it from Terminal: `cd ~/Desktop/Riji-Mac && bash install.command`
  Gatekeeper gates apps you launch from Finder, not scripts you hand to an
  interpreter yourself. Nothing is disabled.
- Or right-click it -> **Open** -> **Open**. This is Apple's own mechanism for
  approving one specific file. You are answering, and only for that file.

The installer deliberately does **not** run `xattr -dr com.apple.quarantine`
over its folder. An earlier version did. It was never needed to make the app
open, and clearing quarantine wholesale removes the check from every file in the
folder, including any you put there later.

## Windows

**SmartScreen may say "Windows protected your PC" the first time.** Click
**More info -> Run anyway**. It appears once per machine, and it appears because
the executable is unsigned and not yet widely distributed -- reputation, not
detection.

Do not disable SmartScreen and do not add an antivirus exclusion. Neither is
necessary, and both weaken protection for everything else on the machine.

## What Riji actually does on your computer

- Reads image files you point it at (`.czi`, `.nd2`, `.tif`).
- Writes results -- spreadsheets and figures -- into a `coloc_results/` folder
  inside the folder you pointed it at.
- On macOS, creates a `.venv` directory inside its own folder and downloads
  Python packages from PyPI into it.
- Makes no network connection at run time, contacts no server of ours, and
  sends no data anywhere. There is no telemetry.
- Installs nothing system-wide. Delete the folder and it is completely gone.

## If your institution forbids unsigned software

That is a legitimate policy and none of the above circumvents it. Ask IT to
allow or sign it, or run Riji from source -- the macOS zip contains the full,
readable Python source, and `AGENT_INSTALL.md` has the from-source steps for
Windows too.
