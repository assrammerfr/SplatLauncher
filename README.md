<p align="center">
  <img src="SplatLaunch-icon.png" alt="SplatLaunch icon" width="128" height="128">
</p>

# SplatLaunch

SplatLaunch is a library browser, profile manager, and save-backup tool for Splatoon 2 and Splatoon 3 folders you extracted from your own consoles.

No game runtime is included. The "Launch" button runs whatever executable you set in the runtime field.

## Features
- Scan folders for Splatoon 2 and Splatoon 3 by title ID
- Per-title profiles with folders, runtime, and options
- Save backup and restore
- Splatoon-themed interface with game cards, tabs for Library, Settings, Saves, Gallery, and Help

## Setup (Windows)
1. Copy this folder to `D:\splatlauncher\`
2. `pip install -r requirements.txt`
3. `python app.py`
4. Choose the folder that contains your dumps, click Scan, pick a title, set its folders, then Save profile.

Title matching is by title ID in the folder name (Splatoon 2: 01003BC0000A0000, Splatoon 3: 0100C2500FC20000).
Profiles and config live in `%USERPROFILE%\.splatlaunch\`.

## Build
The packaged release is built with PyInstaller. The launcher icon is `SplatLaunch-icon.ico` (and `SplatLaunch-icon.png` for the README and previews). Build with the icon, for example:
```
pyinstaller --clean --onefile --windowed --name SplatLaunch --icon SplatLaunch-icon.ico app.py
```

## Files
- `app.py` — the launcher interface
- `core.py` — scanning, profiles, and save backup
- `theme.py` — Splatoon-themed styling
- `SplatLaunch-icon.png` / `SplatLaunch-icon.ico` — the launcher icon
- `requirements.txt` — PySide6

## Notes
- You must supply your own game files. This project contains no game files, keys, or firmware.
- Screenshots in `screenshots/` show the interface; they are not game footage.
