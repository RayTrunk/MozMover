#!/usr/bin/env python3
"""
MozMover.py  –  Firefox / Thunderbird profile backup & restore
PySide6 + psutil (for graceful shutdown)
Tested with Python 3.9+  /  PySide6 6.4+
Author:  you
License: MIT
"""
import datetime, json, os, shutil, sys, tempfile, zipfile
from pathlib import Path
import psutil  # pip install psutil
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QListWidget, QFileDialog,
                               QLabel, QMessageBox, QProgressDialog, QListWidgetItem,
                               QComboBox, QCheckBox, QToolBar)
from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QFont, QColor, QPalette, QAction, QIcon

# --------------------------------------------------------------------------- #
#  Platform paths                                                             #
# --------------------------------------------------------------------------- #
OS_MAP = {
    "win32": {
        "firefox":  Path(os.environ["APPDATA"]) / "Mozilla/Firefox",
        "thunderbird":  Path(os.environ["APPDATA"]) / "Thunderbird",
    },
    "darwin": {
        "firefox":  Path.home() / "Library/Application Support/Firefox",
        "thunderbird":  Path.home() / "Library/Thunderbird",
    },
    "linux": {
        "firefox":  Path.home() / ".mozilla/firefox",
        "thunderbird":  Path.home() / ".thunderbird",
    },
}

# --------------------------------------------------------------------------- #
#  Translations                                                               #
# --------------------------------------------------------------------------- #
TRANSLATIONS = {
    "de": {
        "title": "MozMover / Firefox Thunderbird Profile Backup (c) 2025 R.Trunk",
        "profiles_label": "Erkannte Profile (eines oder mehrere auswählen):",
        "refresh_btn": "Liste aktualisieren",
        "backup_btn": "Ausgewählte sichern",
        "restore_btn": "Aus ZIP wiederherstellen",
        "nothing_selected": "Nichts ausgewählt",
        "select_profile": "Bitte wählen Sie mindestens ein Profil aus.",
        "operation_in_progress": "Vorgang läuft bereits",
        "another_operation": "Ein anderer Vorgang ist bereits im Gange.",
        "done": "Fertig",
        "operation_finished": "Vorgang erfolgreich abgeschlossen.",
        "error": "Fehler",
        "warning": "Warnung",
        "could_not_close": "Konnte {app} nicht schließen.",
        "save_backup": "Backup speichern",
        "select_backup": "Backup auswählen",
        "restore_to_firefox": "Wiederherstellen zu Firefox?",
        "restore_to_firefox_text": "Wiederherstellen zu Firefox?\nKlicken Sie 'Nein' für Thunderbird.",
        "default_profile": " ★ STANDARD",
        "default_initial": "Dies ist das Standardprofil, das wahrscheinlich verwendet wird",
        "dark_mode": "Dunkler Modus",
        "language": "Sprache",
        "working": "Sicherung läuft …",
        "cancel": "Abbrechen",
        "copying": "Kopiere in Profilordner …",
        "backing_up": "Sichere {count} Dateien …",
        "no_profile_folder": "Kein Profilordner in ZIP gefunden.",
        "profiles_ini_missing": "profiles.ini nicht gefunden.",
        "restoring": "Wiederherstellung läuft …"
    },
    "en": {
        "title": "MozMover - Firefox / Thunderbird Profile Backup & Restore (c) 2025 R.Trunk",
        "profiles_label": "Detected profiles (select one or many):",
        "refresh_btn": "Refresh list",
        "backup_btn": "Backup selected",
        "restore_btn": "Restore from zip",
        "nothing_selected": "Nothing selected",
        "select_profile": "Select at least one profile.",
        "operation_in_progress": "Operation in progress",
        "another_operation": "Another operation is already in progress.",
        "done": "Done",
        "operation_finished": "Operation finished successfully.",
        "error": "Error",
        "warning": "Warning",
        "could_not_close": "Could not close {app}.",
        "save_backup": "Save backup zip",
        "select_backup": "Select backup zip",
        "restore_to_firefox": "Restore to Firefox?",
        "restore_to_firefox_text": "Restore to Firefox?\nClick 'No' for Thunderbird.",
        "default_profile": " ★ DEFAULT",
        "default_initial": "This is the default profile that is likely in use",
        "dark_mode": "Dark mode",
        "language": "Language",
        "working": "Backup in progress …",
        "cancel": "Cancel",
        "copying": "Copying into profile folder …",
        "backing_up": "Backing up {count} files …",
        "no_profile_folder": "No profile folder found in zip.",
        "profiles_ini_missing": "profiles.ini not found.",
        "restoring": "Restore in progress …"
    }
}

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #
def find_profiles(app: str):
    base = OS_MAP[sys.platform][app]
    ini = base / "profiles.ini"
    if not ini.exists():
        return []

    sections = []
    cur = {}
    with ini.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                if cur:
                    sections.append(cur)
                cur = {"__section__": line[1:-1]}
            elif "=" in line:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
        if cur:
            sections.append(cur)

    install_default = None
    for sec in sections:
        if sec["__section__"].startswith("Install"):
            install_default = sec.get("Default")

    results = []
    for sec in sections:
        if sec["__section__"].startswith("Profile"):
            path = sec.get("Path")
            if not path:
                continue
            is_abs = Path(path).is_absolute()
            full_path = Path(path) if is_abs else base / path
            if full_path.is_dir():
                is_default = (sec.get("Default") == "1" and install_default is None) or \
                             (install_default and full_path.name == install_default)
                results.append((full_path, bool(is_default)))
    return results

