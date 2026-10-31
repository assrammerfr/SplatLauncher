"""Library scanning, profiles, save backup, DLC detection, and runtime presets."""
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

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
    runtime_options: dict = field(default_factory=dict)


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
        runtime_options=dict(cfg.get("runtime_options", {})),
    )


def _manifest_entry(path: Path) -> dict | None:
    candidates = [path / "manifest.json", path / "title.json", path / "config.json"]
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


def _folder_exists_named(root: Path, terms: list[str]) -> bool:
    if not root.exists() or not root.is_dir():
        return False
    names = [p.name.lower() for p in root.iterdir() if p.is_dir()]
    for term in terms:
        if any(term in name for name in names):
            return True
    return False


def detect_dlc_status(title_id: str, root: Path) -> dict:
    root = Path(root)
    status = {}
    if title_id == "01003BC0000A0000":
        status["base"] = root.exists()
        status["update"] = _folder_exists_named(root, ["update", "patch"])
        status["octo_expansion"] = _folder_exists_named(root, ["octo", "expansion"])
    elif title_id == "0100C2500FC20000":
        status["base"] = root.exists()
        status["update"] = _folder_exists_named(root, ["update", "patch"])
        status["inkopolis"] = _folder_exists_named(root, ["inkopolis"])
        status["side_order"] = _folder_exists_named(root, ["side order", "order sector"])
    return status


def scan_library(root: Path) -> list[dict]:
    found, seen = [], set()
    if not root.is_dir():
        return found

    for candidate in sorted(root.rglob("*.json"), key=lambda p: str(p).lower()):
        entry = _manifest_entry(candidate.parent)
        if entry and entry["title_id"] not in seen:
            entry["version"] = KNOWN_VERSIONS.get(entry["title_id"], "")
            entry["dlc_tags"] = _folder_tags(Path(entry["path"]), entry["title_id"])
            entry["dlc_status"] = detect_dlc_status(entry["title_id"], Path(entry["path"]))
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
        entry = {
            "title_id": tid,
            "name": name,
            "path": display_path,
            "version": KNOWN_VERSIONS.get(tid, ""),
            "dlc_tags": _folder_tags(Path(display_path), tid),
            "dlc_status": detect_dlc_status(tid, Path(display_path)),
            "save_dir": "",
        }
        found.append(entry)
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
        "width": 720,
        "height": 1280,
        "fullscreen": False,
        "vsync": True,
        "save_dir": "",
        "backup_root": str(DEFAULT_BACKUP_ROOT),
        "library_root": "",
        "runtime_options": {
            "docked_mode": False,
            "resolution": "720p",
            "res_scale": 1,
            "ptc": False,
            "memory_mapping": "HostMapped",
            "graphics_api": "Vulkan",
            "audio": "SDL3",
            "aa": "None",
            "filtering": "Bilinear",
            "anisotropy": 4,
            "texture_recompression": False,
        },
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


def get_runtime_extra_args(cfg: dict | None = None) -> list[str]:
    cfg = (cfg or load_config()).get("runtime_options", {})
    args = []
    if cfg.get("docked_mode") is False:
        args.append("--docked-mode false")
    if cfg.get("graphics_api"):
        args.append(f"--graphics-api {cfg.get('graphics_api')}")
    if cfg.get("memory_mapping"):
        args.append(f"--memory-mapping {cfg.get('memory_mapping')}")
    if cfg.get("audio"):
        args.append(f"--audio {cfg.get('audio')}")
    if cfg.get("aa"):
        args.append(f"--aa {cfg.get('aa')}")
    if cfg.get("filtering"):
        args.append(f"--filtering {cfg.get('filtering')}")
    if cfg.get("anisotropy"):
        args.append(f"--anisotropy {cfg.get('anisotropy')}")
    if cfg.get("texture_recompression") is False:
        args.append("--texture-recompression off")
    return args


def backup_saves(profile: Profile, backup_root: Path) -> Path | None:
    if not profile.save_dir or not Path(profile.save_dir).is_dir():
        return None
    backup_root = Path(profile.backup_root) if profile.backup_root else backup_root
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root / profile.title_id / stamp
    shutil.copytree(profile.save_dir, dest)
    return dest


def restore_backup(save_dir: str, backup_path: str) -> Path:
    target = Path(save_dir)
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Save folder already exists at {target}.")
    shutil.copytree(backup_path, target)
    return target


def launch(profile: Profile, runtime_exe: str) -> subprocess.Popen:
    if not runtime_exe or not Path(runtime_exe).is_file():
        raise FileNotFoundError("Set a valid runtime executable in config first.")
    args = [runtime_exe, "--game", profile.game_dir, "--res", f"{profile.width}x{profile.height}"]
    if profile.fullscreen:
        args.append("--fullscreen")
    if profile.vsync is False:
        args.append("--novsync")
    for value in profile.extra_args:
        if value:
            args.append(value)
    return subprocess.Popen(args)


def list_mods(root: Path, title_id: str) -> list[dict]:
    mods_root = Path(root) / "mods" / "contents" / title_id
    disabled_root = Path(root) / "mods" / "disabled" / title_id
    entries = []
    if not mods_root.exists():
        return entries
    for folder in sorted(p for p in mods_root.iterdir() if p.is_dir()):
        enabled = True
        disabled_path = disabled_root / folder.name
        if disabled_path.exists():
            enabled = False
        entries.append({
            "name": folder.name,
            "path": str(folder),
            "enabled": enabled,
        })
    return entries


def toggle_mod(root: Path, title_id: str, mod_name: str, enabled: bool) -> bool:
    mods_root = Path(root) / "mods" / "contents" / title_id
    disabled_root = Path(root) / "mods" / "disabled" / title_id
    source = mods_root / mod_name
    dest = disabled_root / mod_name
    if enabled:
        if source.exists():
            return True
        if dest.exists():
            if not source.parent.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(source))
            return True
        return False
    if not source.exists():
        return False
    if not disabled_root.exists():
        disabled_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return True
