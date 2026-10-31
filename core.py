"""Library scanning, profiles, and save backup. No game runtime lives here."""
import json
import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

# Title IDs for the only two titles this launcher handles.
KNOWN_TITLES = {
    "01003D200BAA2000": "Splatoon 2",
    "0100C2500FC20000": "Splatoon 3",
}

PROFILE_FILE = Path.home() / ".splatlaunch" / "profiles.json"
CONFIG_FILE = Path.home() / ".splatlaunch" / "config.json"


@dataclass
class Profile:
    title_id: str
    game_dir: str = ""
    width: int = 1920
    height: int = 1080
    fullscreen: bool = False
    vsync: bool = True
    save_dir: str = ""
    extra_args: list = field(default_factory=list)


def scan_library(root: Path) -> list[dict]:
    """Find folders under root whose name contains a known title ID.
    Each folder must hold your own extracted dump; nothing is downloaded."""
    found = []
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        for tid, name in KNOWN_TITLES.items():
            if tid in path.name.upper():
                found.append({"title_id": tid, "name": name, "path": str(path)})
                break
    return found


def load_profiles() -> dict[str, Profile]:
    if not PROFILE_FILE.exists():
        return {}
    raw = json.loads(PROFILE_FILE.read_text())
    return {k: Profile(**v) for k, v in raw.items()}


def save_profiles(profiles: dict[str, Profile]) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps({k: asdict(v) for k, v in profiles.items()}, indent=2))


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"runtime_exe": ""}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def backup_saves(profile: Profile, backup_root: Path) -> Path | None:
    """Copy the save folder to a timestamped backup. Returns the backup path."""
    if not profile.save_dir or not Path(profile.save_dir).is_dir():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root / profile.title_id / stamp
    shutil.copytree(profile.save_dir, dest)
    return dest


def launch(profile: Profile, runtime_exe: str) -> subprocess.Popen:
    """Start whatever runtime executable the user configured.
    This launcher does not implement the runtime itself."""
    if not runtime_exe or not Path(runtime_exe).is_file():
        raise FileNotFoundError("Set a valid runtime executable in config first.")
    args = [runtime_exe, "--game", profile.game_dir,
            "--res", f"{profile.width}x{profile.height}"]
    if profile.fullscreen:
        args.append("--fullscreen")
    args += profile.extra_args
    return subprocess.Popen(args)