def kill_process(name: str, timeout: int = 5):
    name = name.lower()
    procs = [p for p in psutil.process_iter(["pid", "name"]) if name in p.info["name"].lower()]
    if not procs:
        return True
    for p in procs:
        try:
            p.terminate()
        except psutil.AccessDenied:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.AccessDenied:
            pass
    return not bool(alive)

# --------------------------------------------------------------------------- #
#  Worker threads                                                             #
# --------------------------------------------------------------------------- #
class BackupThread(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, profiles, zip_path, translator):
        super().__init__()
        self.profiles = profiles
        self.zip_path = Path(zip_path)
        self.translator = translator

    def run(self):
        try:
            total_files = sum(len(list(p.rglob("*"))) for p in self.profiles)
            msg = self.translator["backing_up"].format(count=total_files)
            self.log.emit(msg)
            done = 0
            with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for prof in self.profiles:
                    for file in prof.rglob("*"):
                        if file.is_file():
                            zf.write(file, arcname=file.relative_to(prof.parent))
                            done += 1
                            if done % 50 == 0:
                                self.progress.emit(int(done * 100 / total_files))
            self.progress.emit(100)
            self.finished_ok.emit()
        except Exception as e:
            self.error.emit(str(e))

class RestoreThread(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, zip_path, target_folder, translator):
        super().__init__()
        self.zip_path = Path(zip_path)
        self.target = Path(target_folder)
        self.translator = translator

    def run(self):
        try:
            if self.target.exists():
                shutil.rmtree(self.target)
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(self.zip_path) as zf:
                    zf.extractall(tmp)
                extracted = Path(tmp)
                prof_dir = next((p for p in extracted.iterdir() if p.is_dir()), None)
                if not prof_dir:
                    raise Exception(self.translator["no_profile_folder"])
                self.log.emit(self.translator["copying"])
                shutil.copytree(prof_dir, self.target)
            self.finished_ok.emit()
        except Exception as e:
            self.error.emit(str(e))

