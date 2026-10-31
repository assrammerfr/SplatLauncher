"""PySide6 frontend: pick a library root, choose a title, edit its profile, launch."""
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import core


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplatLaunch")
        self.resize(720, 480)
        self.profiles = core.load_profiles()
        self.config = core.load_config()
        self.library = []
        self.current_tid = None

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)

        self.root_edit = QLineEdit()
        browse = QPushButton("Choose library folder...")
        browse.clicked.connect(self.pick_root)
        scan = QPushButton("Scan")
        scan.clicked.connect(self.scan)

        self.game_dir = QLineEdit()
        self.save_dir = QLineEdit()
        self.width = QSpinBox(); self.width.setRange(640, 7680); self.width.setValue(1920)
        self.height = QSpinBox(); self.height.setRange(480, 4320); self.height.setValue(1080)
        self.fullscreen = QCheckBox("Fullscreen")
        self.vsync = QCheckBox("VSync"); self.vsync.setChecked(True)

        form = QFormLayout()
        form.addRow("Game folder", self.game_dir)
        form.addRow("Save folder", self.save_dir)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("", self.fullscreen)
        form.addRow("", self.vsync)

        self.runtime_edit = QLineEdit(self.config.get("runtime_exe", ""))
        self.runtime_edit.setPlaceholderText("Path to your runtime executable")
        self.runtime_edit.editingFinished.connect(self.save_runtime)

        save_btn = QPushButton("Save profile")
        save_btn.clicked.connect(self.save_profile)
        backup_btn = QPushButton("Back up saves")
        backup_btn.clicked.connect(self.backup)
        launch_btn = QPushButton("Launch")
        launch_btn.clicked.connect(self.launch)

        root_row = QHBoxLayout(); root_row.addWidget(self.root_edit); root_row.addWidget(browse); root_row.addWidget(scan)
        btn_row = QHBoxLayout(); btn_row.addWidget(save_btn); btn_row.addWidget(backup_btn); btn_row.addWidget(launch_btn)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(QLabel("Runtime executable:"))
        right.addWidget(self.runtime_edit)
        right.addLayout(btn_row)
        right.addStretch()

        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        right_w = QWidget(); right_w.setLayout(right)
        body.addWidget(right_w, 2)

        root = QVBoxLayout()
        root.addLayout(root_row)
        root.addLayout(body)
        container = QWidget(); container.setLayout(root)
        self.setCentralWidget(container)

    def pick_root(self):
        path = QFileDialog.getExistingDirectory(self, "Library folder")
        if path:
            self.root_edit.setText(path)
            self.scan()

    def scan(self):
        self.list.clear()
        self.library = core.scan_library(Path(self.root_edit.text()))
        for entry in self.library:
            item = QListWidgetItem(f"{entry['name']}  ({entry['title_id']})")
            item.setData(256, entry)  # Qt.UserRole
            self.list.addItem(item)
        if not self.library:
            QMessageBox.information(self, "Nothing found",
                "No Splatoon 2 or 3 folders found under that path.")

    def on_select(self, item, _prev):
        if item is None:
            return
        entry = item.data(256)
        self.current_tid = entry["title_id"]
        p = self.profiles.get(self.current_tid, core.Profile(self.current_tid, game_dir=entry["path"]))
        self.game_dir.setText(p.game_dir)
        self.save_dir.setText(p.save_dir)
        self.width.setValue(p.width)
        self.height.setValue(p.height)
        self.fullscreen.setChecked(p.fullscreen)
        self.vsync.setChecked(p.vsync)

    def _current_profile(self) -> core.Profile:
        return core.Profile(
            title_id=self.current_tid,
            game_dir=self.game_dir.text(),
            save_dir=self.save_dir.text(),
            width=self.width.value(),
            height=self.height.value(),
            fullscreen=self.fullscreen.isChecked(),
            vsync=self.vsync.isChecked(),
        )

    def save_profile(self):
        if not self.current_tid:
            return
        self.profiles[self.current_tid] = self._current_profile()
        core.save_profiles(self.profiles)

    def save_runtime(self):
        self.config["runtime_exe"] = self.runtime_edit.text()
        core.save_config(self.config)

    def backup(self):
        if not self.current_tid:
            return
        dest = core.backup_saves(self._current_profile(), Path.home() / ".splatlaunch" / "backups")
        msg = f"Backed up to {dest}" if dest else "No save folder set (or it doesn't exist)."
        QMessageBox.information(self, "Backup", msg)

    def launch(self):
        if not self.current_tid:
            return
        try:
            core.launch(self._current_profile(), self.runtime_edit.text())
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Can't launch", str(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
