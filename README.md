# SplatLaunch (frontend only)

Library browser, profiles, and save backup for Splatoon 2 and 3 folders you extracted from your own consoles.
No game runtime is included. The "Launch" button runs whatever executable you set in the runtime field.

## Setup (Windows)
1. Copy this folder to `D:\splatlauncher\`
2. `pip install -r requirements.txt`
3. `python app.py`
4. Choose the folder that contains your dumps, click Scan, pick a title, set its folders, then Save profile.

Title matching is by title ID in the folder name (Splatoon 2: 01003BC0000A0000, Splatoon 3: 0100C2500FC20000).
Profiles and config live in `%USERPROFILE%\.splatlaunch\`.
