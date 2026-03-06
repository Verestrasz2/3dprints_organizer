import sys
import os
import json
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QScrollArea, QGridLayout, QVBoxLayout, QLabel, QFrame, QDialog,
    QLineEdit, QPushButton, QFileDialog, QMenu, QProgressBar
)
from PyQt6.QtGui import QAction, QIcon, QCursor, QBrush, QColor
from PyQt6.QtCore import Qt, QTimer
import numpy as np
import trimesh
import vtk
import time

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except Exception:
    from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # type: ignore

# -----------------------------------------------------
# PyInstaller resource_path
# -----------------------------------------------------
def resource_path(relative_path):
    """ Ermittelt Pfade, die sowohl in PyInstaller als auch normal funktionieren """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


CONFIG_FILE = "paths.json"


class ClickableFrame(QFrame):
    clicked = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.clicked:
            self.clicked = False
            if hasattr(self, "on_click"):
                self.on_click()
        super().mouseReleaseEvent(event)


class OrcaLikeInteractorStyle(vtk.vtkInteractorStyleTrackballCamera):
    """Orca-ähnliche Kamerasteuerung:
    - Linke Maustaste: Orbit/Rotate
    - Rechte oder mittlere Maustaste: Pan
    - Mausrad: Zoom (VTK-Standard)
    - Taste R: Kamera zurücksetzen
    """

    def __init__(self, reset_callback=None):
        super().__init__()
        self._drag_mode = None
        self._reset_callback = reset_callback
        self.AddObserver("LeftButtonPressEvent", self._on_left_button_press)
        self.AddObserver("LeftButtonReleaseEvent", self._on_left_button_release)
        self.AddObserver("RightButtonPressEvent", self._on_right_button_press)
        self.AddObserver("RightButtonReleaseEvent", self._on_right_button_release)
        self.AddObserver("MiddleButtonPressEvent", self._on_middle_button_press)
        self.AddObserver("MiddleButtonReleaseEvent", self._on_middle_button_release)
        self.AddObserver("MouseMoveEvent", self._on_mouse_move)
        self.AddObserver("KeyPressEvent", self._on_key_press)

    def _begin_action(self, mode):
        interactor = self.GetInteractor()
        if interactor is None:
            return
        x, y = interactor.GetEventPosition()
        self.FindPokedRenderer(x, y)
        self._drag_mode = mode
        if mode == "rotate":
            self.StartRotate()
        elif mode == "pan":
            self.StartPan()
        elif mode == "dolly":
            self.StartDolly()

    def _end_action(self, expected_mode):
        if self._drag_mode != expected_mode:
            return
        if expected_mode == "rotate":
            self.EndRotate()
        elif expected_mode == "pan":
            self.EndPan()
        elif expected_mode == "dolly":
            self.EndDolly()
        self._drag_mode = None

    def _on_left_button_press(self, obj, evt):
        self._begin_action("rotate")

    def _on_left_button_release(self, obj, evt):
        self._end_action("rotate")

    def _on_right_button_press(self, obj, evt):
        self._begin_action("pan")

    def _on_right_button_release(self, obj, evt):
        self._end_action("pan")

    def _on_middle_button_press(self, obj, evt):
        self._begin_action("pan")

    def _on_middle_button_release(self, obj, evt):
        self._end_action("pan")

    def _on_mouse_move(self, obj, evt):
        if self._drag_mode == "rotate":
            self.Rotate()
            self.GetInteractor().Render()
        elif self._drag_mode == "pan":
            self.Pan()
            self.GetInteractor().Render()
        elif self._drag_mode == "dolly":
            self.Dolly()
            self.GetInteractor().Render()

    def _on_key_press(self, obj, evt):
        interactor = self.GetInteractor()
        if interactor is None:
            return
        key = (interactor.GetKeySym() or "").lower()
        if key == "r" and self._reset_callback:
            self._reset_callback()


