"""A painted, animated dock with native Qt configuration dialogs."""

from __future__ import annotations

import math
from pathlib import Path
import time

from PyQt6.QtCore import (
    QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QUrl,
    QThreadPool, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QDrag, QFont, QIcon, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRegion, QTransform,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from . import panel as desktop_panel
from .backend import (
    DesktopApp, SettingsStore, activate_window, app_matches_window,
    autostart_enabled, discover_apps, launch_app, list_windows,
    resolve_dropped_desktops, set_autostart,
)
from .x11 import GlobalShortcut, set_window_layer


APP_MIME = "application/x-flowdocks-app"


THEMES = {
    "midnight": ("#111c30", "#090f1d", "#70e1ed", "#eef6ff"),
    "graphite": ("#292c33", "#13151b", "#d7b58b", "#faf4ed"),
    "aurora": ("#20223c", "#111528", "#baafff", "#f2efff"),
}

DIALOG_STYLE = """
QDialog { background: #101827; color: #ecf2fc; }
QLabel, QCheckBox { color: #ecf2fc; font-size: 13px; }
QLabel[muted="true"] { color: #92a3b9; }
QLabel[heading="true"] { font-size: 27px; font-weight: 600; }
QLineEdit, QComboBox { background: #1a2639; color: #ecf2fc; border: 1px solid #31425b;
    border-radius: 8px; padding: 10px; selection-background-color: #347d91; }
QLineEdit:focus, QComboBox:focus { border-color: #70e1ed; }
QComboBox:hover { border-color: #46617f; background: #1e2c42; }
/* The chevron is painted by ComboBox below: Qt's stylesheet engine cannot
   rotate a border triangle, and collapses a zero-sized one into a dash. */
QComboBox { padding-right: 32px; }
QComboBox::drop-down { border: none; background: transparent; width: 0; }
QComboBox::down-arrow { image: none; border: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: #172335; color: #ecf2fc;
    border: 1px solid #3a5271; border-radius: 8px; padding: 5px; outline: none;
    selection-background-color: #2c4860; }
QComboBox QAbstractItemView::item { border-radius: 6px; padding: 7px 9px; min-height: 20px; }
QComboBox QAbstractItemView::item:hover { background: #24374f; }
QPushButton { background: #223249; color: #eaf2ff; border: 1px solid #354a65;
    border-radius: 8px; padding: 10px 17px; font-weight: 600; }
QPushButton:hover { background: #30445e; border-color: #70e1ed; }
QPushButton:disabled { color: #63758d; border-color: #253248; }
QPushButton[primary="true"] { background: #70e1ed; color: #10202c; border: none; }
QListWidget { background: #141f30; color: #ecf2fc; border: 1px solid #293951;
    border-radius: 10px; padding: 8px; outline: none; }
QListWidget::item { border-radius: 8px; padding: 6px; }
QListWidget::item:selected { background: #2c4860; }
QListWidget::item:hover { background: #23354c; }
QSlider::groove:horizontal { height: 5px; background: #2b3b52; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #70e1ed; border-radius: 2px; }
QSlider::handle:horizontal { background: #ecfaff; width: 15px; margin: -5px 0; border-radius: 7px; }
QCheckBox { spacing: 9px; padding: 5px 0; }
QMenu { background: #172235; color: #eaf2ff; border: 1px solid #354a65; padding: 6px; }
QMenu::item { padding: 8px 20px; border-radius: 4px; }
QMenu::item:selected { background: #30445e; }
QMenu::separator { height: 1px; background: #354a65; margin: 5px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(120, 150, 190, 60); border-radius: 5px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: rgba(140, 180, 225, 110); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class Worker(QRunnable):
    def __init__(self, function, *args):
        super().__init__()
        self.function, self.args = function, args
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.function(*self.args))
        except Exception as error:
            self.signals.failed.emit(str(error))


def app_icon(app: DesktopApp | None = None, name="view-app-grid") -> QIcon:
    if app and app.icon:
        icon = QIcon(app.icon) if Path(app.icon).is_absolute() else QIcon.fromTheme(app.icon)
        if not icon.isNull():
            return icon
    icon = QIcon.fromTheme(name if app is None else "application-x-executable")
    if not icon.isNull():
        return icon
    pixmap = QPixmap(96, 96)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#365b78"))
    painter.setPen(QPen(QColor("#70e1ed"), 2))
    painter.drawRoundedRect(QRectF(8, 8, 80, 80), 20, 20)
    painter.setPen(QColor("#effaff"))
    painter.setFont(QFont("Sans Serif", 28, QFont.Weight.DemiBold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, app.name[:1].upper() if app else "F")
    painter.end()
    return QIcon(pixmap)


def limit_to_one_combination(editor):
    """Cap a shortcut editor at a single combination where Qt supports it.

    setMaximumSequenceLength arrived in Qt 6.5. Ubuntu 24.04 ships PyQt6 6.6
    bindings over a Qt 6.4 runtime, so the binding version does not tell you
    whether it exists. Its absence only means a longer sequence can be typed;
    the parser rejects anything but one combination with a readable message.
    """
    if hasattr(editor, "setMaximumSequenceLength"):
        editor.setMaximumSequenceLength(1)


def label(text, heading=False, muted=False):
    widget = QLabel(text)
    widget.setProperty("heading", heading)
    widget.setProperty("muted", muted)
    widget.setWordWrap(True)
    return widget


class ComboBox(QComboBox):
    """A combo box that paints its own chevron instead of Qt's raised arrow."""

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = self.underMouse() or self.view().isVisible()
        painter.setPen(QPen(QColor("#70e1ed" if lit else "#8fa5c0"), 1.9,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 19, self.height() / 2 - 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4.5, y), QPointF(x, y + 4.7),
                                        QPointF(x + 4.5, y)]))
        painter.end()


