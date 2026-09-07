"""Unit tests never grab the user's display.

Run: python3 -m unittest discover -s tests -p test_x11.py -v
Opt in: FLOWDOCKS_TEST_X11=1 python3 -m unittest discover -s tests -p test_x11.py -v
The opt-in wrapper starts its own xvfb-run server, dbus-run-session and child. xdotool
drives real passive grabs; xfwm4 (if installed) checks mapped EWMH transitions.
No registration, key injection or WM startup occurs on the invoking display.
"""

import ctypes as C
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from flowdocks import x11 as x


class X11UnitTests(unittest.TestCase):
    def test_portable_shortcuts(self):
        for sequence, expected in (
            ("Ctrl+Alt+D", ("d", 12)), ("Meta+Shift+F12", ("F12", 65)),
            ("Ctrl+1", ("1", 4)), ("Alt+Space", ("space", 8)),
            ("Ctrl+Esc", ("Escape", 4)), ("Ctrl+Delete", ("Delete", 4)),
            ("Alt+PgUp", ("Prior", 8)), ("Ctrl+Return", ("Return", 4)),
        ):
            with self.subTest(sequence=sequence):
                self.assertEqual(x._parse_sequence(sequence), expected)

    def test_invalid_shortcuts(self):
        for sequence in ("", "D", "Space", "Ctrl+", "Ctrl+NotAKey", "Ctrl+D, Alt+F",
                         "Num+Ctrl+1", "Ctrl+Shift", None):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                x._parse_sequence(sequence)

    def test_non_xcb_never_opens_display(self):
        with patch.object(x, "_on_xcb", return_value=False), patch.object(x, "_xlib") as lib:
            shortcut = x.GlobalShortcut()
            self.assertIn("xcb", shortcut.register("Ctrl+Alt+D"))
            shortcut.unregister()
            shortcut.close()
            shortcut.close()
            self.assertIn("closed", shortcut.register("Ctrl+Alt+D"))
            x.set_window_layer(123, "above")
            lib.assert_not_called()

    def test_missing_library_and_display(self):
        shortcut = x.GlobalShortcut()
        self.addCleanup(shortcut.close)
        with patch.object(x, "_on_xcb", return_value=True), patch.object(x, "_xlib", return_value=None):
            self.assertIn("libX11", shortcut.register("Ctrl+Alt+D"))
            x.set_window_layer(123, "above")
        lib = Mock()
        lib.XOpenDisplay.return_value = None
        with patch.object(x, "_on_xcb", return_value=True), patch.object(x, "_xlib", return_value=lib):
            self.assertIn("display", shortcut.register("Ctrl+Alt+D"))
            x.set_window_layer(123, "above")
        lib.XCloseDisplay.assert_not_called()

    def test_invalid_layer_arguments_do_not_open_display(self):
        with patch.object(x, "_xlib") as lib:
            for window in (0, -1, 2**32, "123", None, True):
                x.set_window_layer(window, "above")
            x.set_window_layer(123, "dock")
            lib.assert_not_called()

    def test_legacy_repeat_pair_split_across_polls(self):
        shortcut = x.GlobalShortcut()
        lib = Mock()
        shortcut._connection = Mock(lib=lib, display=123)
        shortcut._grabs = {(42, 12)}
        events = []
        lib.XPending.side_effect = lambda display: len(events)

        def next_event(display, pointer):
            kind, timestamp, code, state = events.pop(0)
            event = C.cast(pointer, C.POINTER(x._Event)).contents
            event.type = kind
            event.key.time, event.key.keycode, event.key.state = timestamp, code, state

        lib.XNextEvent.side_effect = next_event
        activated = Mock()
        shortcut.activated.connect(activated)
        events.extend([(2, 100, 42, 12), (3, 150, 42, 12)])
        shortcut._poll()
        events.append((2, 150, 42, 12))
        shortcut._poll()
        self.assertEqual(activated.call_count, 1)
        events.extend([(3, 200, 42, 12), (2, 220, 42, 12)])
        shortcut._poll()
        self.assertEqual(activated.call_count, 2)
        events.extend([(3, 250, 42, 12), (2, 270, 43, 12), (2, 280, 42, 0)])
        shortcut._poll()
        self.assertEqual(activated.call_count, 2)
        shortcut.close()

    def test_error_trap_restores_and_forwards(self):
        lib = Mock()
        forwarded = []
        previous = x._ErrorHandler(lambda display, event: forwarded.append(display) or 0)
        lib.XSetErrorHandler.return_value = C.cast(previous, C.c_void_p).value
        with self.assertRaisesRegex(RuntimeError, "probe"):
            with x._errors(lib, 123) as errors:
                callback = x._ErrorHandler(lib.XSetErrorHandler.call_args.args[0].value)
                event = x._ErrorEvent()
                event.error_code = 10
                callback(123, C.byref(event))
                callback(456, C.byref(event))
                raise RuntimeError("probe")
        self.assertEqual(errors, [10])
        self.assertEqual(forwarded, [456])
        lib.XSync.assert_called_once_with(123, False)
        self.assertEqual(lib.XSetErrorHandler.call_args.args[0], C.cast(previous, C.c_void_p).value)


