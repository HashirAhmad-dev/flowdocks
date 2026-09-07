"""Qt-free desktop integration. External commands never run through a shell."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


@dataclass
class DesktopApp:
    id: str
    name: str
    exec_line: str
    icon: str = ""
    path: str = ""
    terminal: bool = False
    startup_wm_class: str = ""
    working_directory: str = ""


@dataclass
class WindowInfo:
    id: str
    title: str
    wm_class: str
    pid: int = 0


DEFAULT_DOCK = {
    "icon_size": 52,
    "magnification": 1.55,
    "auto_hide": False,
    "position": "bottom",
    "theme": "midnight",
    "opacity": 92,
    "screen": 0,
    "pinned": [],
    "layer": "normal",
    "edge_action": "reveal",
    "alignment": 0.5,
    "lock_position": False,
    "move_mode": "edge",
    "free_x": 0.5,
    "free_y": 0.82,
    "orientation": "horizontal",
}

# One key grab serves every dock, so the shortcut lives above them.
DEFAULT_GLOBAL = {
    "shortcut_enabled": True,
    "shortcut": "Ctrl+Alt+A",
}

MAX_DOCKS = 5

# A pinned slot holding this instead of a desktop-file ID draws a divider. No
# desktop ID can collide with it, and several may appear in one dock, so pinned
# slots are addressed by position rather than by value.
SEPARATOR = "|"

# Settings are read from here once if the current directory has none, so an
# upgrade from the pre-rename releases keeps the user's pins and layout.
LEGACY_CONFIG_DIR = "nexus-dock"


def _xdg_home(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def _desktop_entry(path: Path) -> configparser.SectionProxy | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with path.open(encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        return parser["Desktop Entry"] if parser.has_section("Desktop Entry") else None
    except (OSError, ValueError, configparser.Error):
        return None


def _value(entry: configparser.SectionProxy, key: str, default: str = "") -> str:
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    return re.sub(r"\\([sntr\\])", lambda match: escapes[match[1]], entry.get(key, default))


def _locales() -> list[str]:
    locale = next((os.environ[key] for key in ("LC_ALL", "LC_MESSAGES", "LANG")
                   if os.environ.get(key)), "")
    if locale.split(".")[0] in ("C", "POSIX"):
        return []
    languages = os.environ.get("LANGUAGE", "").split(":") + [locale]
    result = []
    for language in languages:
        language = re.sub(r"\.[^@]*", "", language)
        base, _, modifier = language.partition("@")
        short = base.split("_")[0]
        for candidate in (language, base, short + "@" + modifier if modifier else short, short):
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def discover_apps() -> list[DesktopApp]:
    """Read XDG application entries; higher-priority IDs also mask hidden entries."""
    roots = [_xdg_home("XDG_DATA_HOME", ".local/share")]
    roots.extend(Path(value) for value in
                 (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
                 if value and Path(value).is_absolute())
    desktops = set(filter(None, os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")))
    locales = _locales()
    seen: set[str] = set()
    apps = []
    for root in roots:
        directory = root / "applications"
        try:
            paths = sorted(directory.rglob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            app_id = str(path.relative_to(directory)).replace(os.sep, "-")
            if app_id in seen:
                continue
            seen.add(app_id)
            entry = _desktop_entry(path)
            if entry is None or entry.get("Type") != "Application":
                continue
            if any(entry.get(key, "false").lower() == "true" for key in ("Hidden", "NoDisplay")):
                continue
            only = set(filter(None, entry.get("OnlyShowIn", "").split(";")))
            excluded = set(filter(None, entry.get("NotShowIn", "").split(";")))
            if ("OnlyShowIn" in entry and not only.intersection(desktops)) or excluded.intersection(desktops):
                continue
            try_exec = _value(entry, "TryExec")
            if try_exec:
                try:
                    if not shutil.which(try_exec):
                        continue
                except (OSError, ValueError):
                    continue
            name = next((_value(entry, f"Name[{locale}]") for locale in locales
                         if entry.get(f"Name[{locale}]")), _value(entry, "Name"))
            exec_line = _value(entry, "Exec")
            if not name or not exec_line:
                continue
            apps.append(DesktopApp(
                app_id, name, exec_line, _value(entry, "Icon"), str(path),
                entry.get("Terminal", "false").lower() == "true",
                _value(entry, "StartupWMClass"), _value(entry, "Path"),
            ))
    return sorted(apps, key=lambda app: (app.name.casefold(), app.id))


def _exec_arguments(exec_line: str, app: DesktopApp | None = None) -> list[str]:
    result = []
    for token in shlex.split(exec_line):
        if token == "%i":
            if app and app.icon:
                result.extend(["--icon", app.icon])
            continue
        output = ""
        index = 0
        removed = False
        while index < len(token):
            char = token[index]
            if char != "%":
                output += char
                index += 1
                continue
            index += 1
            if index == len(token):
                raise ValueError("Exec ends with an incomplete field code")
            code = token[index]
            if code == "%":
                output += "%"
            elif code in "fFuUdDnNvm":
                removed = True
            elif code in "ck":
                output += (app.name if code == "c" else app.path) if app else ""
                removed = True
            elif code == "i":
                raise ValueError("The %i field code must be a separate argument")
            else:
                raise ValueError(f"Unsupported Exec field code %{code}")
            index += 1
        if output or not removed:
            result.append(output)
    if not result or not result[0]:
        raise ValueError("Exec does not contain an executable")
    return result


def parse_exec(exec_line: str) -> list[str]:
    """Parse argv without shell expansion; omit fields needing files or app context.

    Literal %% is retained as %. Unknown/malformed field codes raise ValueError.
    """
    return _exec_arguments(exec_line)


def launch_app(app: DesktopApp) -> None:
    """Launch a desktop entry, raising an actionable RuntimeError on failure."""
    try:
        gio = shutil.which("gio")
        if app.path and Path(app.path).is_file() and gio:
            # Launched apps inherit these descriptors; pipes would wait for the app to exit.
            with tempfile.TemporaryFile() as errors:
                completed = subprocess.run(
                    [gio, "launch", str(Path(app.path).absolute())], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=errors, timeout=8, check=False,
                )
                if completed.returncode:
                    errors.seek(0)
                    detail = errors.read(65536).decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"Could not launch {app.name}: {detail or 'gio rejected the desktop entry'}")
            return
        command = _exec_arguments(app.exec_line, app)
        if app.terminal:
            for terminal, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"),
                                   ("konsole", "-e"), ("xfce4-terminal", "-x"),
                                   ("kitty", "--"), ("alacritty", "-e"), ("xterm", "-e")):
                executable = shutil.which(terminal)
                if executable:
                    command = [executable, flag, *command]
                    break
            else:
                raise RuntimeError(f"Could not launch {app.name}: install a terminal emulator (for example xterm)")
        subprocess.Popen(command, cwd=app.working_directory or None, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Could not launch {app.name}: {error}. Check its executable and working directory.") from error


def _xprop(arguments: list[str], timeout: float = 1.0) -> str:
    try:
        result = subprocess.run(["xprop", *arguments], capture_output=True, text=True,
                                errors="replace", timeout=timeout, check=False)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def _xstrings(value: str) -> list[str]:
    # xprop quotes C-style strings; decoding unicode_escape would corrupt UTF-8 titles.
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    return [re.sub(r"\\([nrt\"\\])", lambda match: escapes[match[1]], item)
            for item in re.findall(r'"((?:\\.|[^"\\])*)"', value)]


def list_windows() -> list[WindowInfo]:
    """Return EWMH task windows on X11 (including XWayland when exposed)."""
    if not os.environ.get("DISPLAY"):
        return []
    root = _xprop(["-root", "_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST"])
    ids = []
    for property_name in ("_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST"):
        line = next((line for line in root.splitlines() if line.startswith(property_name + "(")), "")
        ids = re.findall(r"0x[0-9a-fA-F]+", line)
        if ids:
            break
    windows = []
    deadline = time.monotonic() + 3.0
    for window_id in dict.fromkeys(ids):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        output = _xprop(["-id", window_id, "_NET_WM_NAME", "WM_NAME", "WM_CLASS", "_NET_WM_PID",
                         "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE"], min(1.0, remaining))
        properties = {}
        for line in output.splitlines():
            match = re.match(r"^(\w+)\([^)]*\)\s*=\s*(.*)$", line)
            if match:
                properties[match[1]] = match[2]
        state = properties.get("_NET_WM_STATE", "")
        kind = properties.get("_NET_WM_WINDOW_TYPE", "")
        if "_NET_WM_STATE_SKIP_TASKBAR" in state or any(
            atom in kind for atom in ("_NET_WM_WINDOW_TYPE_DOCK", "_NET_WM_WINDOW_TYPE_DESKTOP")
        ):
            continue
        titles = _xstrings(properties.get("_NET_WM_NAME", "")) or _xstrings(properties.get("WM_NAME", ""))
        classes = _xstrings(properties.get("WM_CLASS", ""))
        if not titles and not classes:
            continue
        pid_text = properties.get("_NET_WM_PID", "")
        pid = int(pid_text) if re.fullmatch(r"[0-9]{1,10}", pid_text) else 0
        windows.append(WindowInfo(window_id, titles[0] if titles else "", ", ".join(classes), pid))
    return windows


def activate_window(window_id: str) -> None:
    """Best-effort activation; stale IDs and unavailable X11 tools are harmless."""
    if not isinstance(window_id, str) or not re.fullmatch(r"(?:0x[0-9a-fA-F]{1,8}|[0-9]{1,10})", window_id):
        return
    numeric_id = int(window_id, 16 if window_id.startswith("0x") else 10)
    if not 0 < numeric_id <= 0xFFFFFFFF or not os.environ.get("DISPLAY"):
        return
    try:
        subprocess.run(["xdotool", "windowactivate", window_id], capture_output=True, timeout=2, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def app_matches_window(app: DesktopApp, window: WindowInfo) -> bool:
    """Use exact case-insensitive identities, never title/substring guesses."""
    classes = {part.strip().strip('"').casefold() for part in window.wm_class.split(",") if part.strip()}
    if not classes:
        return False
    if app.startup_wm_class and app.startup_wm_class.casefold() in classes:
        return True
    identities = {app.id.removesuffix(".desktop").casefold()}
    try:
        command = parse_exec(app.exec_line)
        executable = Path(command[0]).name.casefold()
        if executable == "env":
            command = command[1:]
            while command and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", command[0]):
                command.pop(0)
            executable = Path(command[0]).name.casefold() if command and not command[0].startswith("-") else ""
        if executable and executable not in {"sh", "bash", "dash", "zsh", "env", "flatpak", "snap", "java", "node", "perl", "ruby"} and not executable.startswith("python"):
            identities.add(executable)
    except ValueError:
        pass
    return bool((identities - {""}).intersection(classes))


def pick_default_pins(apps: list[DesktopApp]) -> list[str]:
    """Pick at most one installed app per browser/terminal/files/editor/settings role."""
    groups = (
        ("firefox", "org.mozilla.firefox", "chromium", "chromium-browser", "google-chrome", "brave-browser", "org.gnome.epiphany"),
        ("org.gnome.terminal", "xfce4-terminal", "org.kde.konsole", "kitty", "alacritty", "xterm", "terminal"),
        ("org.gnome.nautilus", "thunar", "org.kde.dolphin", "nemo", "pcmanfm", "files"),
        ("code", "codium", "org.gnome.texteditor", "org.gnome.gedit", "org.kde.kate", "mousepad", "text editor"),
        ("gnome-control-center", "org.gnome.settings", "systemsettings", "xfce4-settings-manager", "preferences", "settings"),
    )
    pins = []
    for group in groups:
        match = next((app for identity in group for app in apps
                      if app.id not in pins and identity in
                      (app.id.removesuffix(".desktop").casefold(), app.name.casefold())), None)
        if match:
            pins.append(match.id)
    return pins


def resolve_dropped_desktops(paths, apps: list[DesktopApp]) -> list[DesktopApp]:
    """Map dropped .desktop files onto installed apps.

    People drag the copy on their desktop or the entry out of the applications
    menu, which is rarely the catalogued path, so match on the desktop-file ID
    and finally the Exec line too. Only catalogued apps are ever returned, so an
    arbitrary downloaded launcher still cannot become a dock shortcut.
    """
    by_path, by_id, by_exec = {}, {}, {}
    for app in apps:
        if app.path:
            try:
                by_path.setdefault(str(Path(app.path).resolve()), app)
            except OSError:
                pass
        by_id.setdefault(app.id, app)
        if app.exec_line.strip():
            by_exec.setdefault(app.exec_line.strip(), app)
    found = []
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        try:
            match = by_path.get(str(path.resolve()))
        except OSError:
            match = None
        if match is None:
            match = by_id.get(path.name)
        if match is None and path.suffix == ".desktop":
            entry = _desktop_entry(path)
            if entry is not None:
                match = by_exec.get(_value(entry, "Exec").strip())
        if match is not None and match not in found:
            found.append(match)
    return found


def _validated_dock(values: dict) -> dict:
    data = {**DEFAULT_DOCK, "pinned": []}
    for key, value in values.items():
        valid = False
        if key in ("icon_size", "opacity", "screen") and type(value) is int:
            low, high = {"icon_size": (24, 128), "opacity": (20, 100), "screen": (0, 1024)}[key]
            valid = low <= value <= high
        elif key == "magnification" and type(value) in (int, float):
            valid = 1.0 <= value <= 3.0
        elif key in ("auto_hide", "lock_position"):
            valid = type(value) is bool
        elif key in ("alignment", "free_x", "free_y") and type(value) in (int, float):
            valid = 0 <= value <= 1
        elif key == "move_mode":
            valid = isinstance(value, str) and value in ("edge", "free")
        elif key == "orientation":
            valid = isinstance(value, str) and value in ("horizontal", "vertical")
        elif key == "layer":
            valid = isinstance(value, str) and value in ("normal", "above", "below")
        elif key == "edge_action":
            valid = isinstance(value, str) and value in ("off", "reveal", "toggle")
        elif key == "position":
            valid = isinstance(value, str) and value in ("bottom", "top", "left", "right")
        elif key == "theme":
            valid = isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value))
        elif key == "pinned" and isinstance(value, list):
            data[key] = list(dict.fromkeys(item for item in value if isinstance(item, str) and item and "\x00" not in item))
        if valid:
            data[key] = value
    return data


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _read_settings(path: Path) -> dict | None:
    """Return the stored mapping, or None when it is missing or unreadable."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _dock_sources(values: dict) -> list[dict]:
    """The per-dock mappings in a settings file, whatever its vintage.

    Files written before multiple docks existed hold one dock's keys at the top
    level, so such a file reads back as a single dock.
    """
    docks = values.get("docks")
    if isinstance(docks, list) and docks:
        return [dock for dock in docks if isinstance(dock, dict)][:MAX_DOCKS] or [{}]
    return [values] if values else [{}]


