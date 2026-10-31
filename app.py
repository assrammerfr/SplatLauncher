"""PySide6 launcher frontend: SplatLaunch-inspired Games layout with Splatoon styling."""
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import core
import theme


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplatLaunch")
        self.resize(1280, 820)
        self.setMinimumSize(1100, 720)
        icon_path = Path(__file__).with_name("SplatLaunch-icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.profiles = core.load_profiles()
        self.config = core.load_config()
        self.library = []
        self.current_tid = None
        self.selected_game = None
        self.scan_cache = {}

        self.root_edit = QLineEdit(self.config.get("library_root", ""))
        self.list = QListWidget(); self.list.hide()
        self.backup_list = QListWidget(); self.gallery = QListWidget(); self.mod_list = QListWidget()

        self.game_dir = QLineEdit(); self.save_dir = QLineEdit();
        self.width = QSpinBox(); self.width.setRange(640, 7680); self.width.setValue(int(self.config.get("width", 1280)))
        self.height = QSpinBox(); self.height.setRange(480, 4320); self.height.setValue(int(self.config.get("height", 720)))
        self.fullscreen = QCheckBox("Fullscreen"); self.vsync = QCheckBox("VSync"); self.vsync.setChecked(bool(self.config.get("vsync", True)))
        self.runtime_edit = QLineEdit(self.config.get("runtime_exe", "")); self.runtime_edit.setPlaceholderText("Path to your runtime executable")
        self.runtime_edit.editingFinished.connect(self.save_runtime)
        self.save_dir.textChanged.connect(self.refresh_gallery)

        self.user_name = QLabel("sstg22")
        self.user_name.setObjectName("user-name")

        self.tab_bar = QTabWidget()
        self.tab_bar.setObjectName("main-tabs")
        self.tab_bar.addTab(self._games_tab(), "Games")
        self.tab_bar.addTab(self._gallery_tab(), "Gallery")
        self.tab_bar.addTab(self._saves_tab(), "Saves")
        self.tab_bar.addTab(self._mods_tab(), "Mods")
        self.tab_bar.addTab(self._settings_tab(), "Settings")
        self.tab_bar.addTab(self._help_tab(), "Help")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_top_bar())
        layout.addWidget(self.tab_bar)
        self.setCentralWidget(central)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.STYLESHEET)

        if self.root_edit.text().strip():
            self.scan(force=True)

    def _build_top_bar(self):
        bar = QWidget(); bar.setObjectName("top-bar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 12, 18, 12)
        logo = QLabel("SplatLaunch")
        logo.setObjectName("brand-mini")
        layout.addWidget(logo)
        layout.addStretch(1)
        layout.addWidget(self.user_name)
        return bar

    def _games_tab(self):
        page = QWidget(); page.setObjectName("games-page")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(18, 16, 18, 18)
        page_layout.setSpacing(18)

        self.hero = QWidget(); self.hero.setObjectName("hero-panel")
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(28, 28, 20, 28)
        hero_layout.setSpacing(24)

        self.hero_text = QWidget(); self.hero_text.setObjectName("hero-text-panel")
        hero_text_layout = QVBoxLayout(self.hero_text)
        hero_text_layout.setContentsMargins(0, 0, 0, 0)
        hero_text_layout.setSpacing(12)

        self.hero_title = QLabel("Splatoon 3")
        self.hero_title.setObjectName("hero-title")
        self.hero_version = QLabel("Version 11.2.0 • Inkopolis + Side Order")
        self.hero_version.setObjectName("hero-subtitle")

        action_row = QHBoxLayout();
        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("play-btn")
        self.play_btn.clicked.connect(self.launch)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("settings-btn")
        self.settings_btn.clicked.connect(lambda: self.tab_bar.setCurrentIndex(4))
        action_row.addWidget(self.play_btn)
        action_row.addWidget(self.settings_btn)

        hero_text_layout.addWidget(self.hero_title)
        hero_text_layout.addWidget(self.hero_version)
        hero_text_layout.addLayout(action_row)
        hero_text_layout.addStretch(1)
        hero_layout.addWidget(self.hero_text)

        self.hero_art = QLabel();
        self.hero_art.setObjectName("hero-art")
        hero_layout.addWidget(self.hero_art, 1)
        page_layout.addWidget(self.hero)

        label = QLabel("YOUR GAMES")
        label.setObjectName("games-label")
        page_layout.addWidget(label)

        self.games_grid = QWidget()
        self.games_layout = QGridLayout(self.games_grid)
        self.games_layout.setSpacing(14)
        page_layout.addWidget(self.games_grid)

        bottom = QWidget(); bottom.setObjectName("bottom-bar")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 8, 16, 8)
        self.preset_label = QLabel("Handheld • 720p • PTC off • Vulkan")
        self.preset_label.setObjectName("preset-label")
        esc = QLabel("Esc Quit")
        esc.setObjectName("esc-label")
        bottom_layout.addWidget(self.preset_label)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(esc)
        page_layout.addWidget(bottom)

        self._update_preset_label()
        return page

    def _gallery_tab(self):
        container = QWidget(); layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Gallery placeholder."))
        layout.addWidget(self.gallery)
        self.refresh_gallery();
        return container

    def _saves_tab(self):
        self.backup_list.currentItemChanged.connect(self.on_backup_select)
        refresh = QPushButton("Refresh list")
        refresh.clicked.connect(self.refresh_backups)
        restore = QPushButton("Restore selected backup")
        restore.clicked.connect(self.restore_backup)
        row = QHBoxLayout(); row.addWidget(refresh); row.addWidget(restore)
        container = QWidget(); layout = QVBoxLayout(container); layout.addLayout(row); layout.addWidget(self.backup_list)
        self.refresh_backups();
        return container

    def _mods_tab(self):
        refresh = QPushButton("Refresh mods")
        refresh.clicked.connect(self.refresh_mods)
        toggle = QPushButton("Toggle selected")
        toggle.clicked.connect(self.toggle_selected_mod)
        row = QHBoxLayout(); row.addWidget(refresh); row.addWidget(toggle)
        container = QWidget(); layout = QVBoxLayout(container); layout.addLayout(row); layout.addWidget(self.mod_list)
        self.refresh_mods();
        return container

    def _settings_tab(self):
        form = QFormLayout()
        form.addRow("Game folder", self.game_dir)
        form.addRow("Save folder", self.save_dir)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("", self.fullscreen)
        form.addRow("", self.vsync)
        form.addRow("Runtime executable", self.runtime_edit)

        save_btn = QPushButton("Save profile")
        save_btn.clicked.connect(self.save_profile)
        backup_btn = QPushButton("Back up saves")
        backup_btn.clicked.connect(self.backup)
        launch_btn = QPushButton("Launch")
        launch_btn.setObjectName("launch")
        launch_btn.clicked.connect(self.launch)
        row = QHBoxLayout(); row.addWidget(save_btn); row.addWidget(backup_btn); row.addWidget(launch_btn)

        container = QWidget(); layout = QVBoxLayout(container); layout.addLayout(form); layout.addLayout(row); layout.addStretch(1)
        return container

    def _help_tab(self):
        help_text = QTextBrowser(); help_text.setOpenExternalLinks(True)
        help_text.setHtml("<h2>SplatLaunch help</h2><p>Choose a library root, scan, then pick a title.</p><p>Use the performance preset and runtime options to tune launcher behavior.</p>")
        container = QWidget(); layout = QVBoxLayout(container); layout.addWidget(help_text)
        return container

    def _update_preset_label(self):
        opts = self.config.get("runtime_options", {})
        if opts.get("docked_mode") is False:
            preset = "Handheld • 720p • PTC off • Vulkan"
        else:
            preset = "Docked • 1080p • PTC off • Vulkan"
        self.preset_label.setText(preset)

    def _load_hero_image(self, title_id):
        image_path = Path(__file__).with_name("SplatLaunch-hero.jpg")
        if not image_path.exists():
            self.hero.setStyleSheet(f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(7,24,39,0.95), stop:1 rgba(7,24,39,0.7)); border-radius: 22px;")
            return
        self.hero.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(7,24,39,0.86), stop:0.5 rgba(7,24,39,0.65), stop:1 rgba(7,24,39,0.78)), url('{image_path.as_posix()}') center right no-repeat; border-radius: 22px;"
        )

    def _refresh_games_tiles(self):
        while self.games_layout.count():
            item = self.games_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self.library:
            add_game = QPushButton("+")
            add_game.setObjectName("add-game-tile")
            add_game.clicked.connect(self.pick_root)
            self.games_layout.addWidget(add_game, 0, 0)
            return

        for idx, entry in enumerate(self.library[:6]):
            tile = QPushButton()
            tile.setObjectName("game-tile")
            tile.setProperty("selected", entry["title_id"] == self.current_tid)
            title = entry.get("name", "Splatoon")
            tag = entry.get("version") or "Base install"
            if entry.get("dlc_tags"):
                tag = " • ".join(entry["dlc_tags"][:2])
            tile.setText(f"<span style='font-size: 18px; font-weight: 700;'>{title}</span><br><span style='font-size: 11px; opacity: 0.8;'> {tag}</span>")
            accent = theme.S2_PINK if entry.get("title_id") == "01003BC0000A0000" else theme.S3_YELLOW
            tile.setStyleSheet(f"QPushButton#game-tile {{ background: rgba(255,255,255,0.04); border: 1px solid {accent}; border-radius: 14px; padding: 16px; color: {theme.TEXT}; text-align: left; }} QPushButton#game-tile[selected='true'] {{ background: rgba(255,255,255,0.08); border: 2px solid {accent}; }}")
            tile.clicked.connect(lambda _=False, item=entry: self.select_entry(item))
            self.games_layout.addWidget(tile, idx // 3, idx % 3)

        add_game = QPushButton("+")
        add_game.setObjectName("add-game-tile")
        add_game.clicked.connect(self.pick_root)
        self.games_layout.addWidget(add_game, (len(self.library) + 1) // 3, (len(self.library) + 1) % 3)

    def update_hero_from_selection(self):
        if not self.selected_game:
            return
        title_id = self.selected_game.get("title_id", "")
        is_s2 = title_id == "01003BC0000A0000"
        self.hero_title.setText(self.selected_game.get("name", "Splatoon"))
        version = self.selected_game.get("version") or "Base install"
        tags = self.selected_game.get("dlc_tags", [])
        subtitle = f"Version {version}"
        if tags:
            subtitle += f" • {' + '.join(tags)}"
        self.hero_version.setText(subtitle)

        accent = theme.S2_PINK if is_s2 else theme.S3_YELLOW
        text_color = "#fff" if is_s2 else "#06141f"
        self.play_btn.setStyleSheet(f"background: {accent}; color: {text_color}; border-radius: 12px; padding: 12px 22px; font-weight: 800;")
        self.settings_btn.setStyleSheet("background: rgba(255,255,255,0.16); color: #fff; border-radius: 12px; padding: 12px 22px; font-weight: 700;")
        self._load_hero_image(title_id)

    def select_entry(self, entry):
        self.current_tid = entry["title_id"]
        self.selected_game = entry
        p = self.profiles.get(self.current_tid)
        if p is None:
            p = core.default_profile(self.current_tid, entry["path"], self.config)
        self.game_dir.setText(p.game_dir); self.save_dir.setText(p.save_dir)
        self.width.setValue(p.width); self.height.setValue(p.height)
        self.fullscreen.setChecked(p.fullscreen); self.vsync.setChecked(p.vsync)
        self.update_hero_from_selection();
        self._refresh_games_tiles()

    def _render_cards(self):
        if not self.library:
            self.selected_game = None; self.current_tid = None; self._refresh_games_tiles(); return
        if self.current_tid is None:
            self.current_tid = self.library[0]["title_id"]
        self.selected_game = next((entry for entry in self.library if entry["title_id"] == self.current_tid), self.library[0])
        self.update_hero_from_selection();
        self._refresh_games_tiles()

    def pick_root(self):
        path = QFileDialog.getExistingDirectory(self, "Library folder")
        if path:
            self.root_edit.setText(path)
            self.config["library_root"] = path
            core.save_config(self.config)
            self.scan(force=True)

    def scan(self, force: bool = False):
        root_text = self.root_edit.text().strip()
        if not root_text:
            self.library = []; self._render_cards(); return
        root = Path(root_text)
        key = str(root)
        if force or key not in self.scan_cache:
            self.scan_cache[key] = core.scan_library(root)
        self.library = self.scan_cache[key]
        if not self.library:
            self.current_tid = None; self.selected_game = None; self._render_cards(); QMessageBox.information(self, "Nothing found", "No Splatoon 2 or 3 folders found under that path.")
            return
        if self.current_tid is None or self.current_tid not in {entry["title_id"] for entry in self.library}:
            self.current_tid = self.library[0]["title_id"]
        self._render_cards()

    def _current_profile(self) -> core.Profile:
        profile = core.Profile(
            title_id=self.current_tid or "",
            game_dir=self.game_dir.text(),
            save_dir=self.save_dir.text(),
            width=self.width.value(),
            height=self.height.value(),
            fullscreen=self.fullscreen.isChecked(),
            vsync=self.vsync.isChecked(),
            backup_root=self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups")),
            runtime_options=dict(self.config.get("runtime_options", {})),
        )
        if self.current_tid in self.profiles:
            profile.extra_args = self.profiles[self.current_tid].extra_args
        profile.extra_args = core.get_runtime_extra_args(self.config)
        return profile

    def save_profile(self):
        if not self.current_tid:
            return
        self.config["width"] = self.width.value(); self.config["height"] = self.height.value(); self.config["fullscreen"] = self.fullscreen.isChecked(); self.config["vsync"] = self.vsync.isChecked(); self.config["save_dir"] = self.save_dir.text(); self.config["backup_root"] = self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups")); self.config["runtime_options"] = self.config.get("runtime_options", {})
        core.save_config(self.config)
        self.profiles[self.current_tid] = self._current_profile(); core.save_profiles(self.profiles)

    def backup(self):
        if not self.current_tid:
            return
        profile = self._current_profile();
        dest = core.backup_saves(profile, Path(self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups"))))
        self.refresh_backups();
        QMessageBox.information(self, "Backup", f"Backed up to {dest}" if dest else "No save folder set (or it doesn't exist).")

    def refresh_backups(self):
        self.backup_list.clear(); backup_root = Path(self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups")))
        if backup_root.exists():
            for path in sorted(backup_root.rglob("*"), key=lambda p: str(p).lower()):
                if path.is_dir():
                    item = QListWidgetItem(path.name); item.setData(256, str(path)); self.backup_list.addItem(item)
        if self.backup_list.count() == 0:
            self.backup_list.addItem("No backups yet")

    def on_backup_select(self, item, _prev):
        return

    def restore_backup(self):
        current = self.backup_list.currentItem();
        if current is None or not current.data(256):
            QMessageBox.warning(self, "Restore", "Pick a valid backup first."); return
        selected = Path(current.data(256)); target = Path(self.save_dir.text())
        if not target.parent.exists(): target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            QMessageBox.warning(self, "Restore", "The save folder already exists. Pick a different target or clear it first."); return
        try:
            core.restore_backup(str(target), str(selected))
        except FileExistsError as exc:
            QMessageBox.warning(self, "Restore", str(exc)); return
        QMessageBox.information(self, "Restore", f"Restored backup to {target}")

    def refresh_gallery(self):
        self.gallery.clear(); save_root = Path(self.save_dir.text()); images = []
        if save_root.exists():
            images = sorted(save_root.rglob("*.png")) + sorted(save_root.rglob("*.jpg")) + sorted(save_root.rglob("*.jpeg"))
        if not images:
            self.gallery.addItem("No screenshots found in the selected save folder yet."); return
        for image in images[:50]:
            item = QListWidgetItem(image.name); item.setData(256, str(image)); self.gallery.addItem(item)

    def open_gallery_image(self, item):
        if item is None or not item.data(256):
            return
        path = Path(item.data(256))
        if path.exists():
            QMessageBox.information(self, "Gallery", f"Image found: {path}")

    def save_runtime(self):
        self.config["runtime_exe"] = self.runtime_edit.text(); core.save_config(self.config)

    def refresh_mods(self):
        self.mod_list.clear();
        if not self.current_tid:
            self.mod_list.addItem("Select a title to view mods."); return
        root = Path(self.root_edit.text())
        mods = core.list_mods(root, self.current_tid)
        if not mods:
            self.mod_list.addItem("No mods found in mods/contents/<titleID>."); return
        for mod in mods:
            item = QListWidgetItem(f"{mod['name']} | {'enabled' if mod['enabled'] else 'disabled'}"); item.setData(256, mod); self.mod_list.addItem(item)

    def toggle_selected_mod(self, *args):
        if not self.current_tid:
            return
        item = self.mod_list.currentItem();
        if item is None:
            return
        mod = item.data(256)
        if not mod:
            return
        root = Path(self.root_edit.text())
        ok = core.toggle_mod(root, self.current_tid, mod["name"], not mod["enabled"])
        if ok:
            self.refresh_mods()
        else:
            QMessageBox.warning(self, "Mod manager", f"Could not toggle {mod['name']}.")

    def launch(self):
        if not self.current_tid:
            return
        try:
            core.launch(self._current_profile(), self.runtime_edit.text())
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Can't launch", str(exc))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    icon_path = Path(__file__).with_name("SplatLaunch-icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