class ModelGallery(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D Galerie LeCheim")
        self.setGeometry(100, 100, 1600, 900)

        self.start_dir = r"C:\\"
        self.extra_dirs = []
        self.favorite_files = set()
        self.current_dir = self.start_dir
        self.model_min_width = 300
        self.current_mesh_files = []
        self.gallery_items = []
        self.gallery_empty_label = None
        self.render_queue = []
        self.currently_rendering = False
        self.render_total = 0
        self.render_done = 0

        self.load_paths()

        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # -----------------------------------------------------
        # Rechte Seite (Galerie)
        # -----------------------------------------------------
        right_container = QVBoxLayout()
        right_widget = QWidget()
        right_widget.setLayout(right_container)

        self.loading_status_label = QLabel("")
        self.loading_status_label.setStyleSheet("QLabel { color: #d0d0d0; padding: 2px 0; }")
        self.loading_status_label.setVisible(False)
        right_container.addWidget(self.loading_status_label)

        self.loading_progress = QProgressBar()
        self.loading_progress.setVisible(False)
        right_container.addWidget(self.loading_progress)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollBar:vertical {
                width: 18px;
                background: transparent;
                margin: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #6f6f6f;
                min-height: 30px;
                border-radius: 8px;
            }
            QScrollBar::handle:vertical:hover {
                background: #8a8a8a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.gallery_container = QWidget()
        self.gallery_layout = QGridLayout()
        self.gallery_layout.setSpacing(10)
        self.gallery_container.setLayout(self.gallery_layout)
        self.scroll_area.setWidget(self.gallery_container)
        right_container.addWidget(self.scroll_area)
        main_layout.addWidget(right_widget, 6)

        # -----------------------------------------------------
        # Linke Seite (Explorer)
        # -----------------------------------------------------
        left_container = QVBoxLayout()
        left_widget = QWidget()
        left_widget.setLayout(left_container)

        self.btn_change_start = QPushButton("Startordner ändern")
        self.btn_change_start.clicked.connect(self.change_start_folder)
        left_container.addWidget(self.btn_change_start)

        self.btn_add_path = QPushButton("Pfad hinzufügen")
        self.btn_add_path.clicked.connect(self.add_new_path)
        left_container.addWidget(self.btn_add_path)

        self.btn_remove_path = QPushButton("Pfad entfernen")
        self.btn_remove_path.clicked.connect(self.remove_selected_path)
        left_container.addWidget(self.btn_remove_path)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Suche Ordner / Datei…")
        self.search_field.textChanged.connect(self.filter_tree)
        left_container.addWidget(self.search_field)

        self.breadcrumb = QLabel(f"Pfad: {self.current_dir}")
        self.breadcrumb.setWordWrap(True)
        self.breadcrumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.breadcrumb.mousePressEvent = self.on_breadcrumb_click
        left_container.addWidget(self.breadcrumb)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Ordner / 3D-Dateien")
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        self.tree.itemExpanded.connect(self.on_item_expanded)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        left_container.addWidget(self.tree)
        main_layout.addWidget(left_widget, 2)

        # Icons
        self.folder_icon = QIcon.fromTheme("folder")
        self.file_icon = QIcon.fromTheme("text-x-generic")

        self.populate_all_paths()

        # Resize nur verzögert verarbeiten, sonst wird bei jedem Pixel-Schritt
        # die komplette Galerie neu gelayoutet.
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.relayout_gallery_only)

    # -----------------------------------------------------
    # JSON Laden/Speichern
    # -----------------------------------------------------
    def load_paths(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.start_dir = data.get("start_dir", self.start_dir)
                    self.extra_dirs = data.get("extra_dirs", [])
                    self.favorite_files = set(data.get("favorite_files", []))
            except Exception:
                self.extra_dirs = []
                self.favorite_files = set()
        else:
            self.extra_dirs = []
            self.favorite_files = set()

    def save_paths(self):
        data = {
            "start_dir": self.start_dir,
            "extra_dirs": self.extra_dirs,
            "favorite_files": sorted(self.favorite_files),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # -----------------------------------------------------
    # Baumstruktur
    # -----------------------------------------------------
    def populate_all_paths(self):
        self.tree.clear()
        # Wenn start_dir nicht existiert, fallback auf C:\
        if not os.path.exists(self.start_dir):
            self.start_dir = r"C:\\"
        self.add_tree_root(self.start_dir, is_start=True)
        for path in self.extra_dirs:
            if os.path.exists(path):
                self.add_tree_root(path)

    def add_tree_root(self, path, is_start=False):
        root_item = QTreeWidgetItem([os.path.basename(path) or path])
        root_item.setData(0, Qt.ItemDataRole.UserRole, path)
        root_item.setIcon(0, self.folder_icon)
        root_item.setExpanded(True)
        self.tree.addTopLevelItem(root_item)
        self.add_items(root_item, path)

        if is_start:
            self.current_dir = path
            self.show_gallery(path)
            self.breadcrumb.setText(f"Pfad: {path}")

    def add_items(self, parent_item, path):
        try:
            entries = sorted(os.listdir(path))
            for entry in entries:
                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path):
                    dir_item = QTreeWidgetItem([entry])
                    dir_item.setData(0, Qt.ItemDataRole.UserRole, full_path)
                    dir_item.setIcon(0, self.folder_icon)
                    dir_item.addChild(QTreeWidgetItem(["..."]))
                    parent_item.addChild(dir_item)
                elif entry.lower().endswith((".stl", ".3mf")):
                    file_item = QTreeWidgetItem([entry])
                    file_item.setData(0, Qt.ItemDataRole.UserRole, full_path)
                    file_item.setIcon(0, self.file_icon)
                    self.apply_favorite_style(file_item, full_path)
                    parent_item.addChild(file_item)
        except PermissionError:
            pass
        except FileNotFoundError:
            pass

    def apply_favorite_style(self, item, file_path):
        if file_path in self.favorite_files:
            item.setForeground(0, QBrush(QColor("#ffd54a")))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        else:
            item.setForeground(0, QBrush(Qt.GlobalColor.white))
            font = item.font(0)
            font.setBold(False)
            item.setFont(0, font)

    def apply_gallery_title_style(self, label, file_path):
        if file_path in self.favorite_files:
            label.setStyleSheet(
                "QLabel { font-weight: bold; color: #ffd54a; padding: 4px; } "
                "QLabel:hover { color: #ffe78d; text-decoration: underline; }"
            )
        else:
            label.setStyleSheet(
                "QLabel { font-weight: bold; color: white; padding: 4px; } "
                "QLabel:hover { color: #00aaff; text-decoration: underline; }"
            )

    def refresh_tree_favorite_styles(self):
        def walk(item):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and os.path.isfile(path) and path.lower().endswith((".stl", ".3mf")):
                self.apply_favorite_style(item, path)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def toggle_favorite_file(self, item, file_path):
        if file_path in self.favorite_files:
            self.favorite_files.remove(file_path)
        else:
            self.favorite_files.add(file_path)
        if item is not None:
            self.apply_favorite_style(item, file_path)
        self.refresh_tree_favorite_styles()
        for gallery_item in self.gallery_items:
            if gallery_item.get("mesh_path") == file_path:
                self.apply_gallery_title_style(gallery_item["title_label"], file_path)
        self.save_paths()

    def on_item_expanded(self, item):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if os.path.isdir(path):
            item.takeChildren()
            self.add_items(item, path)

    # -----------------------------------------------------
    # Buttons
    # -----------------------------------------------------
    def change_start_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Neuer Startordner", self.start_dir)
        if folder:
            self.start_dir = folder
            self.save_paths()
            self.populate_all_paths()

    def add_new_path(self):
        # Korrigierte Variable: self.start_dir (vorher self.start_field - Tippfehler)
        folder = QFileDialog.getExistingDirectory(self, "Neuen Pfad hinzufügen", self.start_dir)
        if folder and folder not in self.extra_dirs and folder != self.start_dir:
            self.extra_dirs.append(folder)
            self.save_paths()
            self.add_tree_root(folder)

    def remove_selected_path(self):
        item = self.tree.currentItem()
        if not item:
            return

        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path == self.start_dir:
            return
        if path not in self.extra_dirs:
            return

        self.extra_dirs.remove(path)
        self.save_paths()

        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            idx = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(idx)

    # -----------------------------------------------------
    # Rechtsklick Menü im Tree
    # -----------------------------------------------------
    def on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        path = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu()
        if os.path.exists(path):
            open_explorer = QAction("Im Explorer öffnen")
            open_explorer.triggered.connect(
                lambda: subprocess.run(['explorer', '/select,', os.path.normpath(path)])
            )
            menu.addAction(open_explorer)
        if os.path.isfile(path) and path.lower().endswith((".stl", ".3mf")):
            if path in self.favorite_files:
                favorite_action = QAction("Favorit entfernen")
            else:
                favorite_action = QAction("Favorit markieren")
            favorite_action.triggered.connect(
                lambda checked=False, i=item, p=path: self.toggle_favorite_file(i, p)
            )
            menu.addAction(favorite_action)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -----------------------------------------------------
    # DoubleClick
    # -----------------------------------------------------
    def on_double_click(self, item, column):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if os.path.isdir(path):
            item.setExpanded(True)
            self.current_dir = path
            self.breadcrumb.setText(f"Pfad: {path}")
            self.start_folder_loading(path)
            self.show_gallery(path)
        elif path.lower().endswith((".stl", ".3mf")):
            self.show_gallery(os.path.dirname(path))
            self.open_large_view(path)

    def on_breadcrumb_click(self, event):
        self.current_dir = self.start_dir
        self.populate_all_paths()

    # -----------------------------------------------------
    # Suche / Filter
    # -----------------------------------------------------
    def filter_tree(self, text):
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            self.filter_item(self.tree.topLevelItem(i), text)

    def filter_item(self, item, text):
        visible = text in item.text(0).lower()
        for i in range(item.childCount()):
            visible_child = self.filter_item(item.child(i), text)
            visible = visible or visible_child
        item.setHidden(not visible)
        return visible

    # -----------------------------------------------------
    # Galerie anzeigen
    # -----------------------------------------------------
    def start_folder_loading(self, folder_path):
        self.loading_status_label.setText(f"Lade Ordner: {folder_path}")
        self.loading_status_label.setVisible(True)
        self.loading_progress.setRange(0, 0)
        self.loading_progress.setValue(0)
        self.loading_progress.setVisible(True)
        QApplication.processEvents()

    def show_gallery(self, folder_path):
        try:
            self.current_mesh_files = [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if f.lower().endswith((".stl", ".3mf"))
            ]
        except Exception:
            self.current_mesh_files = []
        total = len(self.current_mesh_files)
        self.render_total = total
        self.render_done = 0
        if total > 0:
            self.loading_status_label.setText(f"Rendere Vorschau: 0/{total}")
            self.loading_status_label.setVisible(True)
            self.loading_progress.setRange(0, total)
            self.loading_progress.setValue(0)
            self.loading_progress.setVisible(True)
        else:
            self.loading_status_label.setVisible(False)
            self.loading_progress.setVisible(False)
        self.build_gallery()

    def clear_gallery_layout(self):
        for i in reversed(range(self.gallery_layout.count())):
            item = self.gallery_layout.takeAt(i)
            widget = item.widget() if item else None
            if widget:
                widget.setParent(None)

    def clear_gallery_widgets(self):
        for item in self.gallery_items:
            item["frame"].deleteLater()
        self.gallery_items = []
        self.gallery_empty_label = None

    def build_gallery(self):
        self.render_queue = []
        self.currently_rendering = False
        self.clear_gallery_layout()
        self.clear_gallery_widgets()

        if not self.current_mesh_files:
            msg = QLabel("Keine 3D-Dateien gefunden.")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gallery_layout.addWidget(msg, 0, 0)
            self.gallery_empty_label = msg
            return

        for idx, mesh_path in enumerate(self.current_mesh_files):
            frame = ClickableFrame()
            frame.setCursor(Qt.CursorShape.PointingHandCursor)
            frame.setStyleSheet("""
                QFrame { background-color: #1a1a1a; }
                QFrame:hover { background-color: #252525; }
            """)

            vbox = QVBoxLayout(frame)
            vbox.setContentsMargins(0, 0, 0, 0)

            title = QLabel(os.path.basename(mesh_path))
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.apply_gallery_title_style(title, mesh_path)
            title.setCursor(Qt.CursorShape.PointingHandCursor)

            preview_placeholder = QLabel("Vorschau wird vorbereitet...")
            preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_placeholder.setStyleSheet("QLabel { color: #9a9a9a; padding: 8px; }")

            vbox.addWidget(title)
            vbox.addWidget(preview_placeholder)

            frame.on_click = lambda p=mesh_path: self.open_large_view(p)
            self.setup_model_context_menu(frame, mesh_path)
            self.gallery_items.append({
                "frame": frame,
                "vbox": vbox,
                "preview_widget": preview_placeholder,
                "vtk_widget": None,
                "title_label": title,
                "mesh_path": mesh_path,
                "rendered": False,
                "rendering": False,
            })
            self.render_queue.append(idx)

        self.relayout_gallery_only()
        QTimer.singleShot(0, self.process_next_render)

    def relayout_gallery_only(self):
        if not self.gallery_items:
            return

        self.clear_gallery_layout()

        container_width = self.scroll_area.viewport().width()
        total = len(self.gallery_items)
        max_columns = max(1, container_width // self.model_min_width)
        columns = min(total, max_columns)
        spacing = self.gallery_layout.spacing()
        model_width = (container_width - (columns - 1) * spacing) // columns
        vtk_height = int(model_width * 0.7)

        for idx, item in enumerate(self.gallery_items):
            frame = item["frame"]
            preview_widget = item["preview_widget"]
            frame.setFixedSize(model_width, vtk_height + 40)
            preview_widget.setFixedSize(model_width, vtk_height)
            row = idx // columns
            col = idx % columns
            self.gallery_layout.addWidget(frame, row, col)

    def process_next_render(self):
        if self.currently_rendering:
            return

        while self.render_queue:
            idx = self.render_queue.pop(0)

            if idx < 0 or idx >= len(self.gallery_items):
                continue

            item = self.gallery_items[idx]
            if item["rendered"] or item["rendering"]:
                continue

            self.currently_rendering = True
            item["rendering"] = True

            # Teures VTK-Widget erst beim echten Rendern erzeugen.
            if item["vtk_widget"] is None:
                vtk_widget = QVTKRenderWindowInteractor(item["frame"])
                old_preview = item["preview_widget"]
                vbox = item["vbox"]
                insert_idx = vbox.indexOf(old_preview)
                vbox.removeWidget(old_preview)
                old_preview.deleteLater()
                if insert_idx >= 0:
                    vbox.insertWidget(insert_idx, vtk_widget)
                else:
                    vbox.addWidget(vtk_widget)
                item["vtk_widget"] = vtk_widget
                item["preview_widget"] = vtk_widget

            self.render_mesh(
                item["vtk_widget"],
                item["mesh_path"],
                on_finished=lambda success, idx=idx: self.on_render_finished(idx, success)
            )
            return

    def on_render_finished(self, idx, success):
        if 0 <= idx < len(self.gallery_items):
            item = self.gallery_items[idx]
            item["rendering"] = False
            item["rendered"] = success

            self.render_done += 1
            if self.render_total > 0:
                self.loading_progress.setValue(min(self.render_done, self.render_total))
                self.loading_status_label.setText(
                    f"Rendere Vorschau: {min(self.render_done, self.render_total)}/{self.render_total}"
                )

        self.currently_rendering = False
        if self.render_queue:
            QTimer.singleShot(0, self.process_next_render)
        else:
            self.loading_status_label.setVisible(False)
            self.loading_progress.setVisible(False)

    # -----------------------------------------------------
    # Kontextmenü pro Modell
    # -----------------------------------------------------
    def setup_model_context_menu(self, frame, mesh_path):
        def on_context(pos):
            menu = QMenu()
            open_explorer = QAction("Im Explorer öffnen")
            open_explorer.triggered.connect(
                lambda: subprocess.run(['explorer', '/select,', os.path.normpath(mesh_path)])
            )
            menu.addAction(open_explorer)
            if mesh_path in self.favorite_files:
                favorite_action = QAction("Favorit entfernen")
            else:
                favorite_action = QAction("Favorit markieren")
            favorite_action.triggered.connect(
                lambda checked=False, p=mesh_path: self.toggle_favorite_file(None, p)
            )
            menu.addAction(favorite_action)
            menu.exec(frame.mapToGlobal(pos))

        frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        frame.customContextMenuRequested.connect(on_context)

    # -----------------------------------------------------
    # Einzelansicht
    # -----------------------------------------------------
    def open_large_view(self, mesh_path):
        dlg = QDialog(self)
        dlg.setWindowTitle(os.path.basename(mesh_path))
        dlg.resize(1200, 900)
        layout = QVBoxLayout(dlg)
        reset_button = QPushButton("Ansicht zurücksetzen (R)")
        layout.addWidget(reset_button)
        vtk_widget = QVTKRenderWindowInteractor(dlg)
        layout.addWidget(vtk_widget)
        self.render_mesh(vtk_widget, mesh_path)
        reset_button.clicked.connect(lambda: self.reset_camera_view(vtk_widget))
        dlg.exec()

    def reset_camera_view(self, vtk_widget):
        renderer = getattr(vtk_widget, "_renderer", None)
        actor = getattr(vtk_widget, "_actor", None)
        if renderer is None:
            return
        if actor is not None:
            renderer.ResetCamera(actor.GetBounds())
        else:
            renderer.ResetCamera()
        vtk_widget.GetRenderWindow().Render()

    # -----------------------------------------------------
    # Mesh Rendering
    # -----------------------------------------------------
    def render_mesh(self, vtk_widget, mesh_path, on_finished=None):
        def do_render():
            ext = mesh_path.lower().split(".")[-1]
            polydata = None

            if ext == "stl":
                reader = vtk.vtkSTLReader()
                reader.SetFileName(mesh_path)
                reader.Update()
                polydata = reader.GetOutput()

            elif ext == "3mf":
                if hasattr(vtk, "vtk3MFReader"):
                    try:
                        reader = vtk.vtk3MFReader()
                        reader.SetFileName(mesh_path)
                        reader.Update()
                        polydata = reader.GetOutput()
                    except:
                        polydata = None

                if polydata is None:
                    try:
                        mesh = trimesh.load(mesh_path, force="scene")
                        if isinstance(mesh, trimesh.Scene):
                            mesh = trimesh.util.concatenate(mesh.dump())
                        vertices = mesh.vertices
                        faces = mesh.faces
                        points = vtk.vtkPoints()
                        for v in vertices:
                            points.InsertNextPoint(v.tolist())
                        polys = vtk.vtkCellArray()
                        for f in faces:
                            polys.InsertNextCell(3)
                            for i in f:
                                polys.InsertCellPoint(int(i))
                        polydata = vtk.vtkPolyData()
                        polydata.SetPoints(points)
                        polydata.SetPolys(polys)
                    except Exception as e:
                        print("3MF Fehler:", e)
                        if on_finished:
                            on_finished(False)
                        return

            else:
                if on_finished:
                    on_finished(False)
                return

            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(polydata)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(0.8, 0.8, 0.8)

            renderer = vtk.vtkRenderer()
            renderer.AddActor(actor)
            renderer.SetBackground(0.1, 0.1, 0.1)

            win = vtk_widget.GetRenderWindow()
            win.GetRenderers().RemoveAllItems()
            win.AddRenderer(renderer)
            vtk_widget._renderer = renderer
            vtk_widget._actor = actor
            interactor = win.GetInteractor()
            style = OrcaLikeInteractorStyle(
                reset_callback=lambda w=vtk_widget: self.reset_camera_view(w)
            )
            interactor.SetInteractorStyle(style)
            self.reset_camera_view(vtk_widget)
            win.Render()
            interactor.Initialize()
            if on_finished:
                on_finished(True)

        QTimer.singleShot(0, do_render)

    def resizeEvent(self, event):
        if self.current_mesh_files:
            self.resize_timer.start(120)
        super().resizeEvent(event)


# =====================================================
#                   APP START + SPLASHSCREEN
# =====================================================
from PyQt6.QtWidgets import QSplashScreen
from PyQt6.QtGui import QFont, QPainter, QColor, QPixmap, QIcon

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # App Icon laden (aus PyInstaller oder lokal). Wenn nicht vorhanden: ignorieren.
    icon = None
    try:
        icon_path = resource_path("10485587.png")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            app.setWindowIcon(icon)
    except Exception as e:
        print("Icon konnte nicht geladen werden:", e)

    # Splashscreen Bild laden (wenn vorhanden)
    try:
        splash_image = resource_path("splashscreen3d.png")
        if os.path.exists(splash_image):
            splash_pix = QPixmap(splash_image)
            splash = QSplashScreen(splash_pix)
            splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            splash.show()

            font = QFont("Segoe UI", 15)
            splash.setFont(font)

            def loading(step, text):
                splash.showMessage(
                    f"{text} ({step}/5)",
                    Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter,
                    Qt.GlobalColor.white
                )
                app.processEvents()
                time.sleep(0.6)

            loading(1, "Initialisiere GUI…")
            loading(2, "Lade Dateien…")
            loading(3, "Initialisiere 3D Engine…")
            loading(4, "Initialisiere Explorer…")
            loading(5, "Starte Anwendung…")

        else:
            splash = None
    except Exception as e:
        print("Splash konnte nicht geladen werden:", e)
        splash = None

    # Hauptfenster starten
    window = ModelGallery()
    if icon:
        window.setWindowIcon(icon)

    if splash:
        splash.finish(window)

    window.show()
    sys.exit(app.exec())