def _validated_root(values: dict) -> dict:
    data = {**DEFAULT_GLOBAL}
    if type(values.get("shortcut_enabled")) is bool:
        data["shortcut_enabled"] = values["shortcut_enabled"]
    shortcut = values.get("shortcut")
    if isinstance(shortcut, str) and 0 < len(shortcut) <= 80 and not any(ord(c) < 32 for c in shortcut):
        data["shortcut"] = shortcut
    data["docks"] = [_validated_dock(dock) for dock in _dock_sources(values)]
    return data


class DockSettings:
    """One dock's slice of the shared settings file.

    Docks read and write settings through this, so a dock never needs to know
    which of several it is, nor that the globals sit beside it.
    """

    def __init__(self, store: "SettingsStore", index: int):
        self.store, self.index = store, index

    @property
    def data(self) -> dict:
        return self.store.data["docks"][self.index]

    @property
    def globals(self) -> dict:
        return self.store.data

    def save(self) -> None:
        self.store.save()


class SettingsStore:
    def __init__(self, config_dir: Path | None = None):
        config = _xdg_home("XDG_CONFIG_HOME", ".config")
        self.path = (Path(config_dir) if config_dir is not None else config / "flowdocks") / "settings.json"
        values = _read_settings(self.path)
        if values is None and config_dir is None:
            # Read the old location without touching it; the first save moves forward.
            values = _read_settings(config / LEGACY_CONFIG_DIR / "settings.json")
        self.data = _validated_root(values or {})
        if "pinned" not in _dock_sources(values or {})[0]:
            self.data["docks"][0]["pinned"] = pick_default_pins(discover_apps())

    def count(self) -> int:
        return len(self.data["docks"])

    def dock(self, index: int) -> DockSettings:
        return DockSettings(self, index)

    def add_dock(self) -> int | None:
        """Append a dock, or return None once MAX_DOCKS are configured."""
        if self.count() >= MAX_DOCKS:
            return None
        new = _validated_dock({})
        # Start it on a free edge so it does not land exactly on an existing dock.
        taken = {dock["position"] for dock in self.data["docks"] if dock["move_mode"] == "edge"}
        new["position"] = next((edge for edge in ("bottom", "top", "left", "right")
                                if edge not in taken), "bottom")
        self.data["docks"].append(new)
        self.save()
        return self.count() - 1

    def remove_dock(self, index: int) -> bool:
        """Remove a dock, refusing to leave the user with none."""
        if self.count() <= 1 or not 0 <= index < self.count():
            return False
        self.data["docks"].pop(index)
        self.save()
        return True

    def save(self) -> None:
        """Persist validated settings atomically; read-only homes are tolerated."""
        try:
            self.data = _validated_root(self.data)
            _atomic_write(self.path, json.dumps(self.data, indent=2, allow_nan=False) + "\n")
        except (OSError, ValueError, TypeError):
            pass


