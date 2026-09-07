"""Isolated unit tests; opt into the harmless gio/sleep probe with FLOWDOCKS_TEST_GIO=1."""

import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from flowdocks import backend as b


class BackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.system = self.root / "system"
        self.config = self.root / "config"
        environment = patch.dict(os.environ, {
            "HOME": str(self.root), "XDG_DATA_HOME": str(self.home),
            "XDG_DATA_DIRS": str(self.system), "XDG_CONFIG_HOME": str(self.config),
            "XDG_CURRENT_DESKTOP": "GNOME:Unity", "LANG": "en_US.UTF-8", "DISPLAY": ":99",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.run = self.enterContext(patch.object(b.subprocess, "run"))
        self.popen = self.enterContext(patch.object(b.subprocess, "Popen"))
        self.which = self.enterContext(patch.object(b.shutil, "which", return_value=None))

    def desktop(self, name="app.desktop", text="", root=None):
        path = (root or self.home) / "applications" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nType=Application\nName=Example\nExec=example %U\n" + text,
                        encoding="utf-8")
        return path

    def test_dataclass_and_default_contract(self):
        app = b.DesktopApp("id", "Name", "example")
        self.assertEqual((app.icon, app.path, app.terminal, app.startup_wm_class, app.working_directory),
                         ("", "", False, "", ""))
        self.assertEqual(b.WindowInfo("0x1", "Name", "Class").pid, 0)
        self.assertEqual(b.DEFAULT_SETTINGS, {
            "icon_size": 52, "magnification": 1.55, "auto_hide": False, "position": "bottom",
            "theme": "midnight", "opacity": 92, "screen": 0, "pinned": [],
            "layer": "normal", "edge_action": "reveal", "shortcut_enabled": True,
            "shortcut": "Ctrl+Alt+A", "alignment": 0.5, "lock_position": False,
            "move_mode": "edge", "free_x": 0.5, "free_y": 0.82,
            "orientation": "horizontal",
        })

    def test_dropped_desktop_copies_resolve_to_the_installed_app(self):
        installed = self.desktop("dev.example.Tool.desktop")
        apps = b.discover_apps()
        self.assertEqual([app.id for app in apps], ["dev.example.Tool.desktop"])
        copy = self.root / "Desktop/dev.example.Tool.desktop"
        copy.parent.mkdir(parents=True)
        copy.write_text(installed.read_text(encoding="utf-8"), encoding="utf-8")
        link = self.root / "Desktop/link.desktop"
        link.symlink_to(installed)
        renamed = self.root / "Desktop/renamed-by-the-browser.desktop"
        renamed.write_text(installed.read_text(encoding="utf-8"), encoding="utf-8")
        for description, path in (("catalogued path", installed), ("desktop copy", copy),
                                  ("symlink", link), ("renamed copy matched on Exec", renamed)):
            with self.subTest(description):
                self.assertEqual([app.id for app in b.resolve_dropped_desktops([str(path)], apps)],
                                 ["dev.example.Tool.desktop"])

    def test_dropped_files_that_are_not_installed_apps_are_refused(self):
        self.desktop("installed.desktop")
        apps = b.discover_apps()
        stranger = self.root / "Downloads/malicious.desktop"
        stranger.parent.mkdir(parents=True)
        stranger.write_text("[Desktop Entry]\nType=Application\nName=Bad\nExec=/bin/bad\n", encoding="utf-8")
        plain = self.root / "Downloads/notes.txt"
        plain.write_text("hello", encoding="utf-8")
        for path in (stranger, plain, self.root / "Downloads/missing.desktop"):
            with self.subTest(path=path.name):
                self.assertEqual(b.resolve_dropped_desktops([str(path)], apps), [])
        self.assertEqual(b.resolve_dropped_desktops(["", None], apps), [])

    def test_dropped_duplicates_collapse_to_one_app(self):
        installed = self.desktop("dup.desktop")
        apps = b.discover_apps()
        copy = self.root / "dup.desktop"
        copy.write_text(installed.read_text(encoding="utf-8"), encoding="utf-8")
        found = b.resolve_dropped_desktops([str(installed), str(copy), str(installed)], apps)
        self.assertEqual([app.id for app in found], ["dup.desktop"])

    def test_settings_migrate_once_from_the_previous_configuration_directory(self):
        legacy = self.config / b.LEGACY_CONFIG_DIR / "settings.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"theme": "aurora", "pinned": ["kept.desktop"]}), encoding="utf-8")
        store = b.SettingsStore()
        self.assertEqual(store.path, self.config / "flowdocks" / "settings.json")
        self.assertEqual(store.data["theme"], "aurora")
        self.assertEqual(store.data["pinned"], ["kept.desktop"])
        store.save()
        # Saving moves forward without disturbing the old file.
        self.assertTrue(store.path.is_file())
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["theme"], "aurora")
        legacy.write_text(json.dumps({"theme": "graphite"}), encoding="utf-8")
        self.assertEqual(b.SettingsStore().data["theme"], "aurora")

    def test_positioning_settings_roundtrip_and_invalid_values(self):
        values = {"move_mode": "free", "free_x": 0.25, "free_y": 1, "orientation": "vertical"}
        store = b.SettingsStore(self.config)
        store.data.update(values)
        store.save()
        loaded = b.SettingsStore(self.config)
        for key, value in values.items():
            self.assertEqual(loaded.data[key], value)
        invalid = {"move_mode": "floating", "free_x": -0.1, "free_y": "middle",
                   "orientation": "diagonal"}
        data = b._validated_settings(invalid)
        for key in invalid:
            self.assertEqual(data[key], b.DEFAULT_SETTINGS[key])

    def test_interaction_settings_roundtrip_and_invalid_values(self):
        values = {"layer": "below", "edge_action": "toggle", "shortcut_enabled": False,
                  "shortcut": "Meta+F12", "alignment": 0.8, "lock_position": True}
        store = b.SettingsStore(self.config)
        store.data.update(values)
        store.save()
        loaded = b.SettingsStore(self.config)
        for key, value in values.items():
            self.assertEqual(loaded.data[key], value)
        invalid = {"layer": [], "edge_action": "bad", "shortcut_enabled": "yes",
                   "shortcut": "bad\nvalue", "alignment": float("nan"), "lock_position": 1}
        data = b._validated_settings(invalid)
        for key in invalid:
            self.assertEqual(data[key], b.DEFAULT_SETTINGS[key])

    def test_precedence_hidden_tombstones_and_subdirectory_ids(self):
        self.desktop("vendor/tool.desktop", "Name=System\n", self.system)
        self.desktop("vendor-tool.desktop", "Name=User\n")
        self.desktop("hidden.desktop", root=self.system)
        self.desktop("hidden.desktop", "Hidden=true\n")
        self.desktop("other/deep.desktop", "Name=Alpha\n")
        apps = b.discover_apps()
        self.assertEqual([(app.id, app.name) for app in apps],
                         [("other-deep.desktop", "Alpha"), ("vendor-tool.desktop", "User")])

    def test_filters(self):
        for index, text in enumerate(("NoDisplay=true", "Type=Link", "OnlyShowIn=KDE;",
                                     "NotShowIn=GNOME;", "TryExec=missing", "Exec=", "Name=",
                                     "OnlyShowIn=")):
            self.desktop(f"bad{index}.desktop", text + "\n")
        self.desktop("good.desktop", "OnlyShowIn=Unity;KDE;\nNotShowIn=XFCE;\nTryExec=/bin/example\n")
        self.which.side_effect = lambda name: name if name == "/bin/example" else None
        self.assertEqual([app.id for app in b.discover_apps()], ["good.desktop"])

    def test_localized_names_and_fields(self):
        self.desktop(text="Name[fr]=Francais\nName[fr_CA]=Quebec\nIcon=test\nTerminal=true\n"
                     "StartupWMClass=ExampleClass\nPath=/tmp/work\\sspace\n")
        with patch.dict(os.environ, {"LC_MESSAGES": "fr_CA.UTF-8@variant"}):
            app = b.discover_apps()[0]
        self.assertEqual(app.name, "Quebec")
        self.assertEqual((app.icon, app.terminal, app.startup_wm_class, app.working_directory),
                         ("test", True, "ExampleClass", "/tmp/work space"))
        with patch.dict(os.environ, {"LANGUAGE": "fr:en", "LC_ALL": "C"}):
            self.assertEqual(b.discover_apps()[0].name, "Example")

    def test_language_preference_and_invalid_files(self):
        self.desktop(text="Name[de]=Deutsch\nName[fr]=Francais\n")
        invalid = self.desktop("invalid.desktop")
        invalid.write_bytes(b"\xff\xfe")
        malformed = self.desktop("malformed.desktop")
        malformed.write_text("not an ini file", encoding="utf-8")
        with patch.dict(os.environ, {"LANGUAGE": "de:fr"}):
            self.assertEqual([app.name for app in b.discover_apps()], ["Deutsch"])

    def test_empty_catalog_and_inaccessible_files(self):
        self.assertEqual(b.discover_apps(), [])
        self.desktop()
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            self.assertEqual(b.discover_apps(), [])

    def test_invalid_try_exec_is_ignored(self):
        self.desktop(text="TryExec=invalid\x00executable\n")
        self.which.side_effect = ValueError("embedded null byte")
        self.assertEqual(b.discover_apps(), [])

    def test_exec_fields_quotes_and_literal_percent(self):
        self.assertEqual(b.parse_exec('"/opt/My App/run" "two words" %f %F %u %U %i %c %k %% ""'),
                         ["/opt/My App/run", "two words", "%", ""])
        self.assertEqual(b.parse_exec("run %d %D %n %N %v %m --file=%f %%U"),
                         ["run", "--file=", "%U"])

    def test_exec_does_not_expand_shell_syntax(self):
        self.assertEqual(b.parse_exec("run '$HOME' '~' ';' '$(touch bad)'"),
                         ["run", "$HOME", "~", ";", "$(touch bad)"])

    def test_invalid_exec(self):
        for command in ("", "%U", 'run "unterminated', "run %Z", "run %", "run pre%i"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                b.parse_exec(command)

    def test_gio_launch_preferred(self):
        path = self.desktop()
        self.which.return_value = "/usr/bin/gio"
        self.run.return_value = subprocess.CompletedProcess([], 0, "", "")
        b.launch_app(b.DesktopApp("app", "Example", "ignored", path=str(path), terminal=True))
        self.assertEqual(self.run.call_args.args[0], ["/usr/bin/gio", "launch", str(path)])
        self.assertEqual(self.run.call_args.kwargs["timeout"], 8)
        self.assertEqual(self.run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(self.run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", self.run.call_args.kwargs)
        self.assertTrue(self.run.call_args.kwargs["stderr"].closed)
        self.popen.assert_not_called()

    def test_gio_error_and_timeout_actionable(self):
        path = self.desktop()
        self.which.return_value = "/usr/bin/gio"
        app = b.DesktopApp("app", "Example", "ignored", path=str(path))
        def fail(command, **kwargs):
            os.write(kwargs["stderr"].fileno(), b"not installed\xff\n")
            return subprocess.CompletedProcess(command, 1)

        self.run.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "Example: not installed"):
            b.launch_app(app)
        self.assertTrue(self.run.call_args.kwargs["stderr"].closed)
        self.run.side_effect = subprocess.TimeoutExpired("gio", 8)
        with self.assertRaisesRegex(RuntimeError, "Could not launch Example"):
            b.launch_app(app)
        self.assertTrue(self.run.call_args.kwargs["stderr"].closed)
        self.popen.assert_not_called()

    def test_gio_failure_without_stderr(self):
        path = self.desktop()
        self.which.return_value = "/usr/bin/gio"
        self.run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(RuntimeError, "Example: gio rejected the desktop entry"):
            b.launch_app(b.DesktopApp("app", "Example", "ignored", path=str(path)))

    def test_gio_stderr_file_failure_is_actionable(self):
        path = self.desktop()
        self.which.return_value = "/usr/bin/gio"
        with patch.object(b.tempfile, "TemporaryFile", side_effect=OSError("temporary storage unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Example: temporary storage unavailable"):
                b.launch_app(b.DesktopApp("app", "Example", "ignored", path=str(path)))
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_fallback_launch_expands_context_without_shell(self):
        app = b.DesktopApp("app", "My App", 'example %c %k %i %U "literal %%"',
                           icon="my-icon", path="/missing/app.desktop", working_directory=str(self.root))
        b.launch_app(app)
        self.assertEqual(self.popen.call_args.args[0],
                         ["example", "My App", "/missing/app.desktop", "--icon", "my-icon", "literal %"])
        self.assertEqual(self.popen.call_args.kwargs["cwd"], str(self.root))
        self.assertNotIn("shell", self.popen.call_args.kwargs)

    def test_terminal_fallback_and_missing_terminal(self):
        app = b.DesktopApp("app", "Example", "example --flag", terminal=True)
        with self.assertRaisesRegex(RuntimeError, "install a terminal"):
            b.launch_app(app)
        self.which.side_effect = lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None
        b.launch_app(app)
        self.assertEqual(self.popen.call_args.args[0], ["/usr/bin/gnome-terminal", "--", "example", "--flag"])

    def test_missing_executable_and_bad_exec(self):
        self.popen.side_effect = FileNotFoundError("missing executable")
        with self.assertRaisesRegex(RuntimeError, "Check its executable"):
            b.launch_app(b.DesktopApp("app", "Example", "missing"))
        with self.assertRaisesRegex(RuntimeError, "Unsupported Exec"):
            b.launch_app(b.DesktopApp("app", "Example", "example %z"))

    def test_window_listing_properties_and_filters(self):
        root = "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x1, 0x2, 0x3, 0x4, 0x1, 0x5\n"
        normal = ('_NET_WM_NAME(UTF8_STRING) = "Caf\u00e9 \\"title\\""\n'
                  'WM_NAME(STRING) = "Old"\nWM_CLASS(STRING) = "example", "Example"\n'
                  '_NET_WM_PID(CARDINAL) = 42\n')
        outputs = [root, normal, normal + "_NET_WM_STATE(ATOM) = _NET_WM_STATE_SKIP_TASKBAR\n",
                   normal + "_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_DOCK\n",
                   normal + "_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_DESKTOP\n", ""]
        self.run.side_effect = [subprocess.CompletedProcess([], 0, output, "") for output in outputs]
        self.assertEqual(b.list_windows(), [b.WindowInfo("0x1", 'Caf\u00e9 "title"', "example, Example", 42)])
        self.assertEqual(self.run.call_count, 6)
        self.assertTrue(all(call.kwargs["timeout"] <= 1 for call in self.run.call_args_list))

    def test_window_root_and_title_fallback(self):
        outputs = ["_NET_CLIENT_LIST_STACKING: not found.\n_NET_CLIENT_LIST(WINDOW): window id # 0xa\n",
                   'WM_NAME(STRING) = "Fallback"\nWM_CLASS(STRING) = "app", "App"\n_NET_WM_PID(CARDINAL) = bad\n']
        self.run.side_effect = [subprocess.CompletedProcess([], 0, output, "") for output in outputs]
        self.assertEqual(b.list_windows(), [b.WindowInfo("0xa", "Fallback", "app, App", 0)])

    def test_window_tools_fail_gracefully(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("xprop", 1)):
            self.run.side_effect = error
            self.assertEqual(b.list_windows(), [])
            b.activate_window("0x123")
        self.run.reset_mock()
        with patch.dict(os.environ, {"DISPLAY": ""}):
            self.assertEqual(b.list_windows(), [])
            b.activate_window("123")
        self.run.assert_not_called()

    def test_window_listing_has_an_overall_deadline(self):
        self.run.return_value = subprocess.CompletedProcess(
            [], 0, "_NET_CLIENT_LIST(WINDOW): window id # 0x1, 0x2, 0x3\n", "")
        with patch.object(b.time, "monotonic", side_effect=[0.0, 2.5, 3.1]):
            self.assertEqual(b.list_windows(), [])
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.run.call_args.kwargs["timeout"], 0.5)

    def test_activation_validation(self):
        for window_id in ("", "--help", "1;evil", "0", "0x0", "0x100000000", "9999999999", None):
            b.activate_window(window_id)
        self.run.assert_not_called()
        b.activate_window("0x123")
        self.assertEqual(self.run.call_args.args[0], ["xdotool", "windowactivate", "0x123"])
        self.assertEqual(self.run.call_args.kwargs["timeout"], 2)

    def test_conservative_window_matching(self):
        app = b.DesktopApp("org.example.App.desktop", "Friendly", "/opt/example --new", startup_wm_class="Special")
        for wm_class in ("instance, SPECIAL", "example, Example", "org.example.App"):
            self.assertTrue(b.app_matches_window(app, b.WindowInfo("1", "Unrelated", wm_class)))
        for wm_class in ("example-helper", "Friendly", "", "python3"):
            self.assertFalse(b.app_matches_window(app, b.WindowInfo("1", "Friendly", wm_class)))
        wrapped = b.DesktopApp("custom.desktop", "Custom", "env MODE=test /usr/bin/example")
        self.assertTrue(b.app_matches_window(wrapped, b.WindowInfo("1", "", "Example")))
        wrapped.exec_line = "python3 /opt/example.py"
        self.assertFalse(b.app_matches_window(wrapped, b.WindowInfo("1", "", "python3")))

    def test_default_pins_roles_and_no_duplicates(self):
        apps = [b.DesktopApp(name + ".desktop", name, name) for name in
                ("chromium", "firefox", "thunar", "kitty", "code", "gnome-control-center")]
        self.assertEqual(b.pick_default_pins(apps),
                         [name + ".desktop" for name in ("firefox", "kitty", "thunar", "code", "gnome-control-center")])
        self.assertEqual(b.pick_default_pins([]), [])
        self.assertEqual(b.pick_default_pins([b.DesktopApp("custom.desktop", "Files", "files")]), ["custom.desktop"])

    def test_settings_defaults_and_independent_mutable_values(self):
        self.desktop("firefox.desktop", "Name=Firefox\n")
        store = b.SettingsStore()
        self.assertEqual(store.path, self.config / "flowdocks" / "settings.json")
        self.assertEqual(store.data["pinned"], ["firefox.desktop"])
        self.assertFalse(store.path.exists())
        store.data["pinned"].append("other.desktop")
        self.assertEqual(b.DEFAULT_SETTINGS["pinned"], [])
        self.assertEqual(b.SettingsStore().data["pinned"], ["firefox.desktop"])

    def test_settings_roundtrip_and_explicit_empty_pins(self):
        self.desktop("firefox.desktop")
        store = b.SettingsStore(self.config)
        store.data.update(icon_size=64, pinned=[], position="left", auto_hide=True)
        store.save()
        loaded = b.SettingsStore(self.config)
        self.assertEqual(loaded.data, store.data)
        self.assertEqual(list(self.config.iterdir()), [store.path])

    def test_settings_validation(self):
        self.config.mkdir()
        values = {"icon_size": True, "magnification": float("nan"), "opacity": 101,
                  "screen": -1, "auto_hide": "false", "position": [], "theme": "../bad",
                  "pinned": ["app.desktop", 1, None, "app.desktop", ""], "unknown": 123}
        (self.config / "settings.json").write_text(json.dumps(values), encoding="utf-8")
        store = b.SettingsStore(self.config)
        self.assertEqual(store.data, {**b.DEFAULT_SETTINGS, "pinned": ["app.desktop"]})

    def test_corrupt_settings_and_relative_xdg_fallback(self):
        self.config.mkdir()
        for content in ("{broken", "[]", "null", "\ufffd"):
            (self.config / "settings.json").write_text(content, encoding="utf-8")
            self.assertEqual(b.SettingsStore(self.config).data, b.DEFAULT_SETTINGS)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}):
            self.assertEqual(b.SettingsStore().path, self.root / ".config/flowdocks/settings.json")

    def test_extreme_magnification_values_are_rejected(self):
        store = b.SettingsStore(self.config)
        for value in (10 ** 400, float("inf"), float("-inf"), float("nan")):
            store.data["magnification"] = value
            store.save()
            self.assertEqual(store.data["magnification"], 1.55)
            self.assertEqual(b.SettingsStore(self.config).data["magnification"], 1.55)

    def test_atomic_save_preserves_previous_on_replace_failure(self):
        store = b.SettingsStore(self.config)
        store.save()
        before = store.path.read_bytes()
        store.data["icon_size"] = 64
        with patch.object(b.os, "replace", side_effect=PermissionError("denied")):
            store.save()
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(list(self.config.iterdir()), [store.path])

    def test_read_only_configuration_is_harmless(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            b.SettingsStore(self.config).save()
            b.set_autostart(True)
        self.assertFalse(b.autostart_enabled())

    def test_autostart_toggle_and_quoted_paths(self):
        project = self.root / 'project with "quotes" and % fields'
        project.mkdir()
        (project / "main.py").touch()
        module = project / "flowdocks/backend.py"
        executable = str(self.root / "python with spaces")
        with patch.object(b, "__file__", str(module)), patch.object(b.sys, "executable", executable):
            b.set_autostart(True)
        self.assertTrue(b.autostart_enabled())
        path = self.config / "autostart/flowdocks.desktop"
        entry = b._desktop_entry(path)
        self.assertEqual(b.parse_exec(b._value(entry, "Exec")), [executable, str(project / "main.py")])
        b.set_autostart(False)
        b.set_autostart(False)
        self.assertFalse(path.exists())
        self.assertFalse(b.autostart_enabled())
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_autostart_installed_package_fallback(self):
        module = self.root / "site-packages/flowdocks/backend.py"
        executable = str(self.root / 'venv with spaces and %/bin/python')
        with patch.object(b, "__file__", str(module)), patch.object(b.sys, "executable", executable):
            b.set_autostart(True)
        entry = b._desktop_entry(self.config / "autostart/flowdocks.desktop")
        self.assertEqual(b.parse_exec(b._value(entry, "Exec")), [executable, "-m", "flowdocks.app"])
        self.assertTrue(b.autostart_enabled())
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_autostart_malformed_bytes_are_ignored(self):
        b.set_autostart(True)
        (self.config / "autostart/flowdocks.desktop").write_bytes(b"[Desktop Entry]\nName=\xff\n")
        self.assertFalse(b.autostart_enabled())

    def test_autostart_honors_disabled_flags(self):
        b.set_autostart(True)
        path = self.config / "autostart/flowdocks.desktop"
        original = path.read_text(encoding="utf-8")
        for suffix in ("Hidden=true\n", "X-GNOME-Autostart-enabled=false\n"):
            path.write_text(original + suffix, encoding="utf-8")
            self.assertFalse(b.autostart_enabled())


@unittest.skipUnless(os.environ.get("FLOWDOCKS_TEST_GIO") == "1", "opt-in real gio/sleep probe")
class GioIntegrationTests(unittest.TestCase):
    def test_inherited_pipes_timeout_but_backend_returns_promptly(self):
        gio = shutil.which("gio")
        if not gio or not Path("/bin/sleep").is_file():
            self.skipTest("gio and /bin/sleep are required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desktop = root / "sleep.desktop"
            desktop.write_text("[Desktop Entry]\nType=Application\nName=Sleep Probe\n"
                               "Exec=/bin/sleep 2\nTerminal=false\n", encoding="utf-8")
            with patch.dict(os.environ, {"HOME": directory, "XDG_CONFIG_HOME": directory,
                                        "XDG_DATA_HOME": directory, "XDG_CACHE_HOME": directory,
                                        "PATH": os.defpath}, clear=True):
                # gio itself exits, but communicate still waits for sleep's inherited pipes.
                with subprocess.Popen([gio, "launch", str(desktop)], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                    self.assertEqual(process.wait(timeout=1), 0)
                    try:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            process.communicate(timeout=0.1)
                    finally:
                        process.communicate(timeout=4)

                real_run = subprocess.run

                def bounded_run(command, **kwargs):
                    kwargs["timeout"] = 1
                    return real_run(command, **kwargs)

                try:
                    with patch.object(b.subprocess, "run", side_effect=bounded_run):
                        b.launch_app(b.DesktopApp("sleep.desktop", "Sleep Probe", "/bin/sleep 2", path=str(desktop)))
                finally:
                    # Allow the harmless child to finish before removing its temporary home.
                    time.sleep(2.1)


if __name__ == "__main__":
    unittest.main()