class ApplicationList(QListWidget):
    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.setDragEnabled(True)

    def startDrag(self, _):
        item = self.currentItem()
        if not item:
            return
        app_id = item.data(Qt.ItemDataRole.UserRole)
        app = self.dock.catalog.get(app_id)
        if not app:
            return
        mime = QMimeData()
        mime.setData(APP_MIME, app_id.encode("utf-8"))
        if app.path:
            mime.setUrls([QUrl.fromLocalFile(app.path)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(app_icon(app).pixmap(48, 48))
        drag.setHotSpot(QPoint(24, 24))
        self.dock.reveal()
        self.dock.external_drag = True
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.dock.external_drag = False
            self.dock.drop_index = None
            self.dock.last_inside = time.monotonic()
            self.dock.update()


class AppPicker(QDialog):
    def __init__(self, dock):
        super().__init__(dock, Qt.WindowType.Dialog)
        self.dock = dock
        self.setWindowTitle("FlowDocks | Applications")
        self.setStyleSheet(DIALOG_STYLE)
        self.setMinimumSize(560, 500)
        self.resize(600, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(15)
        layout.addWidget(label("Your launchpad", heading=True))
        layout.addWidget(label("Drag an app onto the dock, or select it and choose Pin.", muted=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search installed applications...")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list = ApplicationList(dock)
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(QSize(40, 40))
        self.list.setGridSize(QSize(126, 96))
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.count = label("", muted=True)
        row.addWidget(self.count, 1)
        self.pin = QPushButton("Pin to dock")
        self.open = QPushButton("Launch")
        self.open.setProperty("primary", True)
        row.addWidget(self.pin)
        row.addWidget(self.open)
        layout.addLayout(row)
        self.search.textChanged.connect(self.populate)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemDoubleClicked.connect(self.launch_selected)
        self.open.clicked.connect(self.launch_selected)
        self.pin.clicked.connect(self.toggle_pin)
        self.populate()
        self.search.setFocus()

    def populate(self):
        query = self.search.text().casefold().strip()
        self.list.clear()
        for app in self.dock.apps:
            if query and query not in f"{app.name} {app.id}".casefold():
                continue
            pinned = app.id in self.dock.store.data["pinned"]
            item = QListWidgetItem(app_icon(app), app.name)
            item.setData(Qt.ItemDataRole.UserRole, app.id)
            item.setToolTip(app.name + (" (pinned)" if pinned else ""))
            self.list.addItem(item)
        self.count.setText(f"{self.list.count()} apps")
        if self.list.count():
            self.list.setCurrentRow(0)
        self.selection_changed()

    def selected(self):
        item = self.list.currentItem()
        return self.dock.catalog.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def selection_changed(self, *_):
        app = self.selected()
        self.pin.setEnabled(app is not None)
        self.open.setEnabled(app is not None)
        self.pin.setText("Unpin" if app and app.id in self.dock.store.data["pinned"] else "Pin to dock")

    def toggle_pin(self):
        app = self.selected()
        if app:
            self.dock.toggle_pin(app.id)
            self.selection_changed()

    def launch_selected(self, *_):
        app = self.selected()
        if app:
            self.dock.launch(app)
            self.accept()


class SettingsDialog(QDialog):
    def __init__(self, dock):
        super().__init__(dock, Qt.WindowType.Dialog)
        self.dock = dock
        self.setWindowTitle("FlowDocks | Preferences")
        self.setStyleSheet(DIALOG_STYLE)
        self.setMinimumWidth(450)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: #101827; }")
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(18)
        layout.addWidget(label("Make it yours.", heading=True))
        layout.addWidget(label("A little space for the things you use most.", muted=True))
        form = QFormLayout()
        form.setSpacing(18)
        settings = dock.store.data
        self.theme = ComboBox()
        for key in THEMES:
            self.theme.addItem(key.title(), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings["theme"])))
        form.addRow("Appearance", self.theme)
        self.move_mode = ComboBox()
        for title, key in (("Snap to a screen edge", "edge"), ("Free / place anywhere", "free")):
            self.move_mode.addItem(title, key)
        self.move_mode.setCurrentIndex(self.move_mode.findData(settings["move_mode"]))
        form.addRow("Positioning", self.move_mode)
        self.position = ComboBox()
        for key in ("bottom", "top", "left", "right"):
            self.position.addItem(key.title(), key)
        self.position.setCurrentIndex(self.position.findData(settings["position"]))
        form.addRow("Screen edge", self.position)
        self.orientation = ComboBox()
        for title, key in (("Horizontal", "horizontal"), ("Vertical", "vertical")):
            self.orientation.addItem(title, key)
        self.orientation.setCurrentIndex(self.orientation.findData(settings["orientation"]))
        form.addRow("Orientation", self.orientation)
        # A free dock floats, so the edge and its hotspot no longer apply, while a
        # docked one takes its orientation from the edge it is attached to.
        self.move_mode.currentIndexChanged.connect(self.mode_changed)
        self.position.currentIndexChanged.connect(self.mode_changed)
        self.screen = ComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screen.addItem(f"{index + 1}  /  {screen.name()}", index)
        self.screen.setCurrentIndex(min(settings["screen"], self.screen.count() - 1))
        form.addRow("Display", self.screen)
        self.layer = ComboBox()
        for title, key in (("Normal / windows can cover the dock", "normal"), ("Above other windows", "above"), ("Below other windows", "below")):
            self.layer.addItem(title, key)
        self.layer.setCurrentIndex(self.layer.findData(settings["layer"]))
        form.addRow("Screen layer", self.layer)
        self.edge_action = ComboBox()
        for title, key in (("Reveal on edge hover", "reveal"), ("Toggle on edge hover", "toggle"), ("Disabled", "off")):
            self.edge_action.addItem(title, key)
        self.edge_action.setCurrentIndex(self.edge_action.findData(settings["edge_action"]))
        form.addRow("Edge action", self.edge_action)
        self.shortcut = QKeySequenceEdit(QKeySequence(settings["shortcut"]))
        limit_to_one_combination(self.shortcut)
        form.addRow("Toggle shortcut", self.shortcut)
        self.size = self.slider(form, "Icon size", 32, 80, settings["icon_size"], " px")
        self.zoom = self.slider(form, "Hover zoom", 100, 200, round(settings["magnification"] * 100), "%")
        self.opacity = self.slider(form, "Surface opacity", 35, 100, settings["opacity"], "%")
        layout.addLayout(form)
        self.hide = QCheckBox("Auto-hide when the pointer leaves")
        self.hide.setChecked(settings["auto_hide"])
        self.autostart = QCheckBox("Start automatically when I sign in")
        self.autostart.setChecked(autostart_enabled())
        layout.addWidget(self.hide)
        self.shortcut_enabled = QCheckBox("Enable global shortcut (X11)")
        self.shortcut_enabled.setChecked(settings["shortcut_enabled"])
        layout.addWidget(self.shortcut_enabled)
        self.lock_position = QCheckBox("Lock dock position")
        self.lock_position.setChecked(settings["lock_position"])
        layout.addWidget(self.lock_position)
        layout.addWidget(self.autostart)
        self.panel_row(layout)
        self.mode_changed()
        layout.addWidget(label(
            "Drag icons to reorder; drop outside to unpin. Drop an installed\n"
            "launcher on the dock to pin it. Drag the grip, clock or empty\n"
            "surface to move the dock: it snaps to an edge, or goes anywhere\n"
            "in free positioning. Free positioning has no edge, so auto-hide\n"
            "and edge actions are disabled there; use the shortcut or tray.\n"
            "Revealing temporarily raises the dock until you leave it.", muted=True))
        if QApplication.platformName() != "xcb":
            layout.addWidget(label("Wayland: launching works, but positioning, edge reveal and window tracking depend on your compositor.", muted=True))
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        save = QPushButton("Apply changes")
        save.setProperty("primary", True)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.apply)
        row.addWidget(cancel)
        row.addWidget(save)
        outer.addLayout(row)
        self.resize(540, min(800, dock.selected_screen().availableGeometry().height() - 60))

    def mode_changed(self, *_):
        free = self.move_mode.currentData() == "free"
        self.position.setEnabled(not free)
        self.edge_action.setEnabled(not free)
        self.hide.setEnabled(not free)
        self.orientation.setEnabled(free)
        if not free:
            edge = self.position.currentData()
            self.orientation.setCurrentIndex(self.orientation.findData(
                "horizontal" if edge in ("bottom", "top") else "vertical"))

    def panel_row(self, layout):
        """Expose the desktop's own panel next to the dock's settings."""
        controller = self.dock.panel
        layout.addWidget(label(f"Desktop panel  ({controller.name})", heading=False))
        row = QHBoxLayout()
        hidden = controller.hidden() if controller.can_hide else None
        toggle = QPushButton("Show panel" if hidden else "Hide panel")
        toggle.setEnabled(controller.can_hide)
        toggle.clicked.connect(lambda: self.dock.panel_action(controller.set_hidden, not hidden))
        settings = QPushButton("Panel settings...")
        settings.setEnabled(controller.can_settings)
        settings.clicked.connect(lambda: self.dock.panel_action(controller.open_settings))
        row.addWidget(toggle)
        row.addWidget(settings)
        row.addStretch()
        layout.addLayout(row)
        if controller.can_move:
            moves = QHBoxLayout()
            moves.addWidget(label("Move panel to", muted=True))
            for edge in desktop_panel.EDGES:
                button = QPushButton(edge.title())
                button.clicked.connect(lambda checked=False, edge=edge:
                                       self.dock.panel_action(controller.move, edge))
                moves.addWidget(button)
            moves.addStretch()
            layout.addLayout(moves)
        if controller.reason:
            layout.addWidget(label(controller.reason, muted=True))

    def slider(self, form, title, minimum, maximum, value, suffix):
        row = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        text = label(f"{slider.value()}{suffix}", muted=True)
        text.setFixedWidth(58)
        slider.valueChanged.connect(lambda v: text.setText(f"{v}{suffix}"))
        row.addWidget(slider)
        row.addWidget(text)
        form.addRow(title, row)
        return slider

    def apply(self):
        sequence = self.shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if self.shortcut_enabled.isChecked() and not self.dock.configure_shortcut(True, sequence):
            return
        if not self.shortcut_enabled.isChecked():
            self.dock.configure_shortcut(False, sequence)
        self.dock.store.data.update(
            theme=self.theme.currentData(), position=self.position.currentData(),
            screen=self.screen.currentData(), icon_size=self.size.value(),
            magnification=self.zoom.value() / 100, opacity=self.opacity.value(),
            auto_hide=self.hide.isChecked(),
            layer=self.layer.currentData(), edge_action=self.edge_action.currentData(),
            shortcut_enabled=self.shortcut_enabled.isChecked(), shortcut=sequence or "Ctrl+Alt+A",
            lock_position=self.lock_position.isChecked(),
            move_mode=self.move_mode.currentData(),
            orientation=self.orientation.currentData(),
        )
        if not self.dock.smoke_test and self.autostart.isChecked() != autostart_enabled():
            set_autostart(self.autostart.isChecked())
        self.dock.save_settings()
        self.dock.temporary_raise = False
        self.dock.apply_layer()
        self.dock.rebuild()
        self.accept()


class Dock(QWidget):
    def __init__(self, store: SettingsStore, smoke_test=False):
        super().__init__()
        self.store, self.smoke_test = store, smoke_test
        self.setWindowTitle("FlowDocks")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.panel = desktop_panel.controller()
        self.grab_offset = QPoint()
        self.apps = discover_apps()
        self.catalog = {app.id: app for app in self.apps}
        self.windows = []
        self.entries = []
        self.rects = []
        self.scales = []
        self.icons = {}
        self.hover = -1
        self.pressed = -1
        self.press_point = QPoint()
        self.dragging = False
        self.dock_dragging = False
        self.move_candidate = False
        self.external_drag = False
        self.drop_index = None
        self.drag_point = QPointF()
        self.press_global = QPoint()
        self.manual_hidden = False
        self.temporary_raise = False
        self.edge_since = None
        self.edge_latched = False
        self.hidden_amount = 0.0
        self.last_inside = time.monotonic()
        self.dialog = None
        self.menu_open = False
        self.poll_busy = False
        self.launch_times = {}
        self.last_mask = None
        self.tray = QSystemTrayIcon(app_icon(name="preferences-desktop"), self)
        self.tray.setToolTip("FlowDocks")
        tray_menu = QMenu()
        tray_menu.setStyleSheet(DIALOG_STYLE)
        tray_menu.addAction("Show / hide dock", self.toggle_visibility)
        tray_menu.addAction("Applications", self.open_picker)
        tray_menu.addAction("Preferences", self.open_settings)
        tray_menu.addSeparator()
        tray_menu.addAction("Quit FlowDocks", QApplication.quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if not smoke_test and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.animate)
        self.timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1800)
        self.poll_timer.timeout.connect(self.poll_windows)
        self.poll_timer.start()
        app = QApplication.instance()
        app.screenAdded.connect(self.screens_changed)
        app.screenRemoved.connect(self.screens_changed)
        self.connected_screens = set()
        self.screens_changed()
        self.poll_windows()
        self.global_shortcut = GlobalShortcut(self)
        self.global_shortcut.activated.connect(self.toggle_visibility)
        app.aboutToQuit.connect(self.global_shortcut.close)
        if not smoke_test:
            QTimer.singleShot(0, self.initialize_shortcut)

    def initialize_shortcut(self):
        if self.store.data["shortcut_enabled"]:
            self.configure_shortcut(True, self.store.data["shortcut"])

    def configure_shortcut(self, enabled, sequence):
        if self.smoke_test:
            return True
        if not enabled:
            self.global_shortcut.unregister()
            return True
        error = self.global_shortcut.register(sequence)
        if error:
            self.show_error(error + " You can also bind 'flowdocks --toggle' in your desktop keyboard settings.")
        return not error

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_layer()

    def apply_layer(self):
        if QApplication.platformName() == "xcb":
            set_window_layer(int(self.winId()), "above" if self.temporary_raise else self.store.data["layer"])

    def reveal(self):
        self.manual_hidden = False
        self.last_inside = time.monotonic()
        self.temporary_raise = True
        if not self.isVisible():
            self.show()
        self.apply_layer()
        self.raise_()

    def toggle_visibility(self):
        if self.manual_hidden or self.hidden_amount > 0.5 or not self.isVisible():
            self.reveal()
        else:
            self.manual_hidden = True
            self.edge_latched = True
            if self.dialog:
                self.dialog.reject()

    def set_edge(self, edge):
        self.store.data["position"] = edge
        self.store.data["move_mode"] = "edge"
        self.store.data["alignment"] = 0.5
        self.save_settings()
        self.rebuild()
        self.reveal()

    def screens_changed(self, *_):
        for screen in QApplication.screens():
            if screen not in self.connected_screens:
                screen.geometryChanged.connect(self.reposition)
                self.connected_screens.add(screen)
        self.rebuild()

    def run_worker(self, function, *args, finished=None, failed=None):
        worker = Worker(function, *args)
        if finished:
            worker.signals.finished.connect(finished)
        if failed:
            worker.signals.failed.connect(failed)
        self.pool.start(worker)

    def poll_windows(self):
        if not self.poll_busy:
            self.poll_busy = True
            self.run_worker(list_windows, finished=self.windows_received, failed=self.poll_failed)

    def poll_failed(self, _):
        self.poll_busy = False

    def windows_received(self, windows):
        self.poll_busy = False
        self.windows = windows
        signature = [(entry[0], entry[1].id if entry[0] == "app" else "") for entry in self.entries]
        if signature != [(entry[0], entry[1].id if entry[0] == "app" else "") for entry in self.make_entries()]:
            if not self.dragging and not self.dock_dragging and not self.external_drag and self.pressed < 0:
                self.rebuild()
        self.update()

    def make_entries(self):
        pinned = self.store.data["pinned"]
        apps = [self.catalog[key] for key in pinned if key in self.catalog]
        claimed = {w.id for w in self.windows if any(app_matches_window(app, w) for app in apps)}
        for app in self.apps:
            matches = {w.id for w in self.windows if app_matches_window(app, w)}
            if app.id not in pinned and matches - claimed:
                apps.append(app)
                claimed.update(matches)
        screen = self.selected_screen().geometry()
        extent = screen.width() if self.horizontal else screen.height()
        # Leave enough space for magnification and the three built-in controls.
        capacity = max(1, int((extent - 100) / (self.store.data["icon_size"] + 14)) - 4)
        self.overflow = len(apps) > capacity
        return [("launcher", None), *[("app", app) for app in apps[:capacity]], ("clock", None), ("settings", None)]

    @property
    def free_mode(self):
        return self.store.data["move_mode"] == "free"

    @property
    def horizontal(self):
        # An edge dictates its own orientation, so only a free dock can be rotated.
        if self.free_mode:
            return self.store.data["orientation"] == "horizontal"
        return self.store.data["position"] in ("bottom", "top")

    @property
    def anchor(self):
        """Side of its own window the shelf hugs; the tooltip padding sits opposite.

        A free dock flips that padding to whichever half of the screen it is in, so
        it can sit flush against either edge and the tooltip always has room.
        """
        if not self.free_mode:
            return self.store.data["position"]
        if self.horizontal:
            return "bottom" if self.store.data["free_y"] >= 0.5 else "top"
        return "right" if self.store.data["free_x"] >= 0.5 else "left"

    @property
    def shelf_depth(self):
        """Thickness of the visible shelf, excluding the window's tooltip padding."""
        return self.store.data["icon_size"] + 29

    def selected_screen(self):
        screens = QApplication.screens()
        return screens[min(self.store.data["screen"], len(screens) - 1)]

    def rebuild(self):
        self.entries = self.make_entries()
        self.scales = [1.0] * len(self.entries)
        self.icons = {app.id: app_icon(app) for kind, app in self.entries if kind == "app"}
        self.hover = self.pressed = -1
        self.reposition()

    def reposition(self, *_):
        screen = self.selected_screen().geometry()
        size = self.store.data["icon_size"]
        length = math.ceil((size + 14) * len(self.entries) + size * (self.store.data["magnification"] - 1) * 3 + 52)
        depth = math.ceil(size * self.store.data["magnification"] + 86)
        edge = self.store.data["position"]
        if self.free_mode:
            # Place the visible shelf rather than the padded window, so a free dock
            # reaches an edge instead of stopping a tooltip's height short of it.
            # The window may hang off-screen; that margin is masked out anyway.
            pad = depth - self.shelf_depth - 9 if self.anchor in ("bottom", "right") else 9
            if self.horizontal:
                width, height = min(length, screen.width()), depth
                x = screen.x() + round((screen.width() - width) * self.store.data["free_x"])
                y = screen.y() + round((screen.height() - self.shelf_depth) * self.store.data["free_y"]) - pad
            else:
                width, height = depth, min(length, screen.height())
                x = screen.x() + round((screen.width() - self.shelf_depth) * self.store.data["free_x"]) - pad
                y = screen.y() + round((screen.height() - height) * self.store.data["free_y"])
        elif self.horizontal:
            width, height = min(length, screen.width()), depth
            x = screen.x() + round((screen.width() - width) * self.store.data["alignment"])
            y = screen.bottom() - height + 1 if edge == "bottom" else screen.y()
        else:
            width, height = depth, min(length, screen.height())
            x = screen.right() - width + 1 if edge == "right" else screen.x()
            y = screen.y() + round((screen.height() - height) * self.store.data["alignment"])
        self.setGeometry(x, y, width, height)
        self.layout_icons()

    def layout_icons(self):
        size = self.store.data["icon_size"]
        edge = self.anchor
        widths = [size * scale + 14 for scale in self.scales]
        length = sum(widths) + 24
        extent = self.width() if self.horizontal else self.height()
        shrink = min(1.0, (extent - 20) / length)
        widths = [width * shrink for width in widths]
        length = sum(widths) + 24
        start = (extent - length) / 2
        depth = size + 29
        cross = self.height() if self.horizontal else self.width()
        far = edge in ("bottom", "right")
        # A free-floating dock has no edge to slide into, so it hides outright.
        offset = 0 if self.free_mode else self.hidden_amount * (cross - 3) * (1 if far else -1)
        shelf_cross = cross - depth - 9 + offset if far else 9 + offset
        self.shelf = QRectF(start, shelf_cross, length, depth) if self.horizontal else QRectF(shelf_cross, start, depth, length)
        cursor = start + 12
        self.rects = []
        for scale, width in zip(self.scales, widths):
            actual = size * scale * shrink
            baseline = cross - 27 + offset if far else 27 + offset
            perpendicular = baseline - actual if far else baseline
            along = cursor + (width - actual) / 2
            rect = QRectF(along, perpendicular, actual, actual) if self.horizontal else QRectF(perpendicular, along, actual, actual)
            self.rects.append(rect)
            cursor += width
        region = QRegion(self.shelf.adjusted(-7, -7, 7, 7).toAlignedRect())
        for rect in self.rects:
            region |= QRegion(rect.adjusted(-4, -4, 4, 4).toAlignedRect())
        if self.hidden_amount > 0.95 and not self.free_mode:
            if self.horizontal:
                region |= QRegion(QRect(round(start), cross - 3 if far else 0, round(length), 3))
            else:
                region |= QRegion(QRect(cross - 3 if far else 0, round(start), 3, round(length)))
        if self.hover >= 0 and self.hidden_amount < 0.1:
            region |= QRegion(self.tooltip_rect().toAlignedRect())
        if self.dragging:
            region |= QRegion(QRect(round(self.drag_point.x()) - 30, round(self.drag_point.y()) - 30, 60, 60))
        if region != self.last_mask and QApplication.platformName() != "offscreen":
            self.setMask(region)
            self.last_mask = region

    def hit_test(self, point):
        return next((i for i, rect in enumerate(self.rects) if rect.adjusted(-6, -8, 6, 8).contains(QPointF(point))), -1)

    def animate(self):
        point = self.mapFromGlobal(QCursor.pos())
        inside = self.rect().contains(point) and (self.shelf.adjusted(-8, -16, 8, 16).contains(QPointF(point)) or self.hit_test(point) >= 0)
        edge = self.store.data["position"]
        # The reveal strip is intentionally confined to the dock's own span.
        if self.horizontal:
            reveal = self.shelf.left() <= point.x() <= self.shelf.right() and (point.y() >= self.height() - 3 if edge == "bottom" else point.y() <= 2)
        else:
            reveal = self.shelf.top() <= point.y() <= self.shelf.bottom() and (point.x() >= self.width() - 3 if edge == "right" else point.x() <= 2)
        # A free-floating dock sits away from the edges, so it has no hotspot.
        reveal = reveal and self.rect().contains(point) and not self.free_mode
        now = time.monotonic()
        if reveal and not self.dock_dragging:
            if self.edge_since is None:
                self.edge_since = now
            if not self.edge_latched and now - self.edge_since >= 0.25:
                self.edge_latched = True
                action = self.store.data["edge_action"]
                if action == "reveal":
                    self.reveal()
                elif action == "toggle":
                    self.toggle_visibility()
        else:
            self.edge_since = None
            self.edge_latched = False
        if (inside and self.isVisible() and self.hidden_amount < 0.5) or self.dialog or self.menu_open or self.dragging or self.dock_dragging or self.external_drag:
            self.last_inside = now
        if self.temporary_raise and now - self.last_inside > 0.65:
            self.temporary_raise = False
            self.apply_layer()
        # Without an edge hotspot, auto-hide would leave no way back, so free mode
        # hides only when the user asks through the shortcut, tray or menu.
        auto_hide = self.store.data["auto_hide"] and not self.free_mode
        target = 1.0 if self.manual_hidden or (auto_hide and now - self.last_inside > 0.65) else 0.0
        if self.dialog or self.menu_open or self.dragging or self.dock_dragging or self.external_drag:
            target = 0.0
        old_hidden = self.hidden_amount
        self.hidden_amount += (target - self.hidden_amount) * 0.18
        if abs(target - self.hidden_amount) < 0.002:
            self.hidden_amount = target
        if self.hidden_amount == 1.0 and self.isVisible():
            self.hide()
        elif target == 0 and not self.isVisible():
            self.show()
        old_hover = self.hover
        self.hover = self.hit_test(point) if inside and self.hidden_amount < 0.2 and not self.dialog else -1
        changed = old_hover != self.hover or old_hidden != self.hidden_amount
        for i, scale in enumerate(self.scales):
            if self.dragging or self.external_drag or self.dock_dragging:
                continue
            distance = abs(i - self.hover) if self.hover >= 0 else 100
            factor = math.exp(-(distance * distance) / 1.5)
            wanted = 1 + (self.store.data["magnification"] - 1) * factor
            new = scale + (wanted - scale) * 0.22
            if abs(new - wanted) < 0.002:
                new = wanted
            changed = changed or new != scale
            self.scales[i] = new
        if changed:
            self.layout_icons()
        # The clock and launch pulse also need repainting while the pointer is idle.
        if changed or self.launch_times or int(now) != getattr(self, "last_second", -1):
            self.last_second = int(now)
            self.update()
        self.launch_times = {key: stamp for key, stamp in self.launch_times.items() if now - stamp < 1.3}

    def tooltip_text(self):
        if not 0 <= self.hover < len(self.entries):
            return ""
        kind, app = self.entries[self.hover]
        if kind == "app":
            return app.name
        return {"launcher": "Applications / more apps" if self.overflow else "Applications", "clock": time.strftime("%A, %d %B"), "settings": "Dock preferences"}[kind]

    def tooltip_rect(self):
        if not 0 <= self.hover < len(self.rects):
            return QRectF()
        rect = self.rects[self.hover]
        width = min(260, self.fontMetrics().horizontalAdvance(self.tooltip_text()) + 26)
        if self.horizontal:
            x = min(max(4, rect.center().x() - width / 2), self.width() - width - 4)
            y = rect.top() - 35 if self.anchor == "bottom" else rect.bottom() + 9
            return QRectF(x, y, width, 27)
        # Side docks keep labels inside the window to avoid a screen-wide input area.
        width = min(width, self.width() - 8)
        return QRectF((self.width() - width) / 2, max(4, rect.top() - 33), width, 27)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        top, bottom, accent, text = THEMES.get(self.store.data["theme"], THEMES["midnight"])
        for spread in range(7, 0, -1):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 5 + (7 - spread) * 2))
            painter.drawRoundedRect(self.shelf.adjusted(-spread, -spread / 2, spread, spread), 23 + spread, 23 + spread)
        gradient = QLinearGradient(self.shelf.topLeft(), self.shelf.bottomRight())
        alpha = round(self.store.data["opacity"] * 2.55)
        first, last = QColor(top), QColor(bottom)
        first.setAlpha(alpha)
        last.setAlpha(alpha)
        gradient.setColorAt(0, first)
        gradient.setColorAt(1, last)
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(200, 225, 255, 53), 1))
        painter.drawRoundedRect(self.shelf, 22, 22)
        painter.setPen(QPen(QColor(255, 255, 255, 17), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.shelf.adjusted(2, 2, -2, -2), 20, 20)
        # A small dedicated grip also makes moving an otherwise full dock discoverable.
        painter.setPen(QPen(QColor(185, 209, 238, 90), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.horizontal:
            center = self.shelf.center().x()
            y = self.shelf.bottom() - 4
            painter.drawLine(QPointF(center - 12, y), QPointF(center + 12, y))
        else:
            center = self.shelf.center().y()
            x = self.shelf.right() - 4
            painter.drawLine(QPointF(x, center - 12), QPointF(x, center + 12))

        for i, ((kind, app), rect) in enumerate(zip(self.entries, self.rects)):
            painter.save()
            if i == self.pressed:
                painter.setOpacity(0.65)
            if i == self.hover:
                painter.setPen(Qt.PenStyle.NoPen)
                glow = QColor(accent)
                glow.setAlpha(23)
                painter.setBrush(glow)
                painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 16, 16)
            if kind == "app":
                draw_rect = QRectF(rect)
                elapsed = time.monotonic() - self.launch_times.get(app.id, -100)
                if elapsed < 1.3:
                    bounce = abs(math.sin(elapsed * 11)) * 10 * (1 - elapsed / 1.3)
                    if self.horizontal:
                        draw_rect.translate(0, -bounce if self.anchor == "bottom" else bounce)
                    else:
                        draw_rect.translate(-bounce if self.anchor == "right" else bounce, 0)
                pixmap = self.icons[app.id].pixmap(QSize(round(rect.width() * self.devicePixelRatioF()), round(rect.height() * self.devicePixelRatioF())))
                painter.drawPixmap(draw_rect, pixmap, QRectF(pixmap.rect()))
                if self.anchor == "bottom":
                    painter.save()
                    painter.setOpacity(0.12)
                    reflection = pixmap.transformed(QTransform().scale(1, -1))
                    painter.setClipRect(QRectF(rect.left(), rect.bottom() + 3, rect.width(), 9))
                    painter.drawPixmap(QRectF(rect.left(), rect.bottom() + 3, rect.width(), rect.height()), reflection, QRectF(reflection.rect()))
                    painter.restore()
                matches = [w for w in self.windows if app_matches_window(app, w)]
                if matches:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(accent))
                    count = min(3, len(matches))
                    for dot in range(count):
                        along = (dot - (count - 1) / 2) * 7
                        edge = self.anchor
                        if self.horizontal:
                            center = QPointF(rect.center().x() + along, self.shelf.bottom() - 8 if edge == "bottom" else self.shelf.top() + 8)
                        else:
                            center = QPointF(self.shelf.right() - 8 if edge == "right" else self.shelf.left() + 8, rect.center().y() + along)
                        painter.drawEllipse(center, 2, 2)
            elif kind == "launcher":
                self.draw_brand(painter, rect, accent)
            elif kind == "settings":
                painter.setPen(QPen(QColor(text), max(1.5, rect.width() / 30), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for index, fraction in enumerate((0.32, 0.5, 0.68)):
                    y = rect.top() + rect.height() * fraction
                    painter.drawLine(QPointF(rect.left() + rect.width() * 0.24, y), QPointF(rect.right() - rect.width() * 0.24, y))
                    painter.setBrush(QColor(top))
                    painter.drawEllipse(QPointF(rect.left() + rect.width() * (0.4 if index != 1 else 0.61), y), rect.width() * 0.05, rect.width() * 0.05)
            else:
                painter.setPen(QColor(text))
                font = QFont("Sans Serif")
                font.setPixelSize(max(10, round(rect.width() * 0.24)))
                font.setWeight(QFont.Weight.DemiBold)
                painter.setFont(font)
                painter.drawText(rect.adjusted(-5, 5, 5, -rect.height() * 0.3), Qt.AlignmentFlag.AlignCenter, time.strftime("%H:%M"))
                font.setPixelSize(max(8, round(rect.width() * 0.15)))
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor(accent))
                painter.drawText(rect.adjusted(-5, rect.height() * 0.42, 5, -4), Qt.AlignmentFlag.AlignCenter, time.strftime("%a %d").upper())
            painter.restore()
            if i == 0 or i == len(self.entries) - 3:
                painter.setPen(QPen(QColor(185, 209, 238, 40), 1))
                if self.horizontal:
                    x = rect.right() + 7
                    painter.drawLine(QPointF(x, self.shelf.top() + 20), QPointF(x, self.shelf.bottom() - 20))
                else:
                    y = rect.bottom() + 7
                    painter.drawLine(QPointF(self.shelf.left() + 20, y), QPointF(self.shelf.right() - 20, y))
        if self.drop_index is not None:
            app_rects = [r for (kind, _), r in zip(self.entries, self.rects) if kind == "app"]
            before = app_rects[self.drop_index] if self.drop_index < len(app_rects) else self.rects[-2]
            painter.setPen(QPen(QColor(accent), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if self.horizontal:
                painter.drawLine(QPointF(before.left() - 7, self.shelf.top() + 12), QPointF(before.left() - 7, self.shelf.bottom() - 12))
            else:
                painter.drawLine(QPointF(self.shelf.left() + 12, before.top() - 7), QPointF(self.shelf.right() - 12, before.top() - 7))
        if self.dragging and 0 <= self.pressed < len(self.entries):
            app = self.entries[self.pressed][1]
            ghost = QRectF(self.drag_point.x() - 26, self.drag_point.y() - 26, 52, 52)
            pixmap = self.icons[app.id].pixmap(52, 52)
            painter.drawPixmap(ghost, pixmap, QRectF(pixmap.rect()))
        if self.hover >= 0 and self.hidden_amount < 0.1 and not self.dragging and not self.dock_dragging:
            tooltip = self.tooltip_rect()
            painter.setBrush(QColor("#17253a"))
            painter.setPen(QPen(QColor("#3b516c"), 1))
            painter.drawRoundedRect(tooltip, 8, 8)
            painter.setPen(QColor("#ecf4ff"))
            painter.setFont(self.font())
            title = self.fontMetrics().elidedText(self.tooltip_text(), Qt.TextElideMode.ElideRight, round(tooltip.width() - 18))
            painter.drawText(tooltip, Qt.AlignmentFlag.AlignCenter, title)
        painter.end()

    def draw_brand(self, painter, rect, accent):
        center = rect.center()
        radius = rect.width() * 0.34
        path = QPainterPath()
        path.moveTo(center.x(), center.y() - radius)
        path.lineTo(center.x() + radius, center.y())
        path.lineTo(center.x(), center.y() + radius)
        path.lineTo(center.x() - radius, center.y())
        path.closeSubpath()
        painter.setPen(QPen(QColor(accent), 2))
        fill = QColor(accent)
        fill.setAlpha(22)
        painter.setBrush(fill)
        painter.drawPath(path)
        # Two stacked waves inside the diamond read as "flow" at icon sizes.
        painter.setPen(QPen(QColor(accent), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for direction, height in ((1, -0.30), (-1, 0.18)):
            wave = QPainterPath()
            wave.moveTo(center.x() - radius * 0.44, center.y() + radius * height)
            wave.cubicTo(center.x() - radius * 0.15, center.y() + radius * (height - 0.30 * direction),
                         center.x() + radius * 0.15, center.y() + radius * (height + 0.30 * direction),
                         center.x() + radius * 0.44, center.y() + radius * height)
            painter.drawPath(wave)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed = self.hit_test(event.position())
            self.press_point = event.position().toPoint()
            self.press_global = event.globalPosition().toPoint()
            self.grab_offset = event.position().toPoint()
            grip = (event.position().y() >= self.shelf.bottom() - 8 if self.horizontal else event.position().x() >= self.shelf.right() - 8)
            self.move_candidate = not self.store.data["lock_position"] and (grip or self.pressed < 0 or self.entries[self.pressed][0] == "clock")
            self.update()

    def mouseMoveEvent(self, event):
        moved = (event.globalPosition().toPoint() - self.press_global).manhattanLength() > QApplication.startDragDistance()
        if self.move_candidate and moved:
            self.dock_dragging = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.drag_dock(event.globalPosition().toPoint())
        elif self.pressed >= 0 and moved:
            kind, app = self.entries[self.pressed]
            self.dragging = kind == "app"
            if self.dragging:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_point = event.position()
                self.drop_index = self.insertion_index(event.position())
                self.layout_icons()
                self.update()

    def drag_dock(self, global_point):
        (self.move_free if self.free_mode else self.move_to_edge)(global_point)

    def move_free(self, global_point):
        """Follow the pointer, keeping the grabbed point of the shelf beneath it."""
        screen = QApplication.screenAt(global_point) or self.selected_screen()
        geometry = screen.geometry()
        self.store.data["screen"] = QApplication.screens().index(screen)
        self.hidden_amount = 0
        self.hover = -1
        depth = self.height() if self.horizontal else self.width()
        pad = depth - self.shelf_depth - 9 if self.anchor in ("bottom", "right") else 9
        top_left = global_point - self.grab_offset
        # The cross axis tracks the shelf so it can reach an edge; the along axis
        # tracks the window, which already spans the dock end to end.
        along = ("free_x", top_left.x() - geometry.x(), geometry.width() - self.width())
        cross = ("free_y", top_left.y() + pad - geometry.y(), geometry.height() - self.shelf_depth)
        if not self.horizontal:
            along = ("free_y", top_left.y() - geometry.y(), geometry.height() - self.height())
            cross = ("free_x", top_left.x() + pad - geometry.x(), geometry.width() - self.shelf_depth)
        for key, value, span in (along, cross):
            self.store.data[key] = min(1, max(0, value / span)) if span > 0 else 0.5
        self.reposition()

    def move_to_edge(self, global_point):
        screen = QApplication.screenAt(global_point) or self.selected_screen()
        geometry = screen.geometry()
        distances = {"left": abs(global_point.x() - geometry.left()), "right": abs(global_point.x() - geometry.right()),
                     "top": abs(global_point.y() - geometry.top()), "bottom": abs(global_point.y() - geometry.bottom())}
        self.store.data["position"] = min(distances, key=distances.get)
        self.store.data["screen"] = QApplication.screens().index(screen)
        self.entries = self.make_entries()
        self.scales = [1.0] * len(self.entries)
        self.icons = {app.id: app_icon(app) for kind, app in self.entries if kind == "app"}
        self.hidden_amount = 0
        self.hover = -1
        self.reposition()
        extent = geometry.width() - self.width() if self.horizontal else geometry.height() - self.height()
        offset = global_point.x() - geometry.x() - self.width() / 2 if self.horizontal else global_point.y() - geometry.y() - self.height() / 2
        self.store.data["alignment"] = max(0, min(1, offset / extent)) if extent > 0 else 0.5
        self.reposition()

    def insertion_index(self, point):
        coordinate = point.x() if self.horizontal else point.y()
        return sum(1 for (kind, app), rect in zip(self.entries, self.rects)
                   if kind == "app" and app.id in self.store.data["pinned"]
                   and coordinate >= (rect.center().x() if self.horizontal else rect.center().y()))

    def insert_apps(self, app_ids, index):
        pins = self.store.data["pinned"]
        selected = list(dict.fromkeys(key for key in app_ids if key in self.catalog))
        index -= sum(1 for key in pins[:index] if key in selected)
        pins[:] = [key for key in pins if key not in selected]
        pins[index:index] = selected
        self.save_settings()
        self.rebuild()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self.hit_test(event.position())
        source = self.pressed
        self.pressed = -1
        self.move_candidate = False
        if self.dock_dragging:
            self.dock_dragging = False
            self.unsetCursor()
            self.save_settings()
            self.rebuild()
        elif self.dragging:
            self.dragging = False
            self.unsetCursor()
            if source >= 0:
                app = self.entries[source][1]
                if self.shelf.adjusted(-20, -40, 20, 40).contains(event.position()):
                    self.insert_apps([app.id], self.insertion_index(event.position()))
                elif app.id in self.store.data["pinned"]:
                    self.toggle_pin(app.id)
            self.drop_index = None
            self.rebuild()
        elif target == source and source >= 0:
            kind, app = self.entries[source]
            if kind == "app":
                matches = [w for w in self.windows if app_matches_window(app, w)]
                if matches and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.run_worker(activate_window, matches[-1].id)
                else:
                    self.launch(app)
            elif kind == "launcher":
                self.open_picker()
            elif kind == "settings":
                self.open_settings()
            elif kind == "clock":
                self.open_menu(event.globalPosition().toPoint())
        self.update()

    def contextMenuEvent(self, event):
        self.open_menu(event.globalPos(), self.hit_test(event.pos()))

    def add_panel_menu(self, menu):
        """Offer the desktop's own panel controls, greyed out where unsupported."""
        controller = self.panel
        submenu = menu.addMenu(controller.name)
        hidden = controller.hidden() if controller.can_hide else None
        toggle = submenu.addAction(
            "Show panel" if hidden else "Hide panel",
            lambda: self.panel_action(controller.set_hidden, not hidden))
        toggle.setEnabled(controller.can_hide)
        if controller.can_hide and hidden is not None:
            toggle.setCheckable(True)
            toggle.setChecked(bool(hidden))
        moves = submenu.addMenu("Move panel")
        moves.setEnabled(controller.can_move)
        for edge in desktop_panel.EDGES:
            moves.addAction(edge.title(), lambda checked=False, edge=edge:
                            self.panel_action(controller.move, edge))
        settings = submenu.addAction("Panel settings...",
                                     lambda: self.panel_action(controller.open_settings))
        settings.setEnabled(controller.can_settings)
        if not (controller.can_hide or controller.can_move or controller.can_settings):
            note = submenu.addAction(controller.reason[:70])
            note.setEnabled(False)

    def panel_action(self, function, *args):
        """Panel tools can block briefly, so run them off the animation thread."""
        self.run_worker(lambda: function(*args), finished=self.panel_finished,
                        failed=self.show_error)

    def panel_finished(self, message):
        if message:
            self.show_error(message)

    def set_move_mode(self, mode):
        self.store.data["move_mode"] = mode
        self.save_settings()
        self.rebuild()
        self.reveal()

    def open_menu(self, position, index=-1):
        menu = QMenu(self)
        menu.setStyleSheet(DIALOG_STYLE)
        if index >= 0 and self.entries[index][0] == "app":
            app = self.entries[index][1]
            heading = menu.addAction(app.name)
            heading.setEnabled(False)
            menu.addAction("Launch new instance", lambda: self.launch(app))
            pinned = app.id in self.store.data["pinned"]
            menu.addAction("Unpin from dock" if pinned else "Pin to dock", lambda: self.toggle_pin(app.id))
            for window in [w for w in self.windows if app_matches_window(app, w)]:
                menu.addAction(window.title[:65] or "Activate window", lambda checked=False, key=window.id: self.run_worker(activate_window, key))
            menu.addSeparator()
        menu.addAction("Applications...", self.open_picker)
        menu.addAction("Preferences...", self.open_settings)
        menu.addAction("Refresh applications", self.refresh_apps)
        menu.addAction("Hide dock", self.toggle_visibility)
        edges = menu.addMenu("Screen edge")
        for edge in ("bottom", "top", "left", "right"):
            action = edges.addAction(edge.title(), lambda checked=False, edge=edge: self.set_edge(edge))
            action.setCheckable(True)
            action.setChecked(not self.free_mode and self.store.data["position"] == edge)
        edges.addSeparator()
        free = edges.addAction("Free / place anywhere", lambda: self.set_move_mode("free"))
        free.setCheckable(True)
        free.setChecked(self.free_mode)
        layers = menu.addMenu("Screen layer")
        for title, layer in (("Normal", "normal"), ("Above windows", "above"), ("Below windows", "below")):
            action = layers.addAction(title, lambda checked=False, layer=layer: self.change_layer(layer))
            action.setCheckable(True)
            action.setChecked(self.store.data["layer"] == layer)
        self.add_panel_menu(menu)
        menu.addSeparator()
        menu.addAction("Quit FlowDocks", QApplication.quit)
        self.menu_open = True
        menu.exec(position)
        self.menu_open = False
        self.last_inside = time.monotonic()
        menu.deleteLater()

    def change_layer(self, layer):
        self.store.data["layer"] = layer
        self.temporary_raise = False
        self.save_settings()
        self.apply_layer()

    def launch(self, app):
        if time.monotonic() - self.launch_times.get(app.id, -100) < 0.5:
            return
        self.launch_times[app.id] = time.monotonic()
        self.run_worker(launch_app, app, failed=self.show_error)

    def show_error(self, message):
        if self.tray.isVisible():
            self.tray.showMessage("FlowDocks", message, QSystemTrayIcon.MessageIcon.Warning, 6000)
        else:
            self.menu_open = True
            QMessageBox.warning(self, "FlowDocks", message)
            self.menu_open = False

    def save_settings(self):
        if not self.smoke_test:
            self.store.save()

    def toggle_pin(self, app_id):
        pins = self.store.data["pinned"]
        if app_id in pins:
            pins.remove(app_id)
        elif app_id in self.catalog:
            pins.append(app_id)
        self.save_settings()
        self.rebuild()

    def refresh_apps(self):
        self.run_worker(discover_apps, finished=self.apps_received, failed=self.show_error)

    def apps_received(self, apps):
        self.apps = apps
        self.catalog = {app.id: app for app in apps}
        self.rebuild()
        if isinstance(self.dialog, AppPicker):
            self.dialog.populate()

    def show_dialog(self, dialog_type):
        self.reveal()
        if self.dialog:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.hidden_amount = 0
        self.dialog = dialog_type(self)
        self.dialog.finished.connect(self.dialog_closed)
        screen = self.selected_screen().availableGeometry()
        self.dialog.move(screen.center() - self.dialog.rect().center())
        self.dialog.show()
        self.dialog.activateWindow()

    def dialog_closed(self, _):
        self.dialog.deleteLater()
        self.dialog = None
        self.last_inside = time.monotonic()

    def open_picker(self):
        self.show_dialog(AppPicker)

    def open_settings(self):
        self.show_dialog(SettingsDialog)

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings()

    def dropped_apps(self, mime):
        if mime.hasFormat(APP_MIME):
            app_id = bytes(mime.data(APP_MIME)).decode("utf-8", errors="replace")
            return [app_id] if app_id in self.catalog else []
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        # Only installed apps are accepted, not arbitrary downloaded launchers.
        return [app.id for app in resolve_dropped_desktops(paths, self.apps)]

    def dragEnterEvent(self, event):
        if self.dropped_apps(event.mimeData()):
            self.external_drag = True
            self.reveal()
            self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if self.dropped_apps(event.mimeData()):
            self.drop_index = self.insertion_index(event.position())
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.update()

    def dragLeaveEvent(self, event):
        self.external_drag = False
        self.drop_index = None
        event.accept()
        self.update()

    def dropEvent(self, event):
        app_ids = self.dropped_apps(event.mimeData())
        self.external_drag = False
        self.drop_index = None
        if not app_ids:
            event.ignore()
            return
        self.insert_apps(app_ids, self.insertion_index(event.position()))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