@unittest.skipUnless(os.environ.get("FLOWDOCKS_TEST_X11") == "1", "opt-in isolated Xvfb tests")
class XvfbIntegrationTests(unittest.TestCase):
    def test_isolated_server(self):
        for tool in ("xvfb-run", "Xvfb", "xdotool", "dbus-run-session"):
            if not shutil.which(tool):
                self.skipTest(f"{tool} is required")
        environment = dict(os.environ, QT_QPA_PLATFORM="xcb", FLOWDOCKS_TEST_X11="0",
                           XDG_CURRENT_DESKTOP="", QT_QPA_PLATFORMTHEME="", GTK_USE_PORTAL="0",
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SESSION_MANAGER"):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory(prefix="nexus-x11-") as directory:
            environment.update(HOME=directory, XDG_CONFIG_HOME=directory,
                               XDG_CACHE_HOME=directory, XDG_RUNTIME_DIR=directory,
                               DBUS_SESSION_BUS_ADDRESS=f"unix:path={directory}/no-session-bus")
            result = subprocess.run(
                ["xvfb-run", "-a", "-s", "-screen 0 1024x768x24 -nolisten tcp",
                 "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "--x11-child"],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90,
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        print(result.stdout, end="")


# Constructed only by the isolated child entry point, not unittest discovery.
def _integration_suite():
    class RealX11Tests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.qt = QApplication.instance() or QApplication([])
            cls.qt.setQuitOnLastWindowClosed(False)
            assert cls.qt.platformName() == "xcb"
            cls.lib = x._xlib()

        def setUp(self):
            self.shortcut = x.GlobalShortcut()
            self.addCleanup(self.shortcut.close)
            self.activations = []
            self.shortcut.activated.connect(lambda: self.activations.append(time.monotonic()))
            # Lock state is changed only inside the disposable X server.
            self.keys("keyup", "d", "Control_L", "Alt_L", "Shift_L", "Super_L")

        def keys(self, *arguments):
            subprocess.run(["xdotool", *arguments], check=True, timeout=5,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def wait_for(self, predicate, timeout=2):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return
                QTest.qWait(20)
            self.assertTrue(predicate(), "Timed out waiting for X11/WM")

        def test_activation_locks_repeat_and_unregister(self):
            self.assertEqual(self.shortcut.register("Ctrl+Alt+D"), "")
            self.keys("key", "a")
            QTest.qWait(60)
            self.assertEqual(self.activations, [])
            # Cycle all four lock combinations and leave both locks off.
            for index, lock in enumerate((None, "Caps_Lock", "Num_Lock", "Caps_Lock")):
                if lock:
                    self.keys("key", lock)
                self.keys("key", "ctrl+alt+d")
                self.wait_for(lambda: len(self.activations) == index + 1)
            self.keys("key", "Num_Lock")
            self.keys("keydown", "ctrl+alt+d")
            self.wait_for(lambda: len(self.activations) == 5)
            QTest.qWait(1100)
            self.assertEqual(len(self.activations), 5, "Holding a shortcut must not toggle repeatedly")
            self.keys("keyup", "d", "Alt_L", "Control_L")
            QTest.qWait(70)
            self.keys("key", "ctrl+alt+d")
            self.wait_for(lambda: len(self.activations) == 6)
            self.shortcut.unregister()
            self.keys("key", "ctrl+alt+d")
            QTest.qWait(70)
            self.assertEqual(len(self.activations), 6)
            self.assertEqual(self.shortcut.register("Meta+Shift+D"), "")
            self.keys("key", "Super_L+Shift_L+d")
            self.wait_for(lambda: len(self.activations) == 7)

        def test_legacy_server_autorepeat(self):
            self.assertEqual(self.shortcut.register("Ctrl+Alt+D"), "")
            supported = C.c_int()
            self.lib.XkbSetDetectableAutoRepeat(self.shortcut._connection.display,
                                               False, C.byref(supported))
            self.shortcut._detectable_repeat = False
            self.keys("keydown", "ctrl+alt+d")
            self.wait_for(lambda: len(self.activations) == 1)
            QTest.qWait(1100)
            self.assertEqual(len(self.activations), 1)
            self.keys("keyup", "d", "Alt_L", "Control_L")
            QTest.qWait(70)
            self.keys("key", "ctrl+alt+d")
            self.wait_for(lambda: len(self.activations) == 2)

        def test_partial_conflict_rollback_and_old_binding_survives(self):
            self.assertEqual(self.shortcut.register("Ctrl+Alt+D"), "")
            connection = x._Connection(self.lib)
            self.addCleanup(connection.close)
            display = connection.display
            root = self.lib.XDefaultRootWindow(display)
            code = self.lib.XKeysymToKeycode(display, self.lib.XStringToKeysym(b"f"))
            with x._errors(self.lib, display) as errors:
                self.lib.XGrabKey(display, code, 12 | x._LOCK, root, False, 1, 1)
            self.assertEqual(errors, [])
            self.assertIn("already in use", self.shortcut.register("Ctrl+Alt+F"))
            self.keys("key", "ctrl+alt+d")
            self.wait_for(lambda: len(self.activations) == 1)
            # If rollback missed any successful variant this second client conflicts.
            probe = x.GlobalShortcut()
            self.addCleanup(probe.close)
            connection.close()
            self.assertEqual(probe.register("Ctrl+Alt+F"), "")
            self.assertIn("already in use", probe.register("Ctrl+Alt+D"))
            self.assertTrue(self.shortcut.register("D"))
            self.shortcut.close()
            self.assertEqual(probe.register("Ctrl+Alt+D"), "")

        def test_successful_rebind_and_parent_cleanup(self):
            parent = QObject()
            shortcut = x.GlobalShortcut(parent)
            self.assertEqual(shortcut.register("Ctrl+Alt+D"), "")
            self.assertEqual(shortcut.register("Ctrl+Alt+F"), "")
            self.assertEqual(self.shortcut.register("Ctrl+Alt+D"), "")
            connection = shortcut._connection
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertIsNone(connection.display)
            self.assertEqual(self.shortcut.register("Ctrl+Alt+F"), "")

        def test_close_inside_activation(self):
            self.assertEqual(self.shortcut.register("Ctrl+Alt+D"), "")
            self.shortcut.activated.connect(self.shortcut.close)
            self.keys("key", "ctrl+alt+d")
            self.wait_for(lambda: bool(self.activations))
            QTest.qWait(70)
            self.assertIsNone(self.shortcut._connection.display)

        def test_key_names(self):
            for key in ("A", "9", "F1", "F12", "Space", "Escape", "Return", "Tab",
                        "Backspace", "Delete", "Home", "End", "PgUp", "PgDown", "Left", "+"):
                with self.subTest(key=key):
                    self.assertEqual(self.shortcut.register("Ctrl+Alt+" + key), "")

        def atom(self, display, name):
            return self.lib.XInternAtom(display, name.encode("ascii"), False)

        def test_unmapped_layer_preserves_state_and_invalid_window(self):
            window = QWidget()
            self.addCleanup(window.close)
            wid = int(window.winId())
            self.qt.sync()
            connection = x._Connection(self.lib)
            self.addCleanup(connection.close)
            display = connection.display
            state = self.atom(display, "_NET_WM_STATE")
            unrelated = self.atom(display, "_NET_WM_STATE_DEMANDS_ATTENTION")
            x._replace_atoms(self.lib, display, wid, state, [unrelated])
            self.lib.XSync(display, False)
            for layer in ("above", "below", "normal"):
                x.set_window_layer(wid, layer)
                actual = set(x._atom_list(self.lib, display, wid, state))
                expected = {unrelated, self.atom(display, "_NET_WM_STATE_SKIP_TASKBAR"),
                            self.atom(display, "_NET_WM_STATE_SKIP_PAGER")}
                if layer != "normal":
                    expected.add(self.atom(display, "_NET_WM_STATE_" + layer.upper()))
                self.assertEqual(actual, expected)
                self.assertEqual(x._atom_list(self.lib, display, wid, self.atom(display, "_NET_WM_WINDOW_TYPE")),
                                 [self.atom(display, "_NET_WM_WINDOW_TYPE_NORMAL")])
            # BadWindow must never reach Xlib's fatal default handler.
            window.destroy()
            self.qt.sync()
            x.set_window_layer(wid, "above")

        def test_mapped_layers_with_xfwm(self):
            if not shutil.which("xfwm4") or not shutil.which("xprop"):
                self.skipTest("xfwm4 and xprop are required for mapped-window EWMH checks")
            wm = subprocess.Popen(["xfwm4", "--sm-client-disable", "--compositor=off"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def stop_wm():
                wm.terminate()
                try:
                    wm.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    wm.kill()
                    wm.wait(timeout=5)

            self.addCleanup(stop_wm)
            # Wait for the WM's published support property without touching the host.
            self.wait_for(lambda: subprocess.run(
                ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], capture_output=True,
                text=True, timeout=2).stdout.find("window id #") >= 0, timeout=8)
            window = QWidget()
            self.addCleanup(window.close)
            window.show()
            self.qt.sync()
            wid = int(window.winId())
            x.set_window_layer(wid, "normal")
            connection = x._Connection(self.lib)
            self.addCleanup(connection.close)
            display = connection.display
            state = self.atom(display, "_NET_WM_STATE")
            above = self.atom(display, "_NET_WM_STATE_ABOVE")
            below = self.atom(display, "_NET_WM_STATE_BELOW")
            skips = {self.atom(display, "_NET_WM_STATE_SKIP_TASKBAR"),
                     self.atom(display, "_NET_WM_STATE_SKIP_PAGER")}
            self.keys("windowsize", str(wid), "400", "300")
            QTest.qWait(150)
            # Maximize through Qt to install unrelated WM-owned states.
            window.showMaximized()
            maximized = {self.atom(display, "_NET_WM_STATE_MAXIMIZED_VERT"),
                         self.atom(display, "_NET_WM_STATE_MAXIMIZED_HORZ")}
            self.wait_for(lambda: maximized <= set(x._atom_list(self.lib, display, wid, state) or []))
            for layer in ("above", "below", "normal", "above", "normal"):
                x.set_window_layer(wid, layer)
                wanted = {"above": {above}, "below": {below}, "normal": set()}[layer]

                def correct():
                    actual = set(x._atom_list(self.lib, display, wid, state) or [])
                    return actual & {above, below} == wanted and skips | maximized <= actual

                self.wait_for(correct)
                self.assertEqual(x._atom_list(self.lib, display, wid, self.atom(display, "_NET_WM_WINDOW_TYPE")),
                                 [self.atom(display, "_NET_WM_WINDOW_TYPE_NORMAL")])

            tool = QWidget()
            self.addCleanup(tool.close)
            tool.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
            tool.show()
            self.qt.sync()
            wid = int(tool.winId())
            maximized = set()
            for layer in ("normal", "above", "below", "normal"):
                x.set_window_layer(wid, layer)
                wanted = {"above": {above}, "below": {below}, "normal": set()}[layer]
                self.wait_for(correct)
                self.assertEqual(x._atom_list(self.lib, display, wid, self.atom(display, "_NET_WM_WINDOW_TYPE")),
                                 [self.atom(display, "_NET_WM_WINDOW_TYPE_NORMAL")])

    return unittest.defaultTestLoader.loadTestsFromTestCase(RealX11Tests)


if __name__ == "__main__":
    if sys.argv[1:] == ["--x11-child"]:
        result = unittest.TextTestRunner(verbosity=2).run(_integration_suite())
        sys.exit(not result.wasSuccessful())
    unittest.main()
