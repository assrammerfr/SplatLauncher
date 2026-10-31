"""PySide6 frontend: pick a library root, choose a title, edit its profile, launch."""
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import core
import theme


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplatLaunch")
        self.resize(920, 620)
        self.profiles = core.load_profiles()
        self.config = core.load_config()
        self.library = []
        self.current_tid = None

        self.root_edit = QLineEdit(self.config.get("library_root", ""))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)
        self.backup_list = QListWidget()
        self.gallery = QListWidget()

        self.game_dir = QLineEdit()
        self.save_dir = QLineEdit()
        self.width = QSpinBox(); self.width.setRange(640, 7680); self.width.setValue(int(self.config.get("width", 1920)))
        self.height = QSpinBox(); self.height.setRange(480, 4320); self.height.setValue(int(self.config.get("height", 1080)))
        self.fullscreen = QCheckBox("Fullscreen")
        self.vsync = QCheckBox("VSync"); self.vsync.setChecked(bool(self.config.get("vsync", True)))

        self.runtime_edit = QLineEdit(self.config.get("runtime_exe", ""))
        self.runtime_edit.setPlaceholderText("Path to your runtime executable")
        self.runtime_edit.editingFinished.connect(self.save_runtime)
        self.save_dir.textChanged.connect(self.refresh_gallery)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._library_tab(), "Library")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._saves_tab(), "Saves")
        self.tabs.addTab(self._gallery_tab(), "Gallery")
        self.tabs.addTab(self._help_tab(), "Help")
        self.setCentralWidget(self.tabs)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.STYLESHEET)

    def _library_tab(self):
        browse = QPushButton("Choose library folder...")
        browse.clicked.connect(self.pick_root)
        scan = QPushButton("Scan")
        scan.clicked.connect(self.scan)

        row = QHBoxLayout(); row.addWidget(self.root_edit); row.addWidget(browse); row.addWidget(scan)
        wrapper = QWidget(); wrapper_layout = QVBoxLayout(); wrapper_layout.addLayout(row); wrapper_layout.addWidget(self.list); wrapper.setLayout(wrapper_layout)
        return wrapper

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
            "<p>1. Point the library folder at the root that contains your extracted Splatoon dump folders.</p>"
            "<p>2. Scan the root, pick the title, then set the game and save folders.</p>"
            "<p>3. Set a runtime executable such as a harmless test exe before launching the real runtime.</p>"
            "<h3>Common problems</h3>"
            "<ul>"
            "<li>PySide6 missing: run <b>pip install -r requirements.txt</b>.</li>"
            "<li>Title not found: check the folder name or add a manifest.json with <b>title_id</b>.</li>"
            "<li>Runtime exe not set: fill in the Runtime executable field before Launch.</li>"
            "<li>Folder not found: make sure the library root contains the extracted dump path.</li>"
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
            self.scan()

    def scan(self):
        self.list.clear()
        self.library = core.scan_library(Path(self.root_edit.text()))
        for entry in self.library:
            item = QListWidgetItem(f"{entry['name']}  ({entry['title_id']})")
            item.setData(256, entry)
            self.list.addItem(item)
        if not self.library:
            QMessageBox.information(self, "Nothing found",
                "No Splatoon 2 or 3 folders found under that path.")
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def on_select(self, item, _prev):
        if item is None:
            return
        entry = item.data(256)
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

    def _current_profile(self) -> core.Profile:
        profile = core.Profile(
            title_id=self.current_tid,
            game_dir=self.game_dir.text(),
            save_dir=self.save_dir.text(),
            width=self.width.value(),
            height=self.height.value(),
            fullscreen=self.fullscreen.isChecked(),
            vsync=self.vsync.isChecked(),
            backup_root=self.config.get("backup_root", str(Path.home() / ".splatlaunch" / "backups")),
        )
        if self.current_tid in self.profiles:
            profile.extra_args = self.profiles[self.current_tid].extra_args
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
        core.save_config(self.config)
        self.profiles[self.current_tid] = self._current_profile()
        core.save_profiles(self.profiles)

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
        shutil.copytree(selected, target)
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
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
