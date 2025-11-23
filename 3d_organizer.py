import sys
import os
import json
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QScrollArea, QGridLayout, QVBoxLayout, QLabel, QFrame, QDialog,
    QLineEdit, QPushButton, QFileDialog, QMenu
)
from PyQt6.QtGui import QAction, QIcon, QCursor
from PyQt6.QtCore import Qt, QTimer
import numpy as np
import trimesh
import vtk

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except Exception:
    from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor # type: ignore

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

class ModelGallery(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D Galerie LeCheim")
        self.setGeometry(100, 100, 1600, 900)

        self.start_dir = r"C:\\"
        self.extra_dirs = []
        self.current_dir = self.start_dir
        self.model_min_width = 300
        self.current_mesh_files = []

        self.load_paths()

        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # -------------------
        # Rechte Seite: Galerie
        # -------------------
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.gallery_container = QWidget()
        self.gallery_layout = QGridLayout()
        self.gallery_layout.setSpacing(10)
        self.gallery_container.setLayout(self.gallery_layout)
        self.scroll_area.setWidget(self.gallery_container)
        main_layout.addWidget(self.scroll_area, 6)

        # -------------------
        # Linke Seite: Explorer
        # -------------------
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
        self.breadcrumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.breadcrumb.mousePressEvent = self.on_breadcrumb_click
        left_container.addWidget(self.breadcrumb)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Ordner / 3D-Dateien")
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        self.tree.itemExpanded.connect(self.on_item_expanded)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        left_container.addWidget(self.tree)
        main_layout.addWidget(left_widget, 2)

        self.folder_icon = QIcon.fromTheme("folder")
        self.file_icon = QIcon.fromTheme("text-x-generic")

        self.populate_all_paths()

    # -------------------
    # JSON Laden/Speichern
    # -------------------
    def load_paths(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.start_dir = data.get("start_dir", self.start_dir)
                    self.extra_dirs = data.get("extra_dirs", [])
            except Exception as e:
                print("Fehler beim Laden der Pfade:", e)
                self.extra_dirs = []
        else:
            self.extra_dirs = []

    def save_paths(self):
        data = {
            "start_dir": self.start_dir,
            "extra_dirs": self.extra_dirs,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # -------------------
    # Tree Befüllen
    # -------------------
    def populate_all_paths(self):
        self.tree.clear()
        # Startordner
        self.add_tree_root(self.start_dir, is_start=True)
        # Extra Pfade
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
                    dir_item.addChild(QTreeWidgetItem(["..."]))  # immer aufklappbar
                    parent_item.addChild(dir_item)
                elif entry.lower().endswith((".stl", ".3mf")):
                    file_item = QTreeWidgetItem([entry])
                    file_item.setData(0, Qt.ItemDataRole.UserRole, full_path)
                    file_item.setIcon(0, self.file_icon)
                    parent_item.addChild(file_item)
        except PermissionError:
            pass

    def on_item_expanded(self, item):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if os.path.isdir(path):
            item.takeChildren()
            self.add_items(item, path)

    # -------------------
    # Buttons
    # -------------------
    def change_start_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Neuer Startordner", self.start_dir)
        if folder:
            self.start_dir = folder
            self.save_paths()
            self.populate_all_paths()

    def add_new_path(self):
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
            return  # Startordner darf nicht entfernt werden
        if path not in self.extra_dirs:
            return  # Nur extra_dirs sollen entfernt werden

        # Entferne aus extra_dirs
        self.extra_dirs.remove(path)
        self.save_paths()

        # Entferne Tree-Eintrag
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            idx = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(idx)

    # -------------------
    # Rechtsklick Menü Tree
    # -------------------
    def on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu()
        # Explorer öffnen
        if os.path.exists(path):
            open_explorer = QAction("Im Explorer öffnen")
            open_explorer.triggered.connect(lambda: subprocess.run(['explorer', '/select,', os.path.normpath(path)]))
            menu.addAction(open_explorer)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -------------------
    # DoubleClick
    # -------------------
    def on_double_click(self, item, column):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if os.path.isdir(path):
            self.current_dir = path
            self.breadcrumb.setText(f"Pfad: {path}")
            self.show_gallery(path)
        elif path.lower().endswith((".stl", ".3mf")):
            self.show_gallery(os.path.dirname(path))
            self.open_large_view(path)

    def on_breadcrumb_click(self, event):
        self.current_dir = self.start_dir
        self.populate_all_paths()

    def filter_tree(self, text):
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            self.filter_item(self.tree.topLevelItem(i), text)

    def filter_item(self, item, text):
        visible = text in item.text(0).lower()
        for i in range(item.childCount()):
            child_visible = self.filter_item(item.child(i), text)
            visible = visible or child_visible
        item.setHidden(not visible)
        return visible

    # -------------------
    # Galerie anzeigen
    # -------------------
    def show_gallery(self, folder_path):
        self.current_mesh_files = [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path)
            if f.lower().endswith((".stl", ".3mf"))
        ]
        self.update_gallery_layout()

    def update_gallery_layout(self):
        for i in reversed(range(self.gallery_layout.count())):
            widget = self.gallery_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        if not self.current_mesh_files:
            msg = QLabel("Keine 3D-Dateien gefunden.")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gallery_layout.addWidget(msg, 0, 0)
            return

        container_width = self.scroll_area.viewport().width()
        total = len(self.current_mesh_files)
        max_columns = max(1, container_width // self.model_min_width)
        columns = min(total, max_columns)
        spacing = self.gallery_layout.spacing()
        model_width = (container_width - (columns - 1) * spacing) // columns
        vtk_height = int(model_width * 0.7)

        for idx, mesh_path in enumerate(self.current_mesh_files):
            frame = ClickableFrame()
            frame.setFixedSize(model_width, vtk_height + 40)
            frame.setCursor(Qt.CursorShape.PointingHandCursor)
            frame.setStyleSheet("""
                QFrame { background-color: #1a1a1a; }
                QFrame:hover { background-color: #252525; }
            """)

            vbox = QVBoxLayout(frame)
            vbox.setContentsMargins(0, 0, 0, 0)

            title = QLabel(os.path.basename(mesh_path))
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("""
                QLabel { font-weight: bold; color: white; padding: 4px; }
                QLabel:hover { color: #00aaff; text-decoration: underline; }
                """)
            title.setCursor(Qt.CursorShape.PointingHandCursor)

            vtk_widget = QVTKRenderWindowInteractor(frame)
            vtk_widget.setFixedSize(model_width, vtk_height)

            vbox.addWidget(title)
            vbox.addWidget(vtk_widget)

            self.render_mesh(vtk_widget, mesh_path)
            frame.on_click = lambda p=mesh_path: self.open_large_view(p)
            self.setup_model_context_menu(frame, mesh_path)

            row = idx // columns
            col = idx % columns
            self.gallery_layout.addWidget(frame, row, col)

    # -------------------
    # Kontextmenü pro Modell
    # -------------------
    def setup_model_context_menu(self, frame, mesh_path):
        def on_context(pos):
            menu = QMenu()
            open_explorer = QAction("Im Explorer öffnen")
            open_explorer.triggered.connect(lambda: subprocess.run(['explorer', '/select,', os.path.normpath(mesh_path)]))
            menu.addAction(open_explorer)
            menu.exec(frame.mapToGlobal(pos))
        frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        frame.customContextMenuRequested.connect(on_context)

    # -------------------
    # Einzelansicht
    # -------------------
    def open_large_view(self, mesh_path):
        dlg = QDialog(self)
        dlg.setWindowTitle(os.path.basename(mesh_path))
        dlg.resize(1200, 900)
        layout = QVBoxLayout(dlg)
        vtk_widget = QVTKRenderWindowInteractor(dlg)
        layout.addWidget(vtk_widget)
        self.render_mesh(vtk_widget, mesh_path)
        dlg.exec()

    # -------------------
    # VTK Render
    # -------------------
    def render_mesh(self, vtk_widget, mesh_path):
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
                        return
            else:
                return

            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(polydata)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(0.8, 0.8, 0.8)
            renderer = vtk.vtkRenderer()
            renderer.AddActor(actor)
            renderer.SetBackground(0.1, 0.1, 0.1)
            renderer.ResetCamera()

            win = vtk_widget.GetRenderWindow()
            win.AddRenderer(renderer)
            win.Render()
            interactor = win.GetInteractor()
            interactor.Initialize()

        QTimer.singleShot(200, do_render)

    def resizeEvent(self, event):
        if self.current_mesh_files:
            self.update_gallery_layout()
        super().resizeEvent(event)

# -----------------------------
# Start App mit Splashscreen
# -----------------------------
from PyQt6.QtWidgets import QSplashScreen
from PyQt6.QtGui import QFont, QPainter, QColor
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt
import time

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Icon setzen
    icon = QIcon(r"C:\Users\daniel\Pictures\Saved Pictures\10485587.png")
    app.setWindowIcon(icon)

    # Splashscreen Bildpfad
    splash_image = r"C:\Users\daniel\Pictures\Saved Pictures\1.png"

    splash_pix = QPixmap(splash_image)
    splash = QSplashScreen(splash_pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    splash.show()

    # Schriftgröße anpassen
    font = QFont("Segoe UI", 15)  # 20 pt, kannst du anpassen
    splash.setFont(font)

    # Fortschritt anzeigen
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

    # Hauptfenster starten
    window = ModelGallery()
    window.setWindowIcon(icon)

    splash.finish(window)
    window.show()

    sys.exit(app.exec())

