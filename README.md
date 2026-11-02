<p align="center">
  <img src="SplatLaunch-icon.png" alt="SplatLaunch icon" width="128" height="128">
</p>

# SplatLaunch

SplatLaunch is a library browser, profile manager, and save-backup tool for Splatoon 2 and Splatoon 3, with a native compatibility runtime in development. It is one product in two layers:

| Layer | Role | Status |
| --- | --- | --- |
| SplatLauncher | User interface: library, profiles, save backup, launch | Working interface, see Features below |
| SplatNative | Compatibility runtime: static recompilation that makes the games run | In development, Gate 1 not yet passed, see `docs/` |

No game files, keys, or firmware are included in this repository. You must supply your own files, extracted from your own consoles.

## Status

- **Launcher:** scans folders by title ID, manages per-title profiles, and backs up and restores saves.
- **Native runtime:** Gate 0 passed with constraints. Gate 1 is in progress — the current measured state is init 6,858 of 6,858, `nnMain` reached, then crash `ca3e5ec`, with no `GetService` log yet. There is no playable build and no release.
- **Release:** no binary release is available. A release follows Gate 1 (run to `nnMain` with a `GetService` log), Gate 2 (title and plaza rendering, measured against an emulator baseline), and Gate 3 (saves, DLC, and launcher integration, tested), in that order.

## Features

- Scan folders for Splatoon 2 (title ID `01003BC0000A0000`) and Splatoon 3 (title ID `0100C2500FC20000`) by title ID in the folder name
- Per-title profiles with folders, runtime executable, and options
- Save backup and restore
- Interface with game cards and tabs for Library, Settings, Saves, Gallery, and Help

The "Launch" button runs whatever executable is set in the runtime field for that profile. It does not download, extract, or provide a game.

## Setup (Windows)

1. Copy this folder to `D:\splatlauncher\`
2. `pip install -r requirements.txt`
3. `python app.py`
4. Choose the folder that contains your dumps, click Scan, pick a title, set its folders, then Save profile.

Profiles and configuration live in `%USERPROFILE%\.splatlaunch\`.

## Build

The packaged launcher is built with PyInstaller. The launcher icon is `SplatLaunch-icon.ico` (and `SplatLaunch-icon.png` for the README and previews).

```text
pyinstaller --clean --onefile --windowed --name SplatLaunch --icon SplatLaunch-icon.ico app.py
```

## Project structure

```text
app.py                  Launcher interface
core.py                 Scanning, profiles, and save backup
theme.py                Interface styling
SplatLaunch-icon.png    Launcher icon (preview)
SplatLaunch-icon.ico    Launcher icon (build)
requirements.txt        PySide6
screenshots/            Interface screenshots (not game footage)
docs/
  native-gate0/         Gate 0 audit, sources, and baseline evidence
  native-gate1/         Gate 1 source, tools, build guide, and run evidence
```

## Native runtime documentation

- `docs/native-gate0/README.md` — Gate 0 audit summary and constraints
- `docs/native-gate1/README.md` — Gate 1 current status and progress log
- `docs/native-gate1/BUILD.md` — How the native runtime is built and verified
- `docs/native-gate1/FILES.md` — What is source, what is generated, and what is not in this repository

The native runtime targets modern DX12 FL_11_1 GPUs (RX 5700 XT and newer). It is title-specific static recompilation, not a general Switch emulator, and it does not include online play.

## Contributors

- Freebuff — Gate 0 and Gate 1 builder
- Muse — build prompts, verification, and repository publishing
- Claude and VS Copilot — launcher interface work only, not the native runtime

## Notes

- You must supply your own game files. This project contains no game files, keys, or firmware.
- Screenshots in `screenshots/` show the interface only; they are not game footage.
- Generated build outputs (compiled objects, generated C sources, and executables) are not committed. Source, tools, and evidence logs are committed; see `docs/native-gate1/FILES.md`.
