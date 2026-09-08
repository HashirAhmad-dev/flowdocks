"""Offscreen interaction and rendering tests; no real app launches or config writes."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, QMimeData, QUrl
from PyQt6.QtGui import QCursor, QDragEnterEvent, QDropEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

from flowdocks import panel
from flowdocks.backend import (
    MAX_DOCKS, PATH_PIN_PREFIX, SEPARATOR, DesktopApp, SettingsStore, WindowInfo,
)
from flowdocks.ui import APP_MIME, DockManager, AppPicker, Dock, SettingsDialog, limit_to_one_combination


class DockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.apps = [
            DesktopApp("browser.desktop", "Browser", "browser", startup_wm_class="Browser"),
            DesktopApp("terminal.desktop", "Terminal", "terminal"),
            DesktopApp("editor.desktop", "Editor", "editor"),
        ]
        self.store = SettingsStore(Path(self.directory.name))
        self.settings = self.store.dock(0)
        self.settings.data["pinned"] = [app.id for app in self.apps[:2]]
        self.settings.data["icon_size"] = 48
        with patch("flowdocks.ui.discover_apps", return_value=self.apps), patch.object(Dock, "poll_windows"):
            self.dock = Dock(self.settings, smoke_test=True)
        self.dock.timer.stop()
        self.dock.poll_timer.stop()
        self.dock.show()
        self.qt.processEvents()
        self.addCleanup(self.cleanup_dock)

    def cleanup_dock(self):
        if self.dock.dialog:
            self.dock.dialog.close()
        self.dock.pool.waitForDone()
        self.dock.close()
        self.dock.deleteLater()
        self.qt.processEvents()

    def test_render_and_geometry_on_all_edges(self):
        for edge in ("bottom", "top", "left", "right"):
            for theme in ("midnight", "graphite", "aurora"):
                with self.subTest(edge=edge, theme=theme):
                    self.settings.data.update(position=edge, theme=theme)
                    self.dock.rebuild()
                    self.assertTrue(self.dock.selected_screen().geometry().contains(self.dock.geometry()))
                    self.assertFalse(self.dock.grab().isNull())
                    for index, rect in enumerate(self.dock.rects):
                        self.assertEqual(self.dock.hit_test(rect.center()), index)

    def test_click_launch_and_shift_click_running_app(self):
        point = self.dock.rects[1].center().toPoint()
        with patch.object(self.dock, "launch") as launch:
            QTest.mouseClick(self.dock, Qt.MouseButton.LeftButton, pos=point)
            launch.assert_called_once_with(self.apps[0])
        self.dock.windows = [WindowInfo("0x123", "Browser window", "Browser")]
        with patch.object(self.dock, "run_worker") as worker, patch.object(self.dock, "launch") as launch:
            QTest.mouseClick(self.dock, Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(worker.call_args.args[1], "0x123")
            launch.assert_not_called()
            QTest.mouseClick(self.dock, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point)
            launch.assert_called_once_with(self.apps[0])

    def test_pinning_and_running_app_entries(self):
        self.dock.toggle_pin("editor.desktop")
        self.assertIn("editor.desktop", self.settings.data["pinned"])
        self.dock.toggle_pin("editor.desktop")
        self.assertNotIn("editor.desktop", self.settings.data["pinned"])
        self.dock.windows_received([WindowInfo("0x9", "Notes", "editor")])
        self.assertIn("editor.desktop", [app.id for kind, app in self.dock.entries if kind == "app"])
        self.dock.windows_received([])
        self.assertNotIn("editor.desktop", [app.id for kind, app in self.dock.entries if kind == "app"])
        self.assertFalse(self.store.path.exists())

    def test_drag_reorders_pins(self):
        first = self.dock.rects[1].center().toPoint()
        second = self.dock.rects[2].center().toPoint()
        QTest.mousePress(self.dock, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(self.dock, second)
        QTest.mouseRelease(self.dock, Qt.MouseButton.LeftButton, pos=second)
        self.assertEqual(self.settings.data["pinned"], ["terminal.desktop", "browser.desktop"])

    def test_picker_search_and_pin(self):
        picker = AppPicker(self.dock)
        self.addCleanup(picker.deleteLater)
        picker.search.setText("editor")
        self.assertEqual(picker.list.count(), 1)
        picker.toggle_pin()
        self.assertIn("editor.desktop", self.settings.data["pinned"])
        picker.search.setText("no such application")
        self.assertEqual(picker.list.count(), 0)
        self.assertFalse(picker.open.isEnabled())
        self.assertFalse(picker.pin.isEnabled())

    def test_settings_apply_and_smoke_test_no_autostart_write(self):
        settings = SettingsDialog(self.dock)
        self.addCleanup(settings.deleteLater)
        settings.size.setValue(64)
        settings.position.setCurrentIndex(settings.position.findData("left"))
        settings.hide.setChecked(True)
        settings.autostart.setChecked(True)
        with patch("flowdocks.ui.set_autostart") as autostart:
            settings.apply()
            autostart.assert_not_called()
        self.assertEqual(self.settings.data["icon_size"], 64)
        self.assertEqual(self.settings.data["position"], "left")
        self.assertTrue(self.settings.data["auto_hide"])

    def test_auto_hide_and_edge_reveal(self):
        self.settings.data["auto_hide"] = True
        with patch.object(QCursor, "pos", return_value=QPoint(-1000, -1000)):
            self.dock.last_inside = time.monotonic() - 2
            for _ in range(45):
                self.dock.animate()
        self.assertEqual(self.dock.hidden_amount, 1.0)
        edge = self.dock.mapToGlobal(QPoint(self.dock.width() // 2, self.dock.height() - 1))
        with patch.object(QCursor, "pos", return_value=edge):
            self.dock.animate()
            self.dock.edge_since = time.monotonic() - 0.3
            self.dock.animate()
        self.assertLess(self.dock.hidden_amount, 1)

    def test_edge_disabled_and_toggle_latches(self):
        self.settings.data["edge_action"] = "off"
        self.dock.manual_hidden = True
        self.dock.hidden_amount = 1
        self.dock.layout_icons()
        self.dock.hide()
        edge = self.dock.mapToGlobal(QPoint(self.dock.width() // 2, self.dock.height() - 1))
        with patch.object(QCursor, "pos", return_value=edge):
            self.dock.edge_since = time.monotonic() - 1
            self.dock.animate()
            self.assertTrue(self.dock.manual_hidden)
            self.settings.data["edge_action"] = "toggle"
            self.dock.edge_latched = False
            self.dock.animate()
            self.assertFalse(self.dock.manual_hidden)
            for _ in range(50):
                self.dock.animate()
            self.assertFalse(self.dock.manual_hidden)
        with patch.object(QCursor, "pos", return_value=QPoint(-100, -100)):
            self.dock.animate()
        with patch.object(QCursor, "pos", return_value=edge):
            self.dock.edge_since = time.monotonic() - 1
            self.dock.animate()
            self.assertTrue(self.dock.manual_hidden)

    def test_toggle_hides_without_reappearing_under_pointer(self):
        point = self.dock.mapToGlobal(self.dock.rects[1].center().toPoint())
        self.dock.toggle_visibility()
        with patch.object(QCursor, "pos", return_value=point):
            for _ in range(50):
                self.dock.animate()
        self.assertTrue(self.dock.manual_hidden)
        self.assertFalse(self.dock.isVisible())
        self.dock.toggle_visibility()
        self.assertFalse(self.dock.manual_hidden)
        self.assertTrue(self.dock.isVisible())

    def test_normal_layer_default_and_temporary_raise_restores(self):
        self.assertEqual(self.settings.data["layer"], "normal")
        self.assertFalse(self.dock.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        with patch.object(self.dock, "apply_layer") as apply:
            self.dock.reveal()
            self.assertTrue(self.dock.temporary_raise)
            self.dock.last_inside = time.monotonic() - 2
            with patch.object(QCursor, "pos", return_value=QPoint(-100, -100)):
                self.dock.animate()
            self.assertFalse(self.dock.temporary_raise)
            self.assertGreaterEqual(apply.call_count, 2)
        self.dock.change_layer("below")
        self.assertEqual(self.settings.data["layer"], "below")

    def test_drag_from_picker_mime_and_drop_position(self):
        mime = QMimeData()
        mime.setData(APP_MIME, b"editor.desktop")
        point = self.dock.rects[1].topLeft().toPoint()
        enter = QDragEnterEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.dock.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertIsNotNone(self.dock.drop_index)
        event = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.dock.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.settings.data["pinned"], ["editor.desktop", "browser.desktop", "terminal.desktop"])
        self.assertFalse(self.dock.external_drag)

    def test_external_drop_copies_not_moves_and_deduplicates(self):
        self.apps[2].path = str(Path(self.directory.name) / "editor.desktop")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.apps[2].path)])
        point = self.dock.rects[-2].center()
        for _ in range(2):
            event = QDropEvent(point, Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.dock.dropEvent(event)
            self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.assertEqual(self.settings.data["pinned"].count("editor.desktop"), 1)

    def test_drag_outside_unpins_without_launch(self):
        first = self.dock.rects[1].center().toPoint()
        outside = QPoint(self.dock.width() + 100, -100)
        with patch.object(self.dock, "launch") as launch:
            QTest.mousePress(self.dock, Qt.MouseButton.LeftButton, pos=first)
            QTest.mouseMove(self.dock, outside)
            QTest.mouseRelease(self.dock, Qt.MouseButton.LeftButton, pos=outside)
            launch.assert_not_called()
        self.assertNotIn("browser.desktop", self.settings.data["pinned"])

    def test_move_dock_snaps_and_retains_alignment(self):
        geometry = self.dock.selected_screen().geometry()
        for edge, point in (("left", QPoint(geometry.left(), geometry.center().y())),
                            ("top", QPoint(geometry.center().x(), geometry.top())),
                            ("right", QPoint(geometry.right(), geometry.center().y())),
                            ("bottom", QPoint(geometry.center().x(), geometry.bottom()))):
            self.dock.move_to_edge(point)
            self.assertEqual(self.settings.data["position"], edge)
            self.assertTrue(geometry.contains(self.dock.geometry()))
            self.assertGreaterEqual(self.settings.data["alignment"], 0)
            self.assertLessEqual(self.settings.data["alignment"], 1)

    def test_clock_drag_and_position_lock(self):
        self.settings.data["lock_position"] = True
        clock = self.dock.rects[-2].center().toPoint()
        QTest.mousePress(self.dock, Qt.MouseButton.LeftButton, pos=clock)
        self.assertFalse(self.dock.move_candidate)
        self.dock.pressed = -1
        self.settings.data["lock_position"] = False
        QTest.mousePress(self.dock, Qt.MouseButton.LeftButton, pos=clock)
        self.assertTrue(self.dock.move_candidate)
        with patch.object(self.dock, "move_to_edge") as move, patch.object(self.dock, "open_menu") as menu:
            QTest.mouseMove(self.dock, clock + QPoint(50, 0))
            self.assertTrue(self.dock.dock_dragging)
            QTest.mouseRelease(self.dock, Qt.MouseButton.LeftButton, pos=clock + QPoint(50, 0))
            move.assert_called()
            menu.assert_not_called()

    def test_failed_shortcut_does_not_save_preferences(self):
        settings = SettingsDialog(self.dock)
        self.addCleanup(settings.deleteLater)
        settings.size.setValue(72)
        with patch.object(self.dock, "configure_shortcut", return_value=False):
            settings.apply()
        self.assertEqual(self.settings.data["icon_size"], 48)

    def test_hover_magnifies(self):
        point = self.dock.mapToGlobal(self.dock.rects[1].center().toPoint())
        with patch.object(QCursor, "pos", return_value=point):
            for _ in range(10):
                self.dock.animate()
        self.assertGreater(self.dock.scales[1], 1.1)

    def test_only_catalogued_desktop_files_can_be_dropped(self):
        self.apps[0].path = str(Path(self.directory.name) / "browser.desktop")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("/tmp/untrusted.desktop")])
        self.assertEqual(self.dock.dropped_apps(mime), [])
        mime.setUrls([QUrl.fromLocalFile(self.apps[0].path)])
        self.assertEqual(self.dock.dropped_apps(mime), ["browser.desktop"])

    def test_drop_rescans_for_app_installed_after_startup(self):
        late = Path(self.directory.name) / "late.desktop"
        late.write_text("[Desktop Entry]\nType=Application\nName=Late\nExec=late\n")
        app = DesktopApp("late.desktop", "Late", "late", path=str(late))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(late))])
        self.assertEqual(self.dock.dropped_apps(mime), [])
        point = self.dock.rects[-2].center()
        with patch("flowdocks.ui.discover_apps", return_value=[*self.apps, app]):
            event = QDropEvent(point, Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.dock.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertIn("late.desktop", self.settings.data["pinned"])

    def test_drop_of_missing_path_is_rejected_without_pinning(self):
        pinned = list(self.settings.data["pinned"])
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(Path(self.directory.name) / "gone-xyz"))])
        point = self.dock.rects[-2].center()
        with patch.object(self.dock, "show_error"):
            event = QDropEvent(point, Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.dock.dropEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(self.settings.data["pinned"], pinned)

    def test_drop_folder_pins_it_and_renders(self):
        folder = Path(self.directory.name) / "Reports"
        folder.mkdir()
        token = PATH_PIN_PREFIX + str(folder)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(folder))])
        point = self.dock.rects[-2].center()
        event = QDropEvent(point, Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.dock.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertIn(token, self.settings.data["pinned"])
        kinds = [(kind, getattr(obj, "name", None)) for kind, obj in self.dock.entries]
        self.assertIn(("path", "Reports"), kinds)
        self.assertIn(token, self.dock.icons)

    def test_clicking_a_path_pin_opens_it_via_backend(self):
        folder = Path(self.directory.name) / "Docs"
        folder.mkdir()
        self.settings.data["pinned"].append(PATH_PIN_PREFIX + str(folder))
        self.dock.rebuild()
        pin = next(obj for kind, obj in self.dock.entries if kind == "path")
        with patch("flowdocks.ui.open_path") as opener:
            self.dock.open_pin(pin)
            self.dock.pool.waitForDone()
        opener.assert_called_once_with(str(folder))

    def test_unresolvable_path_pin_is_dropped_from_entries(self):
        self.settings.data["pinned"].append(PATH_PIN_PREFIX + "/no/such/place/xyz")
        self.dock.rebuild()
        self.assertNotIn("path", [kind for kind, _ in self.dock.entries])

    def test_overflow_keeps_geometry_on_screen(self):
        self.dock.apps = [DesktopApp(f"app{i}.desktop", f"App {i}", f"app{i}") for i in range(100)]
        self.dock.catalog = {app.id: app for app in self.dock.apps}
        self.settings.data["pinned"] = list(self.dock.catalog)
        self.dock.rebuild()
        self.assertTrue(self.dock.overflow)
        self.assertLess(len(self.dock.entries), 100)
        self.assertTrue(self.dock.selected_screen().geometry().contains(self.dock.geometry()))
        self.assertEqual(len(self.settings.data["pinned"]), 100)

    def shelf_on_screen(self):
        """The shelf is what the user sees and positions; its window is padded."""
        return QRect(self.dock.mapToGlobal(self.dock.shelf.topLeft().toPoint()),
                     self.dock.shelf.size().toSize())

    def test_free_mode_places_the_dock_where_it_is_asked(self):
        self.settings.data.update(move_mode="free", free_x=0.25, free_y=0.4)
        self.dock.rebuild()
        screen = self.dock.selected_screen().geometry()
        self.assertTrue(screen.contains(self.shelf_on_screen()))
        self.assertTrue(self.dock.horizontal, "a free dock defaults to a horizontal bar")
        self.assertAlmostEqual(self.shelf_on_screen().y() - screen.y(),
                               round((screen.height() - self.dock.shelf_depth) * 0.4), delta=1)
        self.assertFalse(self.dock.grab().isNull())

    def test_free_mode_reaches_the_edge_it_faces(self):
        # The padded window used to hold the shelf a tooltip's height away from the
        # top edge. Across its thickness the shelf must now sit flush; along its
        # length the window keeps its magnification headroom on screen, so a
        # hover-grown shelf cannot spill off, and a small inset is expected there.
        self.settings.data["move_mode"] = "free"
        for orientation in ("horizontal", "vertical"):
            self.settings.data["orientation"] = orientation
            for free_x, free_y in ((0, 0), (1, 1), (0, 1), (1, 0)):
                with self.subTest(orientation=orientation, x=free_x, y=free_y):
                    self.settings.data.update(free_x=free_x, free_y=free_y)
                    self.dock.rebuild()
                    screen = self.dock.selected_screen().geometry()
                    shelf = self.shelf_on_screen()
                    self.assertTrue(screen.contains(shelf), f"{shelf} outside {screen}")
                    self.assertTrue(screen.contains(self.dock.geometry())
                                    or self.dock.free_mode, "window may overhang, shelf may not")
                    if orientation == "horizontal":
                        gap = (shelf.y() - screen.y()) if free_y == 0 else (screen.bottom() - shelf.bottom())
                    else:
                        gap = (shelf.x() - screen.x()) if free_x == 0 else (screen.right() - shelf.right())
                    self.assertLessEqual(abs(gap), 2, f"{gap}px from the edge it faces")

    def test_free_rotation_switches_between_bar_and_column(self):
        self.settings.data.update(move_mode="free", orientation="vertical", free_x=0.5, free_y=0.5)
        self.dock.rebuild()
        self.assertFalse(self.dock.horizontal)
        column = self.dock.geometry()
        self.assertGreater(column.height(), column.width())
        self.assertFalse(self.dock.grab().isNull())
        for index, rect in enumerate(self.dock.rects):
            self.assertEqual(self.dock.hit_test(rect.center()), index)
        self.settings.data["orientation"] = "horizontal"
        self.dock.rebuild()
        self.assertTrue(self.dock.horizontal)
        self.assertGreater(self.dock.geometry().width(), self.dock.geometry().height())

    def test_edge_mode_takes_its_orientation_from_the_edge(self):
        self.settings.data.update(move_mode="edge", orientation="vertical", position="bottom")
        self.dock.rebuild()
        self.assertTrue(self.dock.horizontal, "the edge wins over the rotation setting")
        self.settings.data["position"] = "left"
        self.dock.rebuild()
        self.assertFalse(self.dock.horizontal)

    def test_preferences_orientation_follows_the_edge_then_frees_up(self):
        settings = SettingsDialog(self.dock)
        self.addCleanup(settings.deleteLater)
        self.assertFalse(settings.orientation.isEnabled(), "an edge dock is not rotatable")
        settings.position.setCurrentIndex(settings.position.findData("left"))
        self.assertEqual(settings.orientation.currentData(), "vertical")
        settings.move_mode.setCurrentIndex(settings.move_mode.findData("free"))
        self.assertTrue(settings.orientation.isEnabled())
        settings.orientation.setCurrentIndex(settings.orientation.findData("horizontal"))
        settings.apply()
        self.assertEqual(self.settings.data["orientation"], "horizontal")

    def test_free_drag_keeps_the_shelf_on_screen(self):
        self.settings.data["move_mode"] = "free"
        self.dock.rebuild()
        screen = self.dock.selected_screen().geometry()
        self.dock.grab_offset = QPoint(30, 20)
        self.dock.move_free(QPoint(screen.center().x(), screen.center().y()))
        self.assertTrue(screen.contains(self.shelf_on_screen()))
        # Dragging far past a corner clamps instead of leaving the screen.
        self.dock.move_free(QPoint(screen.right() + 900, screen.bottom() + 900))
        self.assertTrue(screen.contains(self.shelf_on_screen()))
        self.assertEqual((self.settings.data["free_x"], self.settings.data["free_y"]), (1, 1))
        self.dock.move_free(QPoint(screen.left() - 900, screen.top() - 900))
        self.assertTrue(screen.contains(self.shelf_on_screen()))
        self.assertEqual((self.settings.data["free_x"], self.settings.data["free_y"]), (0, 0))

    def test_free_mode_ignores_auto_hide_and_the_edge_hotspot(self):
        self.settings.data.update(move_mode="free", auto_hide=True, edge_action="toggle")
        self.dock.rebuild()
        edge = self.dock.mapToGlobal(QPoint(self.dock.width() // 2, self.dock.height() - 1))
        with patch.object(QCursor, "pos", return_value=edge):
            self.dock.last_inside = time.monotonic() - 5
            self.dock.edge_since = time.monotonic() - 5
            for _ in range(60):
                self.dock.animate()
        self.assertEqual(self.dock.hidden_amount, 0.0, "a free dock must not hide itself")
        self.assertTrue(self.dock.isVisible())
        # An explicit toggle still works, and is the only way to hide it.
        self.dock.toggle_visibility()
        for _ in range(60):
            self.dock.animate()
        self.assertFalse(self.dock.isVisible())

    def test_switching_modes_from_the_menu(self):
        self.dock.set_move_mode("free")
        self.assertTrue(self.dock.free_mode)
        self.dock.set_edge("left")
        self.assertFalse(self.dock.free_mode)
        self.assertEqual(self.settings.data["position"], "left")

    def test_preferences_disables_edge_controls_in_free_mode(self):
        settings = SettingsDialog(self.dock)
        self.addCleanup(settings.deleteLater)
        settings.move_mode.setCurrentIndex(settings.move_mode.findData("free"))
        self.assertFalse(settings.position.isEnabled())
        self.assertFalse(settings.edge_action.isEnabled())
        self.assertFalse(settings.hide.isEnabled())
        settings.apply()
        self.assertEqual(self.settings.data["move_mode"], "free")

    def test_panel_menu_reflects_capabilities(self):
        for desktop, expected in (("xfce", True), ("gnome", False)):
            with self.subTest(desktop=desktop):
                self.dock.panel = panel.controller(desktop)
                menu = QMenu()
                with patch.object(self.dock.panel, "hidden", return_value=False):
                    self.dock.add_panel_menu(menu)
                submenu = menu.actions()[0].menu()
                labels = {a.text(): a.isEnabled() for a in submenu.actions()}
                hide = next(text for text in labels if "panel" in text.lower() and "Move" not in text)
                self.assertEqual(labels[hide], expected)
                self.assertTrue(any("Move panel" in text for text in labels))
                menu.deleteLater()

    def test_panel_actions_run_off_the_animation_thread(self):
        calls = []
        self.dock.panel_action(lambda edge: calls.append(edge) or "", "top")
        self.dock.pool.waitForDone()
        self.qt.processEvents()
        self.assertEqual(calls, ["top"])
        with patch.object(self.dock, "show_error") as error:
            self.dock.panel_action(lambda: "xfconf-query is not installed.")
            self.dock.pool.waitForDone()
            self.qt.processEvents()
            error.assert_called_once()

    def test_shortcut_editor_tolerates_pyqt_without_the_qt65_api(self):
        # setMaximumSequenceLength arrived in Qt 6.5; Debian 12 and Ubuntu 24.04
        # ship PyQt6 6.4, where calling it unconditionally broke preferences.
        class Older:
            pass

        class Newer:
            def __init__(self):
                self.limit = None

            def setMaximumSequenceLength(self, value):
                self.limit = value

        limit_to_one_combination(Older())
        newer = Newer()
        limit_to_one_combination(newer)
        self.assertEqual(newer.limit, 1)
        settings = SettingsDialog(self.dock)
        self.addCleanup(settings.deleteLater)
        self.assertTrue(settings.shortcut.keySequence().toString())

    def pinned_kinds(self):
        return [kind for kind, _ in self.dock.entries if kind in ("app", "separator")]

    def test_separators_appear_between_icons(self):
        self.settings.data["pinned"] = ["browser.desktop", SEPARATOR, "terminal.desktop"]
        self.dock.rebuild()
        self.assertEqual(self.pinned_kinds(), ["app", "separator", "app"])
        self.assertEqual([s for s in self.dock.slots if s is not None], [0, 1, 2])
        self.assertFalse(self.dock.grab().isNull())
        for index, rect in enumerate(self.dock.rects):
            self.assertEqual(self.dock.hit_test(rect.center()), index)

    def test_separator_is_narrower_than_an_icon_and_never_magnifies(self):
        self.settings.data["pinned"] = ["browser.desktop", SEPARATOR, "terminal.desktop"]
        self.dock.rebuild()
        divider = self.pinned_kinds().index("separator") + 1
        icon = self.dock.rects[1]
        self.assertLess(self.dock.rects[divider].width(), icon.width())
        point = self.dock.mapToGlobal(self.dock.rects[divider].center().toPoint())
        with patch.object(QCursor, "pos", return_value=point):
            for _ in range(15):
                self.dock.animate()
        self.assertEqual(self.dock.scales[divider], 1.0)

    def test_identical_separators_are_removed_individually(self):
        # Separators share a value, so they must be addressed by slot, never by
        # searching the pinned list, or removing one would remove the first.
        self.settings.data["pinned"] = ["browser.desktop", SEPARATOR,
                                     "terminal.desktop", SEPARATOR, "editor.desktop"]
        self.dock.rebuild()
        second = [i for i, (kind, _) in enumerate(self.dock.entries) if kind == "separator"][1]
        self.dock.remove_pin(self.dock.slots[second])
        self.assertEqual(self.settings.data["pinned"],
                         ["browser.desktop", SEPARATOR, "terminal.desktop", "editor.desktop"])

    def test_separator_can_be_added_and_dragged_to_a_new_place(self):
        self.settings.data["pinned"] = ["browser.desktop", "terminal.desktop"]
        self.dock.rebuild()
        self.dock.add_separator()
        self.assertEqual(self.settings.data["pinned"],
                         ["browser.desktop", "terminal.desktop", SEPARATOR])
        self.dock.add_separator(1)
        self.assertEqual(self.settings.data["pinned"],
                         ["browser.desktop", SEPARATOR, "terminal.desktop", SEPARATOR])
        # Drag the trailing separator to the front.
        self.dock.move_pin(3, 0)
        self.assertEqual(self.settings.data["pinned"],
                         [SEPARATOR, "browser.desktop", SEPARATOR, "terminal.desktop"])

    def test_dragging_a_separator_off_the_dock_removes_only_it(self):
        self.settings.data["pinned"] = ["browser.desktop", SEPARATOR, "terminal.desktop"]
        self.dock.rebuild()
        divider = self.pinned_kinds().index("separator") + 1
        start = self.dock.rects[divider].center().toPoint()
        outside = QPoint(self.dock.width() + 120, -120)
        QTest.mousePress(self.dock, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.dock, outside)
        QTest.mouseRelease(self.dock, Qt.MouseButton.LeftButton, pos=outside)
        self.assertEqual(self.settings.data["pinned"], ["browser.desktop", "terminal.desktop"])

    def test_separator_survives_a_settings_roundtrip(self):
        self.settings.data["pinned"] = ["browser.desktop", SEPARATOR, "terminal.desktop"]
        self.store.save()
        self.assertEqual(SettingsStore(Path(self.directory.name)).dock(0).data["pinned"],
                         ["browser.desktop", SEPARATOR, "terminal.desktop"])

    def test_dialog_lifecycle(self):
        self.dock.open_picker()
        original = self.dock.dialog
        self.dock.open_settings()
        self.assertIs(original, self.dock.dialog)
        original.reject()
        self.assertIsNone(self.dock.dialog)



class DockManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.apps = [DesktopApp("browser.desktop", "Browser", "browser")]
        self.store = SettingsStore(Path(self.directory.name))
        patcher = patch("flowdocks.ui.discover_apps", return_value=self.apps)
        patcher.start()
        self.addCleanup(patcher.stop)
        poller = patch.object(Dock, "poll_windows")
        poller.start()
        self.addCleanup(poller.stop)
        self.manager = DockManager(self.store, smoke_test=True)
        self.addCleanup(self.cleanup)
        self.quiet(self.manager.docks)
        self.qt.processEvents()

    def quiet(self, docks):
        """Drive animation by hand rather than letting timers fire in tests."""
        for dock in docks:
            dock.timer.stop()
            dock.poll_timer.stop()

    def cleanup(self):
        self.manager.close()
        for dock in list(self.manager.docks):
            dock.close()
            dock.deleteLater()
        self.qt.processEvents()

    def test_starts_with_one_dock_and_adds_up_to_the_limit(self):
        self.assertEqual(len(self.manager.docks), 1)
        while len(self.manager.docks) < MAX_DOCKS:
            self.assertTrue(self.manager.add_dock())
            self.quiet(self.manager.docks)
        self.assertEqual(len(self.manager.docks), MAX_DOCKS)
        with patch.object(self.manager.docks[0], "show_error") as error:
            self.assertFalse(self.manager.add_dock(), "the limit is refused, not exceeded")
            error.assert_called_once()
        self.assertEqual(len(self.manager.docks), MAX_DOCKS)
        self.assertEqual(self.store.count(), MAX_DOCKS)

    def test_each_dock_edits_only_its_own_settings(self):
        self.manager.add_dock()
        self.quiet(self.manager.docks)
        first, second = self.manager.docks
        first.store.data["theme"] = "graphite"
        second.store.data["theme"] = "aurora"
        self.assertEqual(self.store.data["docks"][0]["theme"], "graphite")
        self.assertEqual(self.store.data["docks"][1]["theme"], "aurora")
        first.toggle_pin("browser.desktop")
        self.assertEqual(second.store.data["pinned"], [])

    def test_removing_a_dock_reindexes_the_rest(self):
        self.manager.add_dock()
        self.quiet(self.manager.docks)
        self.manager.add_dock()
        self.quiet(self.manager.docks)
        for index, dock in enumerate(self.manager.docks):
            dock.store.data["theme"] = ("midnight", "graphite", "aurora")[index]
        self.manager.remove_dock(self.manager.docks[0])
        self.assertEqual(len(self.manager.docks), 2)
        # Each surviving dock must now address the slice that moved under it.
        self.assertEqual([dock.store.data["theme"] for dock in self.manager.docks],
                         ["graphite", "aurora"])
        self.assertEqual([dock.store.index for dock in self.manager.docks], [0, 1])

    def test_the_last_dock_cannot_be_removed(self):
        dock = self.manager.docks[0]
        with patch.object(dock, "show_error") as error:
            self.assertFalse(self.manager.remove_dock(dock))
            error.assert_called_once()
        self.assertEqual(len(self.manager.docks), 1)

    def test_one_shortcut_toggles_every_dock_together(self):
        self.manager.add_dock()
        self.quiet(self.manager.docks)
        self.manager.reveal_all()
        self.assertTrue(all(d.isVisible() for d in self.manager.docks))
        self.manager.toggle_all()
        self.assertTrue(all(d.manual_hidden for d in self.manager.docks))
        self.manager.toggle_all()
        self.assertFalse(any(d.manual_hidden for d in self.manager.docks))

    def test_the_shortcut_is_shared_rather_than_per_dock(self):
        self.manager.add_dock()
        self.quiet(self.manager.docks)
        for dock in self.manager.docks:
            self.assertIs(dock.store.globals, self.store.data)
            self.assertNotIn("shortcut", dock.store.data)
        with patch.object(self.manager, "configure_shortcut", return_value=True) as configure:
            self.manager.docks[1].configure_shortcut(True, "Ctrl+Alt+J")
            configure.assert_not_called()  # smoke_test docks short-circuit

if __name__ == "__main__":
    unittest.main()
