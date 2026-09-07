"""Opt-in, black-box tests of main.py; never connect to the user's display.

Run: FLOWDOCKS_TEST_APP=1 python3 -m unittest discover -s tests -p test_app.py -v
Discovery without the flag skips the wrapper and does not import Qt. The wrapper
executes this script inside its own Xvfb, D-Bus session and XFWM, with disposable
HOME/XDG directories. Only owned process handles/groups are terminated.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
DOCK_TITLE = "^FlowDocks$"
PREFS_TITLE = r"^FlowDocks \| Preferences$"


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@unittest.skipUnless(os.environ.get("FLOWDOCKS_TEST_APP") == "1",
                     "opt-in isolated full-application tests")
class AppIntegrationTests(unittest.TestCase):
    def test_isolated_application(self):
        for tool in ("xvfb-run", "Xvfb", "xauth", "dbus-run-session", "xfwm4",
                     "xdotool", "xprop", "xwininfo"):
            if not shutil.which(tool):
                self.skipTest(f"{tool} is required")
        xfconf_service = Path("/usr/share/dbus-1/services/org.xfce.Xfconf.service")
        if not xfconf_service.is_file():
            self.skipTest("XFConf D-Bus service file is required")
        with tempfile.TemporaryDirectory(prefix="nexus-app-") as directory:
            base = Path(directory)
            environment = dict(os.environ)
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SESSION_MANAGER",
                         "DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID",
                         "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                         "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS"):
                environment.pop(name, None)
            for name, subdir in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                                 ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"),
                                 ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "runtime"),
                                 ("XDG_CONFIG_DIRS", "system-config"),
                                 ("XDG_DATA_DIRS", "system-data"), ("TMPDIR", "tmp")):
                path = base / subdir
                path.mkdir(mode=0o700)
                environment[name] = str(path)
            environment.update(
                QT_QPA_PLATFORM="xcb", QT_QPA_PLATFORMTHEME="", QT_SCALE_FACTOR="1",
                QT_AUTO_SCREEN_SCALE_FACTOR="0", XDG_CURRENT_DESKTOP="XFCE",
                XDG_SESSION_TYPE="x11", GTK_USE_PORTAL="0", NO_AT_BRIDGE="1",
                GIO_USE_VFS="local",
                PYTHONPATH=str(ROOT), PYTHONNOUSERSITE="1", LC_ALL="C.UTF-8",
                FLOWDOCKS_TEST_APP="0", FLOWDOCKS_APP_ISOLATED=directory,
                FLOWDOCKS_APP_HOST_DISPLAY=os.environ.get("DISPLAY", ""),
            )
            # Start from no XFWM key grabs at all, so a future default shortcut
            # cannot silently collide with a compiled-in XFWM binding.
            config = base / "config/xfce4/xfconf/xfce-perchannel-xml"
            config.mkdir(parents=True)
            (config / "xfce4-keyboard-shortcuts.xml").write_text(
                '<channel name="xfce4-keyboard-shortcuts" version="1.0">'
                '<property name="xfwm4" type="empty">'
                '<property name="custom" type="empty">'
                '<property name="override" type="bool" value="true"/>'
                '</property></property></channel>', encoding="utf-8")
            # Permit only XFConf activation: no host portals, keyrings, user
            # services or systemd activation are needed to exercise the dock.
            services = base / "bus-services"
            services.mkdir()
            shutil.copyfile(xfconf_service, services / xfconf_service.name)
            bus_config = base / "bus.conf"
            bus_config.write_text(
                '<busconfig><type>session</type><auth>EXTERNAL</auth>'
                f'<listen>unix:tmpdir={escape(str(base / "runtime"))}</listen>'
                f'<servicedir>{escape(str(services))}</servicedir>'
                '<policy context="default"><allow send_destination="*"/>'
                '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>',
                encoding="utf-8")
            process = subprocess.Popen(
                ["xvfb-run", "-a", "-s", "-screen 0 1024x768x24 -nolisten tcp",
                 "dbus-run-session", f"--config-file={bus_config}", "--",
                 sys.executable, str(Path(__file__).resolve()),
                 "--app-child"], cwd=ROOT, env=environment, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                output, _ = process.communicate(timeout=150)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    output, _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    output, _ = process.communicate(timeout=5)
                self.fail("Isolated application suite exceeded 150 seconds:\n" + output)
            finally:
                # Includes the private server/WM/bus on timeout, never host PIDs.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            self.assertEqual(process.returncode, 0, output)
            print(output, end="")


def _integration_suite():
    class RunningAppTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.wm_log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
            cls.addClassCleanup(cls.wm_log.close)
            cls.wm = subprocess.Popen(
                ["xfwm4", "--sm-client-disable", "--compositor=off"],
                stdin=subprocess.DEVNULL, stdout=cls.wm_log, stderr=subprocess.STDOUT)
            cls.addClassCleanup(_stop, cls.wm)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = subprocess.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                                        capture_output=True, text=True, timeout=3)
                if cls.wm.poll() is None and "window id #" in result.stdout:
                    return
                time.sleep(0.05)
            cls.wm_log.seek(0)
            raise AssertionError("Private XFWM failed to start:\n" + cls.wm_log.read())

        def setUp(self):
            directory = tempfile.TemporaryDirectory(prefix="case-", dir=os.environ["TMPDIR"])
            self.addCleanup(directory.cleanup)
            base = Path(directory.name)
            self.environment = dict(os.environ)
            for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
                         "XDG_CACHE_HOME", "XDG_STATE_HOME"):
                path = base / name
                path.mkdir(mode=0o700)
                self.environment[name] = str(path)
            self.settings = Path(self.environment["XDG_CONFIG_HOME"]) / "flowdocks/settings.json"
            self.settings.parent.mkdir()
            applications = Path(self.environment["XDG_DATA_HOME"]) / "applications"
            applications.mkdir()
            for name, title in (("firefox", "Browser"), ("terminal", "Terminal")):
                (applications / f"{name}.desktop").write_text(
                    f"[Desktop Entry]\nType=Application\nName={title}\n"
                    f"Exec=/bin/true\nTerminal=false\nStartupWMClass=FlowTest{name}\n",
                    encoding="utf-8")
            self.app = None
            self.window = None
            self.log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
            self.addCleanup(self.log.close)
            self.command("xdotool", "mousemove", "20", "20")

        def command(self, *arguments, check=True):
            result = subprocess.run(arguments, env=self.environment, cwd=ROOT,
                                    capture_output=True, text=True, timeout=8)
            if check:
                self.assertEqual(result.returncode, 0,
                                 f"{arguments!r}\n{result.stdout}\n{result.stderr}")
            return result

        def wait_for(self, predicate, description, timeout=6):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.app is not None and self.app.poll() is not None:
                    break
                value = predicate()
                if value:
                    return value
                time.sleep(0.05)
            self.log.seek(0)
            detail = self.log.read()
            if self.window:
                detail += self.command("xwininfo", "-id", self.window, check=False).stdout
                detail += self.properties()
            self.fail(f"Timed out waiting for {description}; app exit="
                      f"{self.app.poll() if self.app else None}\n{detail}")

        def windows(self, title=DOCK_TITLE, visible=False):
            args = ["xdotool", "search"]
            if visible:
                args.append("--onlyvisible")
            result = self.command(*args, "--name", title, check=False)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            return result.stdout.split()

        def start(self, *arguments, settings=None):
            if settings is not None:
                self.settings.write_text(json.dumps(settings), encoding="utf-8")
            self.app = subprocess.Popen(
                [sys.executable, str(ROOT / "main.py"), *arguments], cwd=ROOT,
                env=self.environment, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=subprocess.STDOUT)
            self.addCleanup(_stop, self.app)
            self.wait_for(self.windows, "xdotool search --name '^FlowDocks$'")
            # Qt also names its unmapped group-leader window "FlowDocks".
            self.window = self.wait_for(lambda: self.windows(visible=True), "visible dock")[0]
            self.assertEqual(self.windows(visible=True), [self.window],
                             "Expected one dock, not an error dialog")
            self.wait_visible(True)
            pid = self.command("xprop", "-id", self.window, "_NET_WM_PID").stdout
            self.assertEqual(int(pid.rsplit("=", 1)[1].strip()), self.app.pid)

        def wait_visible(self, visible):
            self.wait_for(lambda: (self.window in self.windows(visible=True)) == visible,
                          f"dock {'mapped' if visible else 'unmapped'}")
            info = self.command("xwininfo", "-id", self.window).stdout
            self.assertIn("Map State: IsViewable" if visible else "Map State: IsUnMapped", info)
            self.assertIsNone(self.app.poll(), "IPC must not stop the owner")

        def ipc(self, *arguments):
            # main.py returns success only after receiving the server's b'ok\n'.
            self.command(sys.executable, str(ROOT / "main.py"), *arguments)
            self.assertIsNone(self.app.poll())

        def properties(self):
            return self.command("xprop", "-id", self.window, "_NET_WM_WINDOW_TYPE",
                                "_NET_WM_STATE", check=False).stdout

        def assert_layer(self, layer):
            def correct():
                output = self.properties()
                return ("_NET_WM_WINDOW_TYPE_NORMAL" in output
                        and "_NET_WM_WINDOW_TYPE_DOCK" not in output
                        and "_NET_WM_STATE_SKIP_TASKBAR" in output
                        and "_NET_WM_STATE_SKIP_PAGER" in output
                        and ("_NET_WM_STATE_ABOVE" in output) == (layer == "above")
                        and ("_NET_WM_STATE_BELOW" in output) == (layer == "below"))
            self.wait_for(correct, f"NORMAL window type, skip-taskbar/pager, {layer} layer")

        def test_default_startup_and_ipc_toggle(self):
            self.start()
            self.assert_layer("normal")
            self.ipc("--toggle")
            self.wait_visible(False)
            self.ipc("--toggle")
            self.wait_visible(True)
            self.assert_layer("above")

        def test_default_global_shortcut(self):
            self.start()
            self.assert_layer("normal")
            for visible in (False, True):
                self.command("xdotool", "key", "--clearmodifiers", "ctrl+alt+a")
                self.wait_visible(visible)
            self.assert_layer("above")

        def test_preferences_from_hidden_ack_escape_and_restore_layer(self):
            self.start()
            self.ipc("--toggle")
            self.wait_visible(False)
            self.ipc("--preferences")
            dialog = self.wait_for(lambda: self.windows(PREFS_TITLE, visible=True),
                                   "preferences after acknowledged IPC")[0]
            self.wait_visible(True)
            self.assert_layer("above")
            self.command("xdotool", "windowactivate", "--sync", dialog)
            self.command("xdotool", "key", "--clearmodifiers", "Escape")
            self.wait_for(lambda: not self.windows(PREFS_TITLE, visible=True), "Escape closes preferences")
            self.command("xdotool", "mousemove", "20", "20")
            time.sleep(1.2)
            self.assert_layer("normal")

        def test_above_initialization(self):
            self.start(settings={"layer": "above"})
            self.assert_layer("above")

        def test_below_initialization(self):
            self.start(settings={"layer": "below"})
            self.assert_layer("below")

        def test_toggle_starts_owner_and_default_second_invocation_reveals(self):
            self.assertEqual(self.windows(), [])
            self.start("--toggle")
            self.ipc("--toggle")
            self.wait_visible(False)
            self.ipc()
            self.wait_visible(True)
            self.assertEqual(self.windows(visible=True), [self.window])

        def test_unbindable_control_socket_still_starts_the_dock(self):
            # A runtime directory this deep overflows the ~107-byte sun_path limit,
            # so the control socket cannot bind. Losing --toggle/--preferences must
            # not stop the dock, which stays usable via mouse, tray and shortcut.
            runtime = Path(self.environment["XDG_RUNTIME_DIR"]) / ("d" * 80)
            runtime.mkdir(mode=0o700)
            self.environment["XDG_RUNTIME_DIR"] = str(runtime)
            self.start()
            self.assert_layer("normal")
            self.log.seek(0)
            self.assertIn("--toggle and --preferences are unavailable", self.log.read())
            self.command("xdotool", "key", "--clearmodifiers", "ctrl+alt+a")
            self.wait_visible(False)

        def icon_center(self, index):
            geometry = self.command("xdotool", "getwindowgeometry", "--shell", self.window).stdout
            values = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
            # The drag fixture has five entries, 48px icons and no magnification.
            # Coordinates are derived from the public window geometry, not Qt objects.
            x, y, width, height = (int(values[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))
            return x + width // 2 + (index - 2) * 62, y + height - 27 - 24

        def drag(self, start, end):
            self.command("xdotool", "mousemove", "--sync", *map(str, start))
            self.command("xdotool", "mousedown", "1")
            try:
                time.sleep(0.1)
                for fraction in (0.2, 0.5, 1):
                    point = [str(round(a + (b - a) * fraction)) for a, b in zip(start, end)]
                    self.command("xdotool", "mousemove", "--sync", *point)
                    time.sleep(0.1)
            finally:
                self.command("xdotool", "mouseup", "1")

        def saved(self, key, value):
            self.wait_for(lambda: json.loads(self.settings.read_text(encoding="utf-8")).get(key) == value,
                          f"persisted {key}={value!r}")

        def test_clock_drag_persists_left_edge(self):
            self.start(settings={"pinned": ["firefox.desktop", "terminal.desktop"],
                                 "icon_size": 48, "magnification": 1.0})
            self.drag(self.icon_center(3), (1, 384))
            self.saved("position", "left")
            geometry = self.command("xdotool", "getwindowgeometry", "--shell", self.window).stdout
            self.assertIn("\nX=0\n", geometry)

        def test_icon_drag_reorders_and_drop_outside_unpins(self):
            self.start(settings={"pinned": ["firefox.desktop", "terminal.desktop"],
                                 "icon_size": 48, "magnification": 1.0})
            second = self.icon_center(2)
            self.drag(self.icon_center(1), (second[0] + 12, second[1]))
            self.saved("pinned", ["terminal.desktop", "firefox.desktop"])
            self.drag(self.icon_center(2), (512, 300))
            self.saved("pinned", ["terminal.desktop"])
            self.wait_visible(True)

    return unittest.defaultTestLoader.loadTestsFromTestCase(RunningAppTests)


if __name__ == "__main__":
    if sys.argv[1:] == ["--app-child"]:
        isolated = os.environ.get("FLOWDOCKS_APP_ISOLATED")
        if (not isolated or not Path(isolated).is_dir()
                or os.environ.get("HOME") != str(Path(isolated) / "home")
                or not os.environ.get("DISPLAY")
                or os.environ["DISPLAY"] == os.environ.get("FLOWDOCKS_APP_HOST_DISPLAY")
                or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
            raise SystemExit("Refusing to run child outside the wrapper's private display/session")
        result = unittest.TextTestRunner(verbosity=2).run(_integration_suite())
        raise SystemExit(not result.wasSuccessful())
    unittest.main()
