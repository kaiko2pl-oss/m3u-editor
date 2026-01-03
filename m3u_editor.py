import sys
import os
import csv
import re
import subprocess
import urllib.request
import urllib.error
import copy
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
    QFileDialog, QMessageBox, QHeaderView, QSplitter, QGroupBox, QFormLayout,
    QInputDialog, QAbstractItemView, QProgressBar, QGraphicsOpacityEffect,
    QMenu, QComboBox, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QUrl, QPropertyAnimation, 
                          QEasingCurve, QAbstractAnimation, QSettings)
from PyQt6.QtGui import QColor, QPalette, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# -----------------------------------------------------------------------------
# Data Model
# -----------------------------------------------------------------------------

@dataclass
class M3UEntry:
    """Represents a single channel/stream in the playlist."""
    name: str
    url: str
    group: str = ""
    logo: str = ""
    duration: str = "-1"
    user_agent: str = ""
    raw_extinf: str = ""  # Keep original attributes to preserve unedited data

    def to_m3u_string(self) -> str:
        """Reconstructs the #EXTINF line and URL line."""
        # We rebuild the EXTINF line based on current properties
        # Basic format: #EXTINF:-1 group-title="Group" tvg-logo="Logo",Name
        
        attributes = []
        if self.group:
            attributes.append(f'group-title="{self.group}"')
        if self.logo:
            attributes.append(f'tvg-logo="{self.logo}"')
        
        # You can add more specific tvg- tags here if needed
        attr_str = " ".join(attributes)
        
        # If we have attributes, prepend a space
        if attr_str:
            attr_str = " " + attr_str
            
        lines = [f'#EXTINF:{self.duration}{attr_str},{self.name}']
        if self.user_agent:
            lines.append(f'#EXTVLCOPT:http-user-agent={self.user_agent}')
        lines.append(self.url)
        return "\n".join(lines)

# -----------------------------------------------------------------------------
# Logic / Controller
# -----------------------------------------------------------------------------

class M3UParser:
    """Handles reading and writing M3U files."""
    
    @staticmethod
    def parse_lines(lines: List[str]) -> List[M3UEntry]:
        entries = []
        current_entry = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith("#EXTM3U"):
                continue
            
            if line.startswith("#EXTINF:"):
                # Parse metadata
                # Example: #EXTINF:-1 tvg-id="" group-title="News",Channel Name
                current_entry = M3UEntry(name="", url="")
                
                # Split duration/info from the rest
                # Regex to capture duration, attributes, and name
                # This is a simplified regex for standard M3U8 structures
                meta_match = re.match(r'^#EXTINF:([-0-9]+)(.*),(.*)$', line)
                
                if meta_match:
                    current_entry.duration = meta_match.group(1)
                    attrs = meta_match.group(2)
                    current_entry.name = meta_match.group(3).strip()
                    
                    # Extract Group
                    grp_match = re.search(r'group-title="([^"]*)"', attrs)
                    if grp_match:
                        current_entry.group = grp_match.group(1)
                        
                    # Extract Logo
                    logo_match = re.search(r'tvg-logo="([^"]*)"', attrs)
                    if logo_match:
                        current_entry.logo = logo_match.group(1)
                else:
                    # Fallback if regex fails
                    current_entry.name = line.split(',')[-1]

            elif line.startswith("#EXTVLCOPT:"):
                if current_entry:
                    opt = line[11:].strip()
                    if opt.lower().startswith("http-user-agent="):
                        current_entry.user_agent = opt.split('=', 1)[1]

            elif not line.startswith("#"):
                # This is likely the URL
                if current_entry:
                    current_entry.url = line
                    entries.append(current_entry)
                    current_entry = None
                else:
                    # URL without EXTINF (rare but possible)
                    entries.append(M3UEntry(name="Unknown", url=line))
        return entries

    @staticmethod
    def parse_file(filepath: str) -> List[M3UEntry]:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            return M3UParser.parse_lines(lines)
        except Exception as e:
            print(f"Error parsing file: {e}")
            raise e

    @staticmethod
    def save_file(filepath: str, entries: List[M3UEntry]):
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for entry in entries:
                    f.write(entry.to_m3u_string() + "\n")
        except Exception as e:
            raise e

