from pathlib import Path
import shutil
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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


class GameCard(QWidget):
    def __init__(self, entry, on_launch, on_select):
        super().__init__()
        self.entry = entry
        self.on_launch = on_launch
        self.on_select = on_select
        self.setObjectName("game-card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        title_id = entry.get("title_id", "")
        is_s2 = title_id == "01003BC0000A0000"
        self.primary = theme.S2_PINK if is_s2 else theme.S3_YELLOW
        self.secondary = theme.S2_TURQ if is_s2 else theme.S3_BLUE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel(entry.get("name", "Splatoon"))
        title.setObjectName("game-name")
        title.setStyleSheet(f"color: {self.primary};")
        top.addWidget(title)
        badge = QLabel(title_id)
        badge.setObjectName("game-badge")
        badge.setStyleSheet(f"background: {self.secondary}; color: #06141f; border-radius: 10px; padding: 4px 8px; font-weight: 700;")
        top.addWidget(badge, 0, Qt.AlignRight)
        layout.addLayout(top)

        tag_items = []
        if entry.get("version"):
            tag_items.append(entry["version"])
        for tag in entry.get("dlc_tags", []):
            tag_items.append(tag)
        if not tag_items:
            tag_items.append("Base install")
        tag_row = QHBoxLayout()
        for tag in tag_items:
            chip = QLabel(tag)
            chip.setObjectName("game-tag")
            chip.setStyleSheet(f"background: rgba(255,255,255,.06); border: 1px solid {self.secondary}; color: {theme.TEXT}; border-radius: 12px; padding: 4px 8px; font-size: 9pt;")
            tag_row.addWidget(chip)
        tag_row.addStretch()
        layout.addLayout(tag_row)

        status_bits = []
        dlc_status = entry.get("dlc_status", {})
        if title_id == "01003BC0000A0000":
            status_bits.append(f"Base: {'found' if dlc_status.get('base', False) else 'missing'}")
            status_bits.append(f"Update: {'found' if dlc_status.get('update', False) else 'missing'}")
            status_bits.append(f"Octo: {'found' if dlc_status.get('octo_expansion', False) else 'missing'}")
        elif title_id == "0100C2500FC20000":
            status_bits.append(f"Base: {'found' if dlc_status.get('base', False) else 'missing'}")
            status_bits.append(f"Update: {'found' if dlc_status.get('update', False) else 'missing'}")
            status_bits.append(f"Inkopolis: {'found' if dlc_status.get('inkopolis', False) else 'missing'}")
            status_bits.append(f"Side Order: {'found' if dlc_status.get('side_order', False) else 'missing'}")
        status_line = QLabel(" • ".join(status_bits))
        status_line.setObjectName("game-status")
        layout.addWidget(status_line)

        actions = QHBoxLayout()
        launch_btn = QPushButton("Launch")
        launch_btn.setObjectName("launch-card")
        launch_btn.setStyleSheet(f"background: {self.primary}; color: #071827; font-weight: 700; border: none; border-radius: 8px; padding: 8px 12px;")
        launch_btn.clicked.connect(lambda: self.on_launch(entry))
        actions.addWidget(launch_btn)
        details_btn = QPushButton("Open settings")
        details_btn.clicked.connect(lambda: self.on_select(entry))
        details_btn.setObjectName("secondary-card")
        actions.addWidget(details_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.setStyleSheet(f"QWidget#game-card {{ background: rgba(255,255,255,0.03); border: 1px solid {self.secondary}; border-radius: 14px; }} QLabel#game-name {{ font-size: 22px; font-weight: 800; }} QLabel#game-badge {{ font-size: 9pt; font-weight: 700; }} QLabel#game-status {{ color: {theme.MUTED}; font-size: 9pt; }}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplatLaunch")
        self.resize(1100, 720)
        icon_path = Path(__file__).with_name("SplatLaunch-icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.profiles = core.load_profiles()
        self.config = core.load_config()
        self.library = []
        self.current_tid = None
        self.scan_cache = {}

        self.root_edit = QLineEdit(self.config.get("library_root", ""))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)
        self.list.hide()
        self.backup_list = QListWidget()
        self.gallery = QListWidget()
        self.mod_list = QListWidget()

        self.game_dir = QLineEdit()
        self.save_dir = QLineEdit()
        self.width = QSpinBox(); self.width.setRange(640, 7680); self.width.setValue(int(self.config.get("width", 720)))
        self.height = QSpinBox(); self.height.setRange(480, 4320); self.height.setValue(int(self.config.get("height", 1280)))
        self.fullscreen = QCheckBox("Fullscreen")
        self.vsync = QCheckBox("VSync"); self.vsync.setChecked(bool(self.config.get("vsync", True)))
        self.performance_combo = QComboBox()
        self.performance_combo.addItem("Balanced")
        self.performance_combo.addItem("Performance")
        current_option = "Performance" if self.config.get("runtime_options", {}).get("docked_mode") is False else "Balanced"
        self.performance_combo.setCurrentText(current_option)

        self.runtime_edit = QLineEdit(self.config.get("runtime_exe", ""))
        self.runtime_edit.setPlaceholderText("Path to your runtime executable")
        self.runtime_edit.editingFinished.connect(self.save_runtime)
        self.save_dir.textChanged.connect(self.refresh_gallery)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._library_tab(), "Library")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._saves_tab(), "Saves")
        self.tabs.addTab(self._mods_tab(), "Mods")
        self.tabs.addTab(self._gallery_tab(), "Gallery")
        self.tabs.addTab(self._help_tab(), "Help")
        self.setCentralWidget(self.tabs)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.STYLESHEET)

    def _library_tab(self):
        browse = QPushButton("Choose Folder")
        browse.clicked.connect(self.pick_root)
        browse.setObjectName("library-action")
        scan = QPushButton("Scan")
        scan.clicked.connect(lambda: self.scan(force=True))
        scan.setObjectName("library-action")

        root_row = QHBoxLayout()
        root_row.addWidget(self.root_edit)
        root_row.addWidget(browse)
        root_row.addWidget(scan)

        self.library_scroll = QScrollArea()
        self.library_scroll.setWidgetResizable(True)
        self.library_container = QWidget()
        self.library_layout = QVBoxLayout(self.library_container)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        self.library_layout.setSpacing(14)
        self.library_scroll.setWidget(self.library_container)

        self.empty_state = self._build_empty_state()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self._brand_header())
        layout.addLayout(root_row)
        layout.addWidget(self.empty_state)
        layout.addWidget(self.library_scroll)
        self.library_scroll.hide()
        return container

    def _brand_header(self):
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        badge = QLabel("◉")
        badge.setObjectName("brand-mark")
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {theme.S2_PINK}, stop:0.55 {theme.S2_TURQ}, stop:1 rgba(0,0,0,0)); color: white; border-radius: 24px; min-width: 48px; min-height: 48px; font-size: 22px; font-weight: 700;")
        title = QLabel("SplatLaunch")
        title.setObjectName("brand-title")
        header_layout.addWidget(badge)
        header_layout.addWidget(title)
        header_layout.addStretch()
        return header

    def _build_empty_state(self):
        widget = QWidget()
        widget.setObjectName("empty-state")
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(30, 40, 30, 30)

        icon = QLabel("◌")
        icon.setObjectName("empty-icon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {theme.S2_PINK}, stop:0.5 {theme.S2_TURQ}, stop:1 rgba(0,0,0,0)); color: white; border-radius: 28px; min-width: 56px; min-height: 56px; font-size: 22px; font-weight: 700;")
        title = QLabel("No games found yet")
        title.setObjectName("empty-title")
        msg = QLabel("Choose a folder, then Scan to load your Splatoon 2 / 3 library.")
        msg.setObjectName("empty-message")
        buttons = QHBoxLayout()
        btn_choose = QPushButton("Choose Folder")
        btn_choose.setObjectName("library-action")
        btn_choose.clicked.connect(self.pick_root)
        btn_scan = QPushButton("Scan")
        btn_scan.setObjectName("library-action")
        btn_scan.clicked.connect(lambda: self.scan(force=True))
        buttons.addWidget(btn_choose)
        buttons.addWidget(btn_scan)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(msg)
        layout.addLayout(buttons)
        return widget

    def _settings_tab(self):
        form = QFormLayout()
        form.addRow("Game folder", self.game_dir)
        form.addRow("Save folder", self.save_dir)
        form.addRow("Resolution", self.width)
        form.addRow("Height", self.height)
        form.addRow("", self.fullscreen)
        form.addRow("", self.vsync)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.performance_combo)
        preset_btn = QPushButton("Apply preset")
        preset_btn.clicked.connect(self.apply_performance_preset)
        preset_row.addWidget(preset_btn)
        form.addRow("Performance preset", preset_row)
        form.addRow("Runtime executable", self.runtime_edit)

        save_btn = QPushButton("Save profile")
        save_btn.clicked.connect(self.save_profile)
        backup_btn = QPushButton("Back up saves")
        backup_btn.clicked.connect(self.backup)
        launch_btn = QPushButton("Launch")
        launch_btn.setObjectName("launch")
        launch_btn.clicked.connect(self.launch)

        button_row = QHBoxLayout()
        button_row.addWidget(save_btn)
        button_row.addWidget(backup_btn)
        button_row.addWidget(launch_btn)

        container = QWidget()
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addStretch()
        container.setLayout(layout)
        return container

    def _saves_tab(self):
        self.backup_list.currentItemChanged.connect(self.on_backup_select)
        refresh = QPushButton("Refresh list")
        refresh.clicked.connect(self.refresh_backups)
        restore = QPushButton("Restore selected backup")
        restore.clicked.connect(self.restore_backup)

        action_row = QHBoxLayout(); action_row.addWidget(refresh); action_row.addWidget(restore)
        container = QWidget(); layout = QVBoxLayout(); layout.addLayout(action_row); layout.addWidget(self.backup_list); container.setLayout(layout)
        self.refresh_backups()
        return container

    def _mods_tab(self):
        refresh = QPushButton("Refresh mods")
        refresh.clicked.connect(self.refresh_mods)
        toggle = QPushButton("Toggle selected")
        toggle.clicked.connect(self.toggle_selected_mod)
        row = QHBoxLayout(); row.addWidget(refresh); row.addWidget(toggle)
        self.mod_list.itemDoubleClicked.connect(self.toggle_selected_mod)
        container = QWidget(); layout = QVBoxLayout(); layout.addLayout(row); layout.addWidget(self.mod_list); container.setLayout(layout)
        self.refresh_mods()
        return container

    def _gallery_tab(self):
        self.gallery.itemDoubleClicked.connect(self.open_gallery_image)
        container = QWidget(); layout = QVBoxLayout(); layout.addWidget(QLabel("Photo gallery placeholder. Screenshots are read from save folders when present.")); layout.addWidget(self.gallery); container.setLayout(layout)
        self.refresh_gallery()
        return container

    def _help_tab(self):
        help_text = QTextBrowser()
        help_text.setOpenExternalLinks(True)
        help_text.setHtml(
            "<h2>SplatLaunch help</h2>"
            "<p>1. Set the root folder that contains your extracted dumps.</p>"
            "<p>2. Scan the library and select the game you want to configure.</p>"
            "<p>3. Use the Performance preset or custom runtime options for a smoother match.</p>"
            "<h3>Common problems</h3>"
            "<ul>"
            "<li>Title not found: check the folder name or add a manifest.json with <b>title_id</b>.</li>"
            "<li>Runtime exe not set: fill in the Runtime executable field before Launch.</li>"
            "<li>Missing DLC: the card will warn on the status line.</li>"
            "</ul>"
            "<p>Links: <a href='https://pyside.org/'>PySide6</a></p>"
        )
        container = QWidget(); layout = QVBoxLayout(); layout.addWidget(help_text); container.setLayout(layout)
        return container

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
            self.library = []
            self._render_cards()
            return
        root = Path(root_text)
        key = str(root)
        if force or key not in self.scan_cache:
            self.scan_cache[key] = core.scan_library(root)
        self.library = self.scan_cache[key]
        self._render_cards()
        if not self.library:
            QMessageBox.information(self, "Nothing found", "No Splatoon 2 or 3 folders found under that path.")

    def _render_cards(self):
        while self.library_layout.count():
            item = self.library_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.library:
            self.library_scroll.hide()
            self.empty_state.show()
            return
        self.empty_state.hide()
        self.library_scroll.show()
        for entry in self.library:
            card = GameCard(entry, self.launch_entry, self.select_entry)
            self.library_layout.addWidget(card)
        self.library_layout.addStretch()

    def select_entry(self, entry):
        self.current_tid = entry["title_id"]
        p = self.profiles.get(self.current_tid)
        if p is None:
            p = core.default_profile(self.current_tid, entry["path"], self.config)
        self.game_dir.setText(p.game_dir)
        self.save_dir.setText(p.save_dir)
        self.width.setValue(p.width)
        self.height.setValue(p.height)
        self.fullscreen.setChecked(p.fullscreen)
        self.vsync.setChecked(p.vsync)
        self.refresh_mods()

    def launch_entry(self, entry):
        self.select_entry(entry)
        self.launch()

    def on_select(self, item, _prev):
        if item is None:
            return
        self.select_entry(item.data(256))

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
        self.config["width"] = self.width.value()
        self.config["height"] = self.height.value()
        self.config["fullscreen"] = self.fullscreen.isChecked()
        self.config["vsync"] = self.vsync.isChecked()
        self.config["save_dir"] = self.save_dir.text()
        self.config["backup_root"] = self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups"))
        self.config["runtime_options"] = self.config.get("runtime_options", {})
        core.save_config(self.config)
        self.profiles[self.current_tid] = self._current_profile()
        core.save_profiles(self.profiles)

    def apply_performance_preset(self):
        option = self.performance_combo.currentText()
        if option == "Performance":
            opts = {
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
            }
        else:
            opts = {
                "docked_mode": True,
                "resolution": "1080p",
                "res_scale": 1,
                "ptc": False,
                "memory_mapping": "HostMapped",
                "graphics_api": "Vulkan",
                "audio": "SDL3",
                "aa": "None",
                "filtering": "Bilinear",
                "anisotropy": 4,
                "texture_recompression": False,
            }
        self.config["runtime_options"] = opts
        if option == "Performance":
            self.width.setValue(1280)
            self.height.setValue(720)
        else:
            self.width.setValue(1920)
            self.height.setValue(1080)
        self.fullscreen.setChecked(False)
        self.vsync.setChecked(True)
        self.config["width"] = self.width.value()
        self.config["height"] = self.height.value()
        self.config["fullscreen"] = False
        self.config["vsync"] = True
        core.save_config(self.config)
        QMessageBox.information(self, "Performance preset", f"Applied {option} preset.")

    def backup(self):
        if not self.current_tid:
            return
        profile = self._current_profile()
        dest = core.backup_saves(profile, Path(self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups"))))
        self.refresh_backups()
        msg = f"Backed up to {dest}" if dest else "No save folder set (or it doesn't exist)."
        QMessageBox.information(self, "Backup", msg)

    def refresh_backups(self):
        self.backup_list.clear()
        backup_root = Path(self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups")))
        if backup_root.exists():
            for path in sorted(backup_root.rglob("*"), key=lambda p: str(p).lower()):
                if path.is_dir():
                    item = QListWidgetItem(path.name)
                    item.setData(256, str(path))
                    self.backup_list.addItem(item)
        if self.backup_list.count() == 0:
            self.backup_list.addItem("No backups yet")

    def on_backup_select(self, item, _prev):
        if item is None:
            return

    def restore_backup(self):
        current = self.backup_list.currentItem()
        if current is None or not current.data(256):
            QMessageBox.warning(self, "Restore", "Pick a valid backup first.")
            return
        selected = Path(current.data(256))
        target = Path(self.save_dir.text())
        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            QMessageBox.warning(self, "Restore", "The save folder already exists. Pick a different target or clear it first.")
            return
        try:
            core.restore_backup(str(target), str(selected))
        except FileExistsError as exc:
            QMessageBox.warning(self, "Restore", str(exc))
            return
        QMessageBox.information(self, "Restore", f"Restored backup to {target}")

    def refresh_gallery(self):
        self.gallery.clear()
        save_root = Path(self.save_dir.text())
        images = []
        if save_root.exists():
            images = sorted(save_root.rglob("*.png")) + sorted(save_root.rglob("*.jpg")) + sorted(save_root.rglob("*.jpeg"))
        if not images:
            self.gallery.addItem("No screenshots found in the selected save folder yet.")
            return
        for image in images[:50]:
            item = QListWidgetItem(image.name)
            item.setData(256, str(image))
            self.gallery.addItem(item)

    def open_gallery_image(self, item):
        if item is None or not item.data(256):
            return
        path = Path(item.data(256))
        if path.exists():
            QMessageBox.information(self, "Gallery", f"Image found: {path}")

    def save_runtime(self):
        self.config["runtime_exe"] = self.runtime_edit.text()
        core.save_config(self.config)

    def refresh_mods(self):
        self.mod_list.clear()
        if not self.current_tid:
            self.mod_list.addItem("Select a title to view mods.")
            return
        root = Path(self.root_edit.text())
        mods = core.list_mods(root, self.current_tid)
        if not mods:
            self.mod_list.addItem("No mods found in mods/contents/<titleID>.")
            return
        for mod in mods:
            label = f"{mod['name']} | {'enabled' if mod['enabled'] else 'disabled'}"
            item = QListWidgetItem(label)
            item.setData(256, mod)
            self.mod_list.addItem(item)

    def toggle_selected_mod(self, *args):
        if not self.current_tid:
            return
        item = self.mod_list.currentItem()
        if item is None:
            return
        mod = item.data(256)
        if not mod:
            return
        root = Path(self.root_edit.text())
        new_state = not mod["enabled"]
        ok = core.toggle_mod(root, self.current_tid, mod["name"], new_state)
        if ok:
            self.refresh_mods()
        else:
            QMessageBox.warning(self, "Mod manager", f"Could not toggle {mod['name']}.")

    def launch(self):
        if not self.current_tid:
            return
        try:
            core.launch(self._current_profile(), self.runtime_edit.text())
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Can't launch", str(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    icon_path = Path(__file__).with_name("SplatLaunch-icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