def _autostart_path() -> Path:
    return _xdg_home("XDG_CONFIG_HOME", ".config") / "autostart" / "flowdocks.desktop"


def _desktop_quote(argument: str) -> str:
    # Exec quoting is not shell quoting: escape at both argv and desktop-value layers.
    argument = argument.replace("%", "%%")
    argument = re.sub(r'([\\"`$])', r"\\\1", argument)
    argument = argument.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + argument + '"'


def set_autostart(enabled: bool) -> None:
    try:
        path = _autostart_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        main = Path(__file__).resolve().parent.parent / "main.py"
        executable = str(Path(sys.executable).absolute())
        arguments = (executable, str(main)) if main.is_file() else (executable, "-m", "flowdocks.app")
        command = " ".join(_desktop_quote(argument) for argument in arguments)
        _atomic_write(path, "[Desktop Entry]\nType=Application\nName=FlowDocks\n"
                      f"Exec={command}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
    except (OSError, ValueError):
        pass


def autostart_enabled() -> bool:
    entry = _desktop_entry(_autostart_path())
    return bool(entry is not None and entry.get("Type") == "Application" and entry.get("Exec")
                and entry.get("Hidden", "false").lower() != "true"
                and entry.get("X-GNOME-Autostart-enabled", "true").lower() != "false")
