"""Library scanning, profiles, and save backup. No game runtime lives here."""
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# Title IDs for the only two titles this launcher handles.
KNOWN_TITLES = {
    "01003BC0000A0000": "Splatoon 2",
    "0100C2500FC20000": "Splatoon 3",
}
KNOWN_VERSIONS = {
    "01003BC0000A0000": "v5.5.1",
    "0100C2500FC20000": "v11.2.0",
}

PROFILE_FILE = Path.home() / ".splatlaunch" / "profiles.json"
CONFIG_FILE = Path.home() / ".splatlaunch" / "config.json"
DEFAULT_BACKUP_ROOT = Path.home() / ".splatlaunch" / "backups"


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
    backup_root: str = ""


def default_profile(title_id: str, game_dir: str, cfg: dict | None = None) -> Profile:
    cfg = cfg or load_config()
    return Profile(
        title_id=title_id,
        game_dir=game_dir,
        width=int(cfg.get("width", 1920)),
        height=int(cfg.get("height", 1080)),
        fullscreen=bool(cfg.get("fullscreen", False)),
        vsync=bool(cfg.get("vsync", True)),
        save_dir=str(cfg.get("save_dir", "")),
        backup_root=str(cfg.get("backup_root", str(DEFAULT_BACKUP_ROOT))),
    )


def _manifest_entry(path: Path) -> dict | None:
    candidates = [
        path / "manifest.json",
        path / "title.json",
        path / "config.json",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        tid = str(data.get("title_id") or data.get("titleId") or "").upper()
        if tid not in KNOWN_TITLES:
            continue
        display_name = data.get("name") or KNOWN_TITLES[tid]
        item_path = data.get("base_dir") or data.get("baseDir") or str(path)
        item_path = str(Path(item_path).expanduser()) if item_path else str(path)
        return {"title_id": tid, "name": display_name, "path": item_path}
    return None


def _folder_tags(root: Path, title_id: str) -> list[str]:
    tags: list[str] = []
    if not root.exists() or not root.is_dir():
        return tags
    lowered = [p.name.lower() for p in root.iterdir() if p.is_dir()]
    if title_id == "01003BC0000A0000" and any("octo" in item or "expansion" in item for item in lowered):
        tags.append("Octo Expansion")
    if title_id == "0100C2500FC20000":
        if any("inkopolis" in item for item in lowered):
            tags.append("Inkopolis")
        if any("side order" in item or "order sector" in item for item in lowered):
            tags.append("Side Order")
    return tags


def scan_library(root: Path) -> list[dict]:
    """Find title folders under root by manifest or known title ID.
    Each folder must hold your own extracted dump; nothing is downloaded."""
    found, seen = [], set()
    if not root.is_dir():
        return found

    for candidate in sorted(root.rglob("*.json"), key=lambda p: str(p).lower()):
        entry = _manifest_entry(candidate.parent)
        if entry and entry["title_id"] not in seen:
            entry["version"] = KNOWN_VERSIONS.get(entry["title_id"], "")
            entry["dlc_tags"] = _folder_tags(Path(entry["path"]), entry["title_id"])
            entry["save_dir"] = ""
            found.append(entry)
            seen.add(entry["title_id"])

    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        match = None
        for tid, name in KNOWN_TITLES.items():
            if tid in path.name.upper():
                match = (tid, name)
                break
        if not match:
            continue
        tid, name = match
        if tid in seen:
            continue
        display_path = str(path if path.is_dir() else path.parent)
        if any(Path(item["path"]) == Path(display_path) for item in found):
            continue
        found.append({
            "title_id": tid,
            "name": name,
            "path": display_path,
            "version": KNOWN_VERSIONS.get(tid, ""),
            "dlc_tags": _folder_tags(Path(display_path), tid),
            "save_dir": "",
        })
        seen.add(tid)
    return found


def load_profiles() -> dict[str, Profile]:
    if not PROFILE_FILE.exists():
        return {}
    raw = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return {k: Profile(**v) for k, v in raw.items()}


def save_profiles(profiles: dict[str, Profile]) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps({k: asdict(v) for k, v in profiles.items()}, indent=2), encoding="utf-8")


def load_config() -> dict:
    default = {
        "runtime_exe": "",
        "width": 1920,
        "height": 1080,
        "fullscreen": False,
        "vsync": True,
        "save_dir": "",
        "backup_root": str(DEFAULT_BACKUP_ROOT),
        "library_root": "",
    }
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        default.update(raw)
    return default


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def backup_saves(profile: Profile, backup_root: Path) -> Path | None:
    """Copy the save folder to a timestamped backup. Returns the backup path."""
    if not profile.save_dir or not Path(profile.save_dir).is_dir():
        return None
    backup_root = Path(profile.backup_root) if profile.backup_root else backup_root
    backup_root.mkdir(parents=True, exist_ok=True)
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
    if profile.vsync is False:
        args.append("--novsync")
    args += profile.extra_args
    return subprocess.Popen(args)