# --------------------------------------------------------------------------- #
#  Main window                                                                #
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("MozMover", "MozMover")
        self.language = self.settings.value("language", "en")
        self.translator = TRANSLATIONS[self.language]
        self.is_dark_mode = self.settings.value("dark_mode", False, type=bool)
        
        self.setup_ui()
        self.setup_toolbar()
        self.apply_theme()
        self.populate_profiles()

    def setup_ui(self):
        self.setWindowTitle(self.translator["title"])
        self.resize(750, 550)
        self.current_thread = None

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)

        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(QLabel(self.translator["profiles_label"]))
        lay.addWidget(self.profile_list)

        btn_bar = QHBoxLayout()
        self.refresh_btn = QPushButton(self.translator["refresh_btn"])
        self.backup_btn = QPushButton(self.translator["backup_btn"])
        self.restore_btn = QPushButton(self.translator["restore_btn"])
        btn_bar.addWidget(self.refresh_btn)
        btn_bar.addWidget(self.backup_btn)
        btn_bar.addWidget(self.restore_btn)
        lay.addLayout(btn_bar)

        self.refresh_btn.clicked.connect(self.populate_profiles)
        self.backup_btn.clicked.connect(self.do_backup)
        self.restore_btn.clicked.connect(self.do_restore)

    def setup_toolbar(self):
        toolbar = QToolBar("Settings")
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        
        # Language selector
        lang_label = QLabel(self.translator["language"] + ": ")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Deutsch", "English"])
        self.lang_combo.setCurrentText("Deutsch" if self.language == "de" else "English")
        self.lang_combo.currentTextChanged.connect(self.change_language)
        
        # Dark mode toggle
        self.dark_checkbox = QCheckBox(self.translator["dark_mode"])
        self.dark_checkbox.setChecked(self.is_dark_mode)
        self.dark_checkbox.stateChanged.connect(self.toggle_dark_mode)
        
        toolbar.addWidget(lang_label)
        toolbar.addWidget(self.lang_combo)
        toolbar.addSeparator()
        toolbar.addWidget(self.dark_checkbox)

    def change_language(self, text):
        self.language = "de" if text == "Deutsch" else "en"
        self.settings.setValue("language", self.language)
        self.translator = TRANSLATIONS[self.language]
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(self.translator["title"])
        for i in range(self.profile_list.count()):
            item = self.profile_list.item(i)
            data = item.data(Qt.UserRole)
            if data is not None:  # Korrigiert: Prüfung auf None
                app, path, is_default = data
                flag = self.translator["default_profile"] if is_default else ""
                item.setText(f"{app.upper()}{flag}  –  {path.name}")
                if is_default:
                    item.setToolTip(self.translator["default_initial"])

        # Update button texts
        self.refresh_btn.setText(self.translator["refresh_btn"])
        self.backup_btn.setText(self.translator["backup_btn"])
        self.restore_btn.setText(self.translator["restore_btn"])
        
        # Update labels
        labels = self.findChildren(QLabel)
        if labels:
            labels[0].setText(self.translator["profiles_label"])
        
        # Update toolbar
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItems(["Deutsch", "English"])
        self.lang_combo.setCurrentText("Deutsch" if self.language == "de" else "English")
        self.lang_combo.blockSignals(False)
        
        # Update checkbox
        self.dark_checkbox.setText(self.translator["dark_mode"])

    def toggle_dark_mode(self, state):
        self.is_dark_mode = state == Qt.Checked
        self.settings.setValue("dark_mode", self.is_dark_mode)
        self.apply_theme()

    def apply_theme(self):
        if self.is_dark_mode:
            self.set_dark_theme()
        else:
            self.set_light_theme()

    def set_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        QApplication.setPalette(palette)

    def set_light_theme(self):
        QApplication.setPalette(QPalette())

    def populate_profiles(self):
        self.profile_list.clear()
        all_profiles = []
        for app in ("firefox", "thunderbird"):
            for p, is_default in find_profiles(app):
                all_profiles.append((app, p, is_default))
        all_profiles.sort(key=lambda x: (not x[2], x[0]))

        for app, p, is_default in all_profiles:
            flag = self.translator["default_profile"] if is_default else ""
            item_text = f"{app.upper()}{flag}  –  {p.name}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, (app, p, is_default))
            if is_default:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QColor(60, 60, 80))
                item.setForeground(QColor(200, 200, 255))
                item.setToolTip(self.translator["default_initial"])
            self.profile_list.addItem(item)

    def do_backup(self):
        items = self.profile_list.selectedItems()
        if not items:
            QMessageBox.warning(self, self.translator["nothing_selected"], self.translator["select_profile"])
            return

        profiles = [item.data(Qt.UserRole)[1] for item in items]
        apps = {item.data(Qt.UserRole)[0] for item in items}

        for app in apps:
            if not kill_process(app):
                QMessageBox.warning(self, self.translator["warning"], 
                                  self.translator["could_not_close"].format(app=app))
                return

        zip_path, _ = QFileDialog.getSaveFileName(
            self, self.translator["save_backup"], 
            str(Path.home() / f"MozMover_{datetime.date.today()}.zip"),
            "ZIP files (*.zip)")
        if not zip_path:
            return

        self._run_thread(BackupThread(profiles, zip_path, self.translator), is_backup=True)

    def do_restore(self):
        zip_path, _ = QFileDialog.getOpenFileName(
            self, self.translator["select_backup"], str(Path.home()), "ZIP files (*.zip)")
        if not zip_path:
            return

        reply = QMessageBox.question(self, self.translator["restore_to_firefox"],
                                    self.translator["restore_to_firefox_text"],
                                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if reply == QMessageBox.Cancel:
            return

        app = "firefox" if reply == QMessageBox.Yes else "thunderbird"
        base = OS_MAP[sys.platform][app]
        prof_dirs = find_profiles(app)
        target = prof_dirs[0][0].parent if prof_dirs else base
        new_name = Path(zip_path).stem
        target_prof = target / new_name

        if not kill_process(app):
            QMessageBox.warning(self, self.translator["warning"],
                              self.translator["could_not_close"].format(app=app))
            return

        self._run_thread(RestoreThread(zip_path, target_prof, self.translator), is_backup=False)

    def _run_thread(self, thread: QThread, is_backup: bool = True):
        if self.current_thread and self.current_thread.isRunning():
            QMessageBox.warning(self, self.translator["operation_in_progress"], 
                              self.translator["another_operation"])
            return

        self.current_thread = thread
        
        # Set correct window title based on language
        window_title = "Sicherung" if self.language == "de" else "Backup"
        # Erstelle den ProgressDialog sofort sichtbar
        title = self.translator["working"] if is_backup else self.translator["restoring"]
        self.progress = QProgressDialog(title, self.translator["cancel"], 0, 100, self)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)  # Zeige sofort an
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        
        # Set window title
        self.progress.setWindowTitle(window_title)
        
        # Zeige das Fenster sofort
        self.progress.show()
        QApplication.processEvents()  # Force UI update
        
        self.progress.canceled.connect(thread.terminate)
        thread.progress.connect(self.progress.setValue)
        thread.log.connect(lambda t: self.progress.setLabelText(t))
        thread.finished_ok.connect(lambda: (
            self.progress.close(),
            QMessageBox.information(self, self.translator["done"], self.translator["operation_finished"])
        ))
        thread.error.connect(lambda e: (
            self.progress.close(),
            QMessageBox.critical(self, self.translator["error"], e)
        ))
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _on_thread_finished(self):
        self.current_thread = None

# --------------------------------------------------------------------------- #
#  Main                                                                       #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())