"""Panel-control tests.

Unit tests mock every external command, so they never read or change the
invoking desktop's panel. The opt-in suite starts its own Xvfb, D-Bus session
and xfce4-panel with a disposable configuration:

    FLOWDOCKS_TEST_PANEL=1 python3 -m unittest discover -s tests -p test_panel.py -v
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from flowdocks import panel as p


class DetectionTests(unittest.TestCase):
    def test_detects_known_desktops(self):
        for value, expected in (("XFCE", "xfce"), ("MATE", "mate"), ("X-Cinnamon", "cinnamon"),
                                ("LXQt", "lxqt"), ("KDE", "kde"), ("plasma", "kde"),
                                ("ubuntu:GNOME", "gnome"), ("Budgie:GNOME", "budgie"),
                                ("", "unknown"), ("Enlightenment", "unknown")):
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": value,
                                                                    "DESKTOP_SESSION": ""}, clear=False):
                self.assertEqual(p.detect_desktop(), expected)

    def test_falls_back_to_desktop_session(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "", "DESKTOP_SESSION": "xfce"}):
            self.assertEqual(p.detect_desktop(), "xfce")

    def test_controller_capability_matrix(self):
        expected = {
            "xfce": (True, True, True), "mate": (True, True, False),
            "cinnamon": (True, True, True), "lxqt": (False, False, True),
            "kde": (False, False, True), "gnome": (False, False, True),
            "budgie": (False, False, True), "unknown": (False, False, False),
        }
        for desktop, capabilities in expected.items():
            with self.subTest(desktop=desktop):
                control = p.controller(desktop)
                self.assertEqual((control.can_hide, control.can_move, control.can_settings), capabilities)
                self.assertTrue(control.name)

    def test_unsupported_desktop_reports_instead_of_raising(self):
        control = p.controller("unknown")
        self.assertIsNone(control.hidden())
        for message in (control.set_hidden(True), control.move("top"), control.open_settings()):
            self.assertTrue(message)

    def test_settings_only_desktops_explain_themselves(self):
        for desktop in ("lxqt", "kde", "gnome", "budgie"):
            with self.subTest(desktop=desktop):
                control = p.controller(desktop)
                self.assertIn(control.set_hidden(True), (control.reason,))
                self.assertTrue(control.reason)


class XfceTests(unittest.TestCase):
    def setUp(self):
        self.control = p.XfcePanel()
        self.calls = []

    def fake(self, results):
        def runner(command, timeout=4.0):
            self.calls.append(command)
            # Key off the -p property, so "/panels" cannot shadow a key below it
            # and a -s value at the end cannot hide which property was addressed.
            target = command[command.index("-p") + 1] if "-p" in command else " ".join(command)
            for match, value in results.items():
                if target == match or target.endswith("/" + match.lstrip("/")):
                    return value
            return (True, "")
        return runner

    def test_reads_panel_list(self):
        listing = "Value is an array with 2 items:\n\n1\n2\n"
        with patch.object(p, "_run", self.fake({"/panels": (True, listing)})):
            self.assertEqual(self.control._panels(), ["panel-1", "panel-2"])

    def test_missing_panel_list_assumes_the_default_panel(self):
        with patch.object(p, "_run", self.fake({"/panels": (False, "")})):
            self.assertEqual(self.control._panels(), ["panel-1"])

    def test_hidden_reflects_autohide_behaviour(self):
        for value, expected in (("0", False), ("2", True), ("1", True), ("", False)):
            with self.subTest(value=value):
                with patch.object(p, "_run", self.fake({"/panels": (True, "1"),
                                                        "autohide-behavior": (bool(value), value)})):
                    self.assertEqual(self.control.hidden(), expected)

    def test_set_hidden_creates_the_property_when_missing(self):
        # xfconf rejects -s for a property that does not exist yet.
        def runner(command, timeout=4.0):
            self.calls.append(command)
            if "/panels" == command[-1]:
                return True, "1"
            return ("-n" in command), ""
        with patch.object(shutil, "which", return_value="/usr/bin/xfconf-query"), \
             patch.object(p, "_run", runner):
            self.assertEqual(self.control.set_hidden(True), "")
        created = [c for c in self.calls if "-n" in c]
        self.assertTrue(created)
        self.assertIn("2", created[-1])

    def test_set_hidden_reports_failure(self):
        with patch.object(shutil, "which", return_value="/usr/bin/xfconf-query"), \
             patch.object(p, "_run", self.fake({"autohide-behavior": (False, ""), "/panels": (True, "1")})):
            self.assertIn("panel-1", self.control.set_hidden(True))

    def test_move_uses_verified_mode_and_snap_values(self):
        for edge, mode, snap in (("top", "0", "6"), ("bottom", "0", "8"),
                                 ("left", "1", "5"), ("right", "1", "2")):
            with self.subTest(edge=edge):
                self.calls = []
                with patch.object(shutil, "which", return_value="/usr/bin/xfconf-query"), \
                     patch.object(p, "_run", self.fake({"/panels": (True, "1")})):
                    self.assertEqual(self.control.move(edge), "")
                joined = [" ".join(c) for c in self.calls]
                self.assertTrue(any(f"/mode -s {mode}" in c for c in joined), joined)
                self.assertTrue(any(f"p={snap};x=0;y=0" in c for c in joined), joined)

    def test_move_rejects_an_unknown_edge_without_running_anything(self):
        with patch.object(p, "_run") as run:
            self.assertIn("diagonal", self.control.move("diagonal"))
            run.assert_not_called()

    def test_missing_xfconf_is_reported(self):
        with patch.object(shutil, "which", return_value=None):
            self.assertIn("xfconf-query", self.control.set_hidden(True))
            self.assertIn("xfconf-query", self.control.move("top"))


class CinnamonTests(unittest.TestCase):
    def setUp(self):
        self.control = p.CinnamonPanel()

    def test_parses_and_rewrites_autohide_list(self):
        with patch.object(self.control, "_get", return_value="['1:false', '2:false']"), \
             patch.object(self.control, "_usable", return_value=True), \
             patch.object(self.control, "_set", return_value=True) as setter:
            self.assertIs(self.control.hidden(), False)
            self.assertEqual(self.control.set_hidden(True), "")
            self.assertEqual(setter.call_args.args[2], "['1:true', '2:true']")

    def test_move_rewrites_only_the_position_field(self):
        with patch.object(self.control, "_get", return_value="['1:0:bottom']"), \
             patch.object(self.control, "_usable", return_value=True), \
             patch.object(self.control, "_set", return_value=True) as setter:
            self.assertEqual(self.control.move("left"), "")
            self.assertEqual(setter.call_args.args[2], "['1:0:left']")

    def test_no_panel_is_reported(self):
        with patch.object(self.control, "_get", return_value=""):
            self.assertIsNone(self.control.hidden())
            self.assertIn("No Cinnamon panel", self.control.set_hidden(True))


class HelperTests(unittest.TestCase):
    def test_run_survives_a_missing_command(self):
        self.assertEqual(p._run(["flowdocks-no-such-tool-xyz"]), (False, ""))

    def test_spawn_reports_a_missing_command(self):
        self.assertIn("not installed", p._spawn(["flowdocks-no-such-tool-xyz"]))


@unittest.skipUnless(os.environ.get("FLOWDOCKS_TEST_PANEL") == "1",
                     "opt-in isolated xfce4-panel tests")
class RealXfcePanelTests(unittest.TestCase):
    def test_isolated_panel(self):
        for tool in ("xvfb-run", "Xvfb", "dbus-run-session", "xfce4-panel",
                     "xfconf-query", "xdotool"):
            if not shutil.which(tool):
                self.skipTest(f"{tool} is required")
        with tempfile.TemporaryDirectory(prefix="flowdocks-panel-") as directory:
            environment = dict(os.environ)
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SESSION_MANAGER",
                         "DBUS_SESSION_BUS_ADDRESS"):
                environment.pop(name, None)
            for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
                path = Path(directory) / name
                path.mkdir(mode=0o700)
                environment[name] = str(path)
            environment.update(XDG_CURRENT_DESKTOP="XFCE", PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                               FLOWDOCKS_TEST_PANEL="0")
            result = subprocess.run(
                ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24 -nolisten tcp",
                 "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "--panel-child"],
                env=environment, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout, end="")


def _panel_child():
    """Drive a private xfce4-panel and confirm the real geometry follows."""
    from flowdocks.panel import XfcePanel

    def run(*args):
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    def geometry():
        for window in run("xdotool", "search", "--class", "xfce4-panel").stdout.split():
            values = dict(line.split("=", 1) for line in
                          run("xdotool", "getwindowgeometry", "--shell", window).stdout.splitlines()
                          if "=" in line)
            width, height = int(values.get("WIDTH", 0)), int(values.get("HEIGHT", 0))
            if width >= 600 or height >= 400:
                return int(values["X"]), int(values["Y"]), width, height
        return None

    def wait(predicate, description, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.2)
        raise AssertionError(f"Timed out waiting for {description}; geometry={geometry()}")

    # xfsettingsd only supplies theming, and is absent on a minimal runner, so
    # the panel is exercised with or without it.
    settings = None
    if shutil.which("xfsettingsd"):
        settings = subprocess.Popen(["xfsettingsd", "--sm-client-disable"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    panel = subprocess.Popen(["xfce4-panel", "--disable-wm-check"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait(geometry, "the private panel to map")
        control = XfcePanel()
        assert control._panels(), "no panels discovered"
        assert control.hidden() is False, f"unexpected initial state {control.hidden()!r}"

        assert control.move("top") == ""
        wait(lambda: geometry()[1] == 0 and geometry()[2] >= 600, "a top panel")
        assert control.move("bottom") == ""
        wait(lambda: geometry()[1] > 400 and geometry()[2] >= 600, "a bottom panel")
        assert control.move("left") == ""
        wait(lambda: geometry()[0] == 0 and geometry()[3] >= 400, "a left panel")
        assert control.move("right") == ""
        wait(lambda: geometry()[0] > 600 and geometry()[3] >= 400, "a right panel")
        assert control.move("bottom") == ""
        wait(lambda: geometry()[2] >= 600, "a horizontal panel again")

        # autohide-behavior does not exist yet, so this exercises the create path.
        assert control.set_hidden(True) == ""
        wait(lambda: control.hidden() is True, "the panel to report hidden")
        assert control.set_hidden(False) == ""
        wait(lambda: control.hidden() is False, "the panel to report visible")
        print("isolated xfce4-panel: move to all four edges and hide/show verified")
    finally:
        for process in (panel, settings):
            if process is None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    if sys.argv[1:] == ["--panel-child"]:
        if not os.environ.get("DISPLAY"):
            raise SystemExit("Refusing to run outside the wrapper's private display")
        _panel_child()
        raise SystemExit(0)
    unittest.main()