class StreamValidator(QThread):
    """Worker thread to check stream URLs."""
    progress = pyqtSignal(int, int)  # current, total
    result = pyqtSignal(int, bool, str)   # row_index, is_valid, message

    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks # List of (row_index, url, user_agent)
        self.is_running = True

    def run(self):
        total = len(self.tasks)
        for i, (row, url, ua) in enumerate(self.tasks):
            if not self.is_running:
                break
            is_valid, msg = self.check_url(url, ua)
            self.result.emit(row, is_valid, msg)
            self.progress.emit(i + 1, total)

    def check_url(self, url, user_agent=None):
        headers = {
            'User-Agent': user_agent if user_agent else 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        try:
            req = urllib.request.Request(url, headers=headers, method='HEAD')
            # 5-second timeout
            with urllib.request.urlopen(req, timeout=5) as response:
                if 200 <= response.status < 400:
                    return True, f"OK ({response.status})"
                return False, f"Status: {response.status}"
        except urllib.error.HTTPError as e:
            if e.code == 405: # Method Not Allowed, try GET
                try:
                    req = urllib.request.Request(url, headers=headers, method='GET')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        if 200 <= response.status < 400:
                            return True, f"OK ({response.status})"
                except:
                    pass
            return False, f"HTTP {e.code}"
        except Exception as e:
            return False, f"Error: {str(e)}"

    def stop(self):
        self.is_running = False

class PlaylistTable(QTableWidget):
    """Custom TableWidget to handle Drag and Drop reordering."""
    orderChanged = pyqtSignal()
    aboutToChangeOrder = pyqtSignal()

    def dropEvent(self, event):
        if event.source() == self:
            self.aboutToChangeOrder.emit()
            super().dropEvent(event)
            self.orderChanged.emit()

class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_path=""):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 100)
        self.vlc_path = current_path
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.path_edit = QLineEdit(self.vlc_path)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.browse_path)
        
        row_layout = QHBoxLayout()
        row_layout.addWidget(self.path_edit)
        row_layout.addWidget(btn_browse)
        
        form.addRow("VLC Path:", row_layout)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def browse_path(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select VLC Executable")
        if path:
            self.path_edit.setText(path)
            
    def get_path(self):
        return self.path_edit.text()

# -----------------------------------------------------------------------------
# GUI Implementation
# -----------------------------------------------------------------------------

DARK_STYLESHEET = """
/* Main Window & General */
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; font-size: 10pt; }

/* Buttons */
QPushButton {
    background-color: #313244;
    border: 2px solid #45475a;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover { background-color: #45475a; border-color: #89b4fa; color: #89b4fa; }
QPushButton:pressed { background-color: #585b70; }
QPushButton:disabled { background-color: #1e1e2e; border-color: #313244; color: #6c7086; }

/* Inputs */
QLineEdit {
    background-color: #181825;
    border: 2px solid #313244;
    border-radius: 6px;
    padding: 6px;
    color: #cdd6f4;
}
QLineEdit:focus { border-color: #89b4fa; }

/* Table */
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    border: 1px solid #313244;
    gridline-color: #313244;
    selection-background-color: #313244;
    selection-color: #89b4fa;
}
QHeaderView::section {
    background-color: #1e1e2e;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #89b4fa;
    font-weight: bold;
}

/* Group Box */
QGroupBox {
    border: 2px solid #313244;
    border-radius: 8px;
    margin-top: 1.5em;
    font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #89b4fa; }

/* Splitter */
QSplitter::handle { background-color: #313244; }

/* Scrollbar */
QScrollBar:vertical { border: none; background: #181825; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #45475a; min-height: 20px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""

class M3UEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Open Source M3U Editor")
        self.resize(1000, 700)
        
        self.entries: List[M3UEntry] = []
        self.current_file_path: Optional[str] = None
        self.validator_thread: Optional[StreamValidator] = None
        self.undo_stack: List[List[M3UEntry]] = []
        self.redo_stack: List[List[M3UEntry]] = []
        self.editing_started = False
        self.is_dark_mode = True # Default to dark mode for "fancy" look
        self.settings = QSettings("OpenSource", "M3UEditor")
        
        # Media Player Setup
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        # Apply initial theme
        self.toggle_theme(initial=True)
        self.init_ui()

    def init_ui(self):
        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Toolbar Area ---
        toolbar_layout = QHBoxLayout()
        
        btn_load = QPushButton("Load M3U")
        btn_load.clicked.connect(self.load_m3u)
        
        btn_load_url = QPushButton("Load URL")
        btn_load_url.clicked.connect(self.load_m3u_from_url)
        
        btn_save = QPushButton("Save M3U")
        btn_save.clicked.connect(self.save_m3u)
        
        btn_add = QPushButton("Add Stream")
        btn_add.clicked.connect(self.add_entry)
        
        btn_delete = QPushButton("Delete Selected")
        btn_delete.clicked.connect(self.delete_entry)
        
        self.btn_validate = QPushButton("Check Stream Health")
        self.btn_validate.clicked.connect(self.validate_streams)
        
        btn_duplicates = QPushButton("Find Duplicates")
        btn_duplicates.clicked.connect(self.find_duplicates)
        
        btn_remove_invalid = QPushButton("Remove Invalid")
        btn_remove_invalid.clicked.connect(self.remove_invalid_streams)
        
        btn_theme = QPushButton("Toggle Dark Mode")
        btn_theme.clicked.connect(lambda: self.toggle_theme(initial=False))
        
        btn_undo = QPushButton("Undo")
        btn_undo.setShortcut("Ctrl+Z")
        btn_undo.clicked.connect(self.undo)
        
        btn_redo = QPushButton("Redo")
        btn_redo.setShortcut("Ctrl+Y")
        btn_redo.clicked.connect(self.redo)
        
        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self.export_csv)
        
        self.group_combo = QComboBox()
        self.group_combo.setFixedWidth(150)
        self.group_combo.addItem("All Groups")
        self.group_combo.currentTextChanged.connect(self.filter_table)
        
        btn_settings = QPushButton("Settings")
        btn_settings.clicked.connect(self.open_settings)
        
        toolbar_layout.addWidget(btn_load)
        toolbar_layout.addWidget(btn_load_url)
        toolbar_layout.addWidget(btn_save)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(btn_add)
        toolbar_layout.addWidget(btn_delete)
        toolbar_layout.addWidget(self.btn_validate)
        toolbar_layout.addWidget(btn_remove_invalid)
        toolbar_layout.addWidget(btn_duplicates)
        toolbar_layout.addWidget(btn_theme)
        toolbar_layout.addWidget(btn_undo)
        toolbar_layout.addWidget(btn_redo)
        toolbar_layout.addWidget(btn_export)
        toolbar_layout.addWidget(self.group_combo)
        toolbar_layout.addWidget(btn_settings)
        
        # Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search channels...")
        self.search_bar.setFixedWidth(200)
        self.search_bar.textChanged.connect(self.filter_table)
        toolbar_layout.addWidget(self.search_bar)
        
        main_layout.addLayout(toolbar_layout)

        # --- Splitter for Table and Editor ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 1. Left Side: Table
        self.table = PlaylistTable()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Group", "Name", "URL"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.orderChanged.connect(self.sync_entries_from_table)
        self.table.aboutToChangeOrder.connect(self.save_undo_state)
        
        splitter.addWidget(self.table)
        
        # 2. Right Side: Editor & Controls
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Editor Group
        editor_group = QGroupBox("Edit Channel Details")
        form_layout = QFormLayout()
        
        self.input_name = QLineEdit()
        self.input_name.textChanged.connect(self.update_current_entry_data)
        
        self.input_group = QLineEdit()
        self.input_group.textChanged.connect(self.update_current_entry_data)
        
        self.input_logo = QLineEdit()
        self.input_logo.textChanged.connect(self.update_current_entry_data)
        
        self.input_url = QLineEdit()
        self.input_url.textChanged.connect(self.update_current_entry_data)
        
        self.input_user_agent = QLineEdit()
        self.input_user_agent.textChanged.connect(self.update_current_entry_data)
        
        form_layout.addRow("Name:", self.input_name)
        form_layout.addRow("Group:", self.input_group)
        form_layout.addRow("Logo URL:", self.input_logo)
        form_layout.addRow("Stream URL:", self.input_url)
        form_layout.addRow("User Agent:", self.input_user_agent)
        
        editor_group.setLayout(form_layout)
        right_layout.addWidget(editor_group)
        
        # Preview Group
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout()
        
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.player.setVideoOutput(self.video_widget)
        
        btn_preview_layout = QHBoxLayout()
        btn_play = QPushButton("Play")
        btn_play.clicked.connect(self.play_stream)
        btn_stop = QPushButton("Stop")
        btn_stop.clicked.connect(self.stop_stream)
        
        btn_preview_layout.addWidget(btn_play)
        btn_preview_layout.addWidget(btn_stop)
        
        preview_layout.addWidget(self.video_widget)
        preview_layout.addLayout(btn_preview_layout)
        preview_group.setLayout(preview_layout)
        right_layout.addWidget(preview_group)
        
        # Organization Controls
        org_group = QGroupBox("Organize")
        org_layout = QVBoxLayout()
        
        btn_up = QPushButton("Move Up")
        btn_up.clicked.connect(self.move_up)
        
        btn_down = QPushButton("Move Down")
        btn_down.clicked.connect(self.move_down)
        
        btn_bulk_group = QPushButton("Bulk Edit Group")
        btn_bulk_group.clicked.connect(self.bulk_edit_group)
        
        org_layout.addWidget(btn_up)
        org_layout.addWidget(btn_down)
        org_layout.addWidget(btn_bulk_group)
        org_group.setLayout(org_layout)
        
        right_layout.addWidget(org_group)
        right_layout.addStretch()
        
        splitter.addWidget(right_panel)
        
        # Set initial sizes for splitter (70% table, 30% editor)
        splitter.setSizes([700, 300])
        
        main_layout.addWidget(splitter)
        
        # Status Bar
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(200)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().addWidget(self.status_label)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def save_undo_state(self):
        """Saves the current state of entries to the undo stack."""
        if len(self.undo_stack) > 50: # Limit stack depth
            self.undo_stack.pop(0)
        self.undo_stack.append(copy.deepcopy(self.entries))
        self.redo_stack.clear() # Clear redo history when a new action occurs
        self.status_label.setText("State saved.")

    def undo(self):
        if not self.undo_stack:
            self.status_label.setText("Nothing to undo.")
            return
        
        # Save current state to redo stack before reverting
        self.redo_stack.append(copy.deepcopy(self.entries))
        
        self.entries = self.undo_stack.pop()
        self.refresh_table()
        self.clear_editor()
        self.status_label.setText("Undone last action.")

    def redo(self):
        if not self.redo_stack:
            self.status_label.setText("Nothing to redo.")
            return

        self.undo_stack.append(copy.deepcopy(self.entries))
        self.entries = self.redo_stack.pop()
        self.refresh_table()
        self.clear_editor()
        self.status_label.setText("Redone last action.")

    def load_m3u(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open M3U File", "", "M3U Files (*.m3u *.m3u8);;All Files (*)")
        if file_name:
            try:
                self.undo_stack.clear()
                self.redo_stack.clear()
                self.entries = M3UParser.parse_file(file_name)
                self.current_file_path = file_name
                self.setWindowTitle(f"Open Source M3U Editor - {os.path.basename(file_name)}")
                self.refresh_table()
                self.update_group_combo()
                self.status_label.setText(f"Loaded {len(self.entries)} channels from {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not load file: {str(e)}")

    def load_m3u_from_url(self):
        url, ok = QInputDialog.getText(self, "Load M3U from URL", "Enter Playlist URL:")
        if ok and url:
            try:
                with urllib.request.urlopen(url) as response:
                    content = response.read().decode('utf-8', errors='ignore')
                    lines = content.splitlines()
                
                self.undo_stack.clear()
                self.redo_stack.clear()
                self.entries = M3UParser.parse_lines(lines)
                self.current_file_path = None # No local file path
                self.setWindowTitle(f"Open Source M3U Editor - URL Stream")
                self.refresh_table()
                self.update_group_combo()
                self.status_label.setText(f"Loaded {len(self.entries)} channels from URL")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not load URL: {str(e)}")

    def save_m3u(self):
        if not self.entries:
            QMessageBox.warning(self, "Warning", "No entries to save.")
            return

        file_name, _ = QFileDialog.getSaveFileName(self, "Save M3U File", self.current_file_path or "playlist.m3u", "M3U Files (*.m3u *.m3u8)")
        if file_name:
            try:
                self.sync_entries_from_table() # Ensure saved order matches visual sort order
                M3UParser.save_file(file_name, self.entries)
                self.current_file_path = file_name
                self.setWindowTitle(f"Open Source M3U Editor - {os.path.basename(file_name)}")
                self.status_label.setText(f"Saved to {file_name}")
                QMessageBox.information(self, "Success", "File saved successfully!")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file: {str(e)}")

    def export_csv(self):
        if not self.entries:
            QMessageBox.warning(self, "Warning", "No entries to export.")
            return

        file_name, _ = QFileDialog.getSaveFileName(self, "Export CSV", "playlist.csv", "CSV Files (*.csv)")
        if file_name:
            try:
                with open(file_name, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Group", "Name", "URL", "Logo", "Duration", "User-Agent"])
                    for entry in self.entries:
                        writer.writerow([entry.group, entry.name, entry.url, entry.logo, entry.duration, entry.user_agent])
                self.status_label.setText(f"Exported to {file_name}")
                QMessageBox.information(self, "Success", "Export successful!")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not export: {str(e)}")

    def refresh_table(self):
        """Reloads the table widget from the self.entries list."""
        self.table.blockSignals(True) # Prevent selection triggers during reload
        self.table.setSortingEnabled(False) # Disable sorting while populating
        self.table.setRowCount(0)
        
        for row, entry in enumerate(self.entries):
            self.table.insertRow(row)
            
            item_group = QTableWidgetItem(entry.group)
            item_group.setData(Qt.ItemDataRole.UserRole, entry)  # Store reference to entry
            self.table.setItem(row, 0, item_group)
            self.table.setItem(row, 1, QTableWidgetItem(entry.name))
            self.table.setItem(row, 2, QTableWidgetItem(entry.url))
            
        self.table.setSortingEnabled(True)
        self.animate_table_refresh()
        self.table.blockSignals(False)

    def on_selection_changed(self):
        """Populates the editor panel when a row is selected."""
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            # Retrieve entry from the item data to support sorting
            entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.editing_started = False
            
            # Block signals to prevent 'textChanged' from triggering updates while we populate
            self.input_name.blockSignals(True)
            self.input_group.blockSignals(True)
            self.input_logo.blockSignals(True)
            self.input_url.blockSignals(True)
            self.input_user_agent.blockSignals(True)
            
            self.input_name.setText(entry.name)
            self.input_group.setText(entry.group)
            self.input_logo.setText(entry.logo)
            self.input_url.setText(entry.url)
            self.input_user_agent.setText(entry.user_agent)
            
            self.input_name.blockSignals(False)
            self.input_group.blockSignals(False)
            self.input_logo.blockSignals(False)
            self.input_url.blockSignals(False)
            self.input_user_agent.blockSignals(False)
        else:
            self.clear_editor()

    def clear_editor(self):
        self.input_name.clear()
        self.input_group.clear()
        self.input_logo.clear()
        self.input_url.clear()
        self.input_user_agent.clear()

    def update_current_entry_data(self):
        """Updates the data model when the user types in the editor fields."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
            
        row = selected_rows[0].row()
        entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        if not self.editing_started:
            self.save_undo_state()
            self.editing_started = True
        
        entry.name = self.input_name.text()
        entry.group = self.input_group.text()
        entry.logo = self.input_logo.text()
        entry.url = self.input_url.text()
        entry.user_agent = self.input_user_agent.text()
        
        # Update table display immediately (optional, but looks nice)
        self.table.item(row, 0).setText(entry.group)
        self.table.item(row, 1).setText(entry.name)
        self.table.item(row, 2).setText(entry.url)

    def add_entry(self):
        self.save_undo_state()
        new_entry = M3UEntry(name="New Channel", url="http://", group="Uncategorized")
        self.entries.append(new_entry)
        self.refresh_table()
        # Select the new item
        self.table.selectRow(len(self.entries) - 1)

    def delete_entry(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
            
        row = selected_rows[0].row()
        confirm = QMessageBox.question(self, "Confirm Delete", "Are you sure you want to delete this channel?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if confirm == QMessageBox.StandardButton.Yes:
            self.save_undo_state()
            self.table.removeRow(row)
            self.sync_entries_from_table()
            self.clear_editor()

    def move_up(self):
        self.save_undo_state()
        self.sync_entries_from_table() # Sync first to ensure list matches visual order
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        if row > 0:
            # Swap in list
            self.entries[row], self.entries[row-1] = self.entries[row-1], self.entries[row]
            # Refresh and keep selection
            self.refresh_table()
            self.table.selectRow(row - 1)

    def move_down(self):
        self.save_undo_state()
        self.sync_entries_from_table()
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        if row < len(self.entries) - 1:
            # Swap in list
            self.entries[row], self.entries[row+1] = self.entries[row+1], self.entries[row]
            # Refresh and keep selection
            self.refresh_table()
            self.table.selectRow(row + 1)

    def update_group_combo(self):
        current = self.group_combo.currentText()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("All Groups")
        
        groups = sorted(list(set(entry.group for entry in self.entries if entry.group)))
        self.group_combo.addItems(groups)
        
        if current in groups:
            self.group_combo.setCurrentText(current)
        self.group_combo.blockSignals(False)

    def filter_table(self):
        text = self.search_bar.text().lower()
        group_filter = self.group_combo.currentText()
        
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1) # Name column
            item_group = self.table.item(row, 0)
            
            if item and item_group:
                name_match = text in item.text().lower()
                group_match = (group_filter == "All Groups" or item_group.text() == group_filter)
                self.table.setRowHidden(row, not (name_match and group_match))
            else:
                self.table.setRowHidden(row, True)

    def bulk_edit_group(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Warning", "No channels selected.")
            return
            
        new_group, ok = QInputDialog.getText(self, "Bulk Edit Group", "Enter new group name:")
        if ok:
            self.save_undo_state()
            for index in selected_rows:
                row = index.row()
                entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                entry.group = new_group
                self.table.item(row, 0).setText(new_group)
            
            # Update editor if the primary selection was updated
            if selected_rows and selected_rows[0].row() == self.table.currentRow():
                self.input_group.setText(new_group)

    def sync_entries_from_table(self):
        """Rebuilds self.entries based on the current visual order of the table."""
        new_entries = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                entry = item.data(Qt.ItemDataRole.UserRole)
                if entry:
                    new_entries.append(entry)
        self.entries = new_entries

    def validate_streams(self):
        if self.validator_thread and self.validator_thread.isRunning():
            self.validator_thread.stop()
            self.validator_thread.wait()
            self.btn_validate.setText("Check Stream Health")
            self.status_label.setText("Validation stopped.")
            self.table.setSortingEnabled(True)
            return

        # Determine rows to validate
        selected_rows = self.table.selectionModel().selectedRows()
        rows_to_check = []
        
        if selected_rows:
            for index in selected_rows:
                row = index.row()
                entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                rows_to_check.append((row, entry.url, entry.user_agent))
        else:
            # Check all visible rows
            for row in range(self.table.rowCount()):
                if not self.table.isRowHidden(row):
                    entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                    rows_to_check.append((row, entry.url, entry.user_agent))

        if not rows_to_check:
            return

        self.btn_validate.setText("Stop Validation")
        self.table.setSortingEnabled(False) # Lock sorting to preserve row indices
        self.progress_bar.setVisible(True)
        self.status_label.setText(f"Validating {len(rows_to_check)} streams...")
        
        # Reset colors for these rows
        for row, _ in rows_to_check:
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(Qt.GlobalColor.transparent)
                    item.setToolTip("")

        self.validator_thread = StreamValidator(rows_to_check)
        self.validator_thread.progress.connect(self.on_validation_progress)
        self.validator_thread.result.connect(self.on_validation_result)
        self.validator_thread.finished.connect(self.on_validation_finished)
        self.validator_thread.start()

    def on_validation_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_validation_result(self, row_index, is_valid, message):
        if self.is_dark_mode:
            color = QColor("#1b5e20") if is_valid else QColor("#b71c1c") # Darker Green/Red
        else:
            color = QColor("#c8e6c9") if is_valid else QColor("#ffcdd2") # Light Green/Red
            
        for col in range(3):
            item = self.table.item(row_index, col)
            if item:
                item.setBackground(color)
                item.setToolTip(message)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole + 1, is_valid) # Store validity status

    def on_validation_finished(self):
        self.btn_validate.setText("Check Stream Health")
        self.table.setSortingEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Validation complete.")

    def find_duplicates(self):
        seen_urls = set()
        duplicate_rows = []
        
        # Reset highlights first
        for row in range(self.table.rowCount()):
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(Qt.GlobalColor.transparent)

        for i, entry in enumerate(self.entries):
            if entry.url in seen_urls:
                duplicate_rows.append(i)
            else:
                seen_urls.add(entry.url)

        if not duplicate_rows:
            QMessageBox.information(self, "Duplicates", "No duplicate URLs found.")
            return

        self.table.clearSelection()
        for row in duplicate_rows:
            # Highlight visually
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(QColor("#fff9c4")) # Light yellow
            
            # Select the row
            self.table.selectRow(row)

        reply = QMessageBox.question(
            self, 
            "Duplicates Found", 
            f"Found {len(duplicate_rows)} duplicates.\nDo you want to delete them now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_entry()

    def play_stream(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            entry = self.entries[selected_rows[0].row()]
            self.player.setSource(QUrl(entry.url))
            self.player.play()

    def stop_stream(self):
        self.player.stop()

    def remove_invalid_streams(self):
        rows_to_remove = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            # Check UserRole + 1 (Validity)
            is_valid = item.data(Qt.ItemDataRole.UserRole + 1)
            if is_valid is False: # Explicitly False (failed validation)
                rows_to_remove.append(row)
        
        if not rows_to_remove:
            QMessageBox.information(self, "Info", "No invalid streams found (run validation first).")
            return

        confirm = QMessageBox.question(self, "Confirm", f"Remove {len(rows_to_remove)} invalid streams?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.save_undo_state()
            # Remove in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.table.removeRow(row)
            self.sync_entries_from_table()
            QMessageBox.information(self, "Success", "Invalid streams removed.")

    def toggle_theme(self, initial=False):
        app = QApplication.instance()
        
        if not initial:
            self.is_dark_mode = not self.is_dark_mode
            
        if self.is_dark_mode:
            app.setStyleSheet(DARK_STYLESHEET)
        else:
            app.setStyleSheet("") # Revert to default Fusion/System style
            app.setStyle("Fusion")

    def animate_table_refresh(self):
        """Fade animation for the table."""
        effect = QGraphicsOpacityEffect(self.table)
        self.table.setGraphicsEffect(effect)
        
        self.anim = QPropertyAnimation(effect, b"opacity")
        self.anim.setDuration(500)
        self.anim.setStartValue(0)
        self.anim.setEndValue(1)
        self.anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def show_context_menu(self, position):
        menu = QMenu()
        play_vlc_action = QAction("Open in VLC", self)
        play_vlc_action.triggered.connect(self.open_in_vlc)
        menu.addAction(play_vlc_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def open_settings(self):
        current_path = self.settings.value("vlc_path", "")
        dlg = SettingsDialog(self, current_path)
        if dlg.exec():
            new_path = dlg.get_path()
            self.settings.setValue("vlc_path", new_path)

    def open_in_vlc(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        entry = self.table.item(selected_rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if not entry:
            return

        # Check settings first
        vlc_cmd = self.settings.value("vlc_path", "")
        
        if not vlc_cmd or not os.path.exists(vlc_cmd):
            vlc_cmd = "vlc"
            if sys.platform == "win32":
                # Check common locations if vlc is not in path
                possible_paths = [
                    os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "VideoLAN", "VLC", "vlc.exe"),
                    os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "VideoLAN", "VLC", "vlc.exe")
                ]
                for p in possible_paths:
                    if os.path.exists(p):
                        vlc_cmd = p
                        break
        
        try:
            if sys.platform == 'darwin':
                 subprocess.Popen(['open', '-a', 'VLC', entry.url])
            else:
                 subprocess.Popen([vlc_cmd, entry.url])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not start VLC: {e}\nEnsure VLC is installed and in your PATH.")

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Optional: Set a dark theme style for a "tech" look
    app.setStyle("Fusion")
    
    window = M3UEditorWindow()
    window.show()
    
    sys.exit(app.exec())
