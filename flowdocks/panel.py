"""Control the desktop's own panel, without a shell and without root.

Each desktop is driven through its documented configuration tool, and every
method reports a human-readable problem instead of raising. Capabilities are
advertised per desktop so the menu can disable what a desktop cannot do rather
than silently failing: XFCE is supported end to end, MATE and Cinnamon through
their GSettings keys, and LXQt/KDE/GNOME expose only their settings dialog
because hiding or moving their panel is not scriptable without extra tooling.

Nothing here runs on import. The dock reads state when it opens its menu and
writes only when the user picks an action.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

EDGES = ("top", "bottom", "left", "right")


def _run(command: list[str], timeout: float = 4.0):
    """Return (ok, stdout). Missing tools and timeouts are ordinary failures."""
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                errors="replace", timeout=timeout, check=False)
        return result.returncode == 0, result.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, ""


def _spawn(command: list[str]) -> str:
    if not shutil.which(command[0]):
        return f"{command[0]} is not installed."
    try:
        subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return f"Could not open the panel settings: {error}"
    return ""


def detect_desktop() -> str:
    names = os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")
    names = names.casefold()
    # Budgie and Cinnamon also advertise GNOME, so the specific names come first.
    for key in ("xfce", "mate", "cinnamon", "budgie", "lxqt", "kde", "plasma", "gnome"):
        if key in names:
            return "kde" if key == "plasma" else key
    return "unknown"


class Panel:
    """Base class: a desktop whose panel FlowDocks cannot drive."""

    name = "Desktop panel"
    can_hide = False
    can_move = False
    can_settings = False
    reason = "FlowDocks does not know how to control this desktop's panel."

    def hidden(self) -> bool | None:
        """True/False when known, None when the state cannot be read."""
        return None

    def set_hidden(self, hidden: bool) -> str:
        return self.reason

    def move(self, edge: str) -> str:
        return self.reason

    def open_settings(self) -> str:
        return self.reason


class XfcePanel(Panel):
    """XFCE, the Kali/Xubuntu/Debian-XFCE default.

    Snap positions were confirmed against a live xfce4-panel: a horizontal panel
    (mode 0) uses p=6 for top and p=8 for bottom, and a vertical panel (mode 1)
    uses p=5 for left and p=2 for right. x/y are ignored once a panel is snapped.
    """

    name = "XFCE Panel"
    can_hide = can_move = can_settings = True
    reason = ""
    PLACEMENT = {"top": (0, 6), "bottom": (0, 8), "left": (1, 5), "right": (1, 2)}

    def _panels(self) -> list[str]:
        ok, output = _run(["xfconf-query", "-c", "xfce4-panel", "-p", "/panels"])
        ids = re.findall(r"^\s*(\d+)\s*$", output, re.MULTILINE) if ok else []
        return [f"panel-{value}" for value in ids] or ["panel-1"]

    def _get(self, panel: str, key: str) -> str:
        ok, output = _run(["xfconf-query", "-c", "xfce4-panel", "-p", f"/panels/{panel}/{key}"])
        return output if ok else ""

    def _set(self, panel: str, key: str, kind: str, value: str) -> bool:
        path = f"/panels/{panel}/{key}"
        base = ["xfconf-query", "-c", "xfce4-panel", "-p", path]
        # An unset property must be created; xfconf rejects -s on its own there.
        return (_run(base + ["-s", value])[0]
                or _run(base + ["-n", "-t", kind, "-s", value])[0])

    def hidden(self) -> bool | None:
        states = [self._get(panel, "autohide-behavior") for panel in self._panels()]
        known = [state for state in states if state.isdigit()]
        if not known and not any(state == "" for state in states):
            return None
        # An unset behaviour means "never hide", which is XFCE's default.
        return any(int(state) != 0 for state in known)

    def set_hidden(self, hidden: bool) -> str:
        if not shutil.which("xfconf-query"):
            return "xfconf-query is not installed, so the XFCE panel cannot be changed."
        failed = [panel for panel in self._panels()
                  if not self._set(panel, "autohide-behavior", "int", "2" if hidden else "0")]
        return f"Could not change {', '.join(failed)}." if failed else ""

    def move(self, edge: str) -> str:
        if edge not in self.PLACEMENT:
            return f"Unknown panel edge {edge!r}."
        if not shutil.which("xfconf-query"):
            return "xfconf-query is not installed, so the XFCE panel cannot be moved."
        mode, snap = self.PLACEMENT[edge]
        failed = []
        for panel in self._panels():
            if not (self._set(panel, "mode", "int", str(mode))
                    and self._set(panel, "position", "string", f"p={snap};x=0;y=0")):
                failed.append(panel)
        return f"Could not move {', '.join(failed)}." if failed else ""

    def open_settings(self) -> str:
        return _spawn(["xfce4-panel", "--preferences"])


class _GSettingsPanel(Panel):
    """Shared helper for the GSettings-configured panels."""

    def _get(self, schema: str, key: str) -> str:
        ok, output = _run(["gsettings", "get", schema, key])
        return output if ok else ""

    def _set(self, schema: str, key: str, value: str) -> bool:
        return _run(["gsettings", "set", schema, key, value])[0]

    def _usable(self, schema: str) -> bool:
        if not shutil.which("gsettings"):
            return False
        ok, output = _run(["gsettings", "list-schemas"])
        return ok and schema.split(":")[0] in output.split()


class MatePanel(_GSettingsPanel):
    name = "MATE Panel"
    can_hide = can_move = True
    can_settings = False
    reason = "The MATE panel settings are not available from the command line."
    SCHEMA = "org.mate.panel.toplevel"

    def _toplevels(self) -> list[str]:
        ok, output = _run(["dconf", "list", "/org/mate/panel/toplevels/"])
        return [line.strip("/") for line in output.splitlines() if line.strip()] if ok else []

    def _paths(self) -> list[str]:
        return [f"{self.SCHEMA}:/org/mate/panel/toplevels/{name}/" for name in self._toplevels()]

    def hidden(self) -> bool | None:
        values = [self._get(path, "auto-hide") for path in self._paths()]
        return any(value == "true" for value in values) if any(values) else None

    def set_hidden(self, hidden: bool) -> str:
        paths = self._paths()
        if not paths or not self._usable(self.SCHEMA):
            return "No MATE panel was found to change."
        ok = all(self._set(path, "auto-hide", "true" if hidden else "false") for path in paths)
        return "" if ok else "Could not change the MATE panel."

    def move(self, edge: str) -> str:
        paths = self._paths()
        if edge not in EDGES:
            return f"Unknown panel edge {edge!r}."
        if not paths or not self._usable(self.SCHEMA):
            return "No MATE panel was found to move."
        ok = all(self._set(path, "orientation", edge) for path in paths)
        return "" if ok else "Could not move the MATE panel."


class CinnamonPanel(_GSettingsPanel):
    name = "Cinnamon Panel"
    can_hide = can_settings = True
    can_move = True
    reason = ""
    SCHEMA = "org.cinnamon"

    def _entries(self, key: str) -> list[str]:
        # Both keys are string lists such as ['1:0:bottom'].
        return re.findall(r"'([^']*)'", self._get(self.SCHEMA, key))

    def hidden(self) -> bool | None:
        entries = self._entries("panels-autohide")
        if not entries:
            return None
        return any(entry.split(":")[-1] == "true" for entry in entries)

    def set_hidden(self, hidden: bool) -> str:
        entries = self._entries("panels-autohide")
        if not entries or not self._usable(self.SCHEMA):
            return "No Cinnamon panel was found to change."
        value = "true" if hidden else "false"
        updated = [f"{entry.split(':')[0]}:{value}" for entry in entries]
        ok = self._set(self.SCHEMA, "panels-autohide", "[" + ", ".join(f"'{e}'" for e in updated) + "]")
        return "" if ok else "Could not change the Cinnamon panel."

    def move(self, edge: str) -> str:
        entries = self._entries("panels-enabled")
        if edge not in EDGES:
            return f"Unknown panel edge {edge!r}."
        if not entries or not self._usable(self.SCHEMA):
            return "No Cinnamon panel was found to move."
        updated = []
        for entry in entries:
            parts = entry.split(":")
            updated.append(":".join(parts[:2] + [edge]) if len(parts) >= 3 else entry)
        ok = self._set(self.SCHEMA, "panels-enabled", "[" + ", ".join(f"'{e}'" for e in updated) + "]")
        return "" if ok else "Could not move the Cinnamon panel."

    def open_settings(self) -> str:
        return _spawn(["cinnamon-settings", "panel"])


class SettingsOnlyPanel(Panel):
    """Desktops whose panel is only safely changed in its own settings app."""

    can_settings = True

    def __init__(self, name: str, command: list[str], reason: str):
        self.name = name
        self.command = command
        self.reason = reason

    def open_settings(self) -> str:
        return _spawn(self.command)


def controller(desktop: str | None = None) -> Panel:
    """Return the panel controller for this desktop; never raises."""
    desktop = detect_desktop() if desktop is None else desktop
    if desktop == "xfce":
        return XfcePanel()
    if desktop == "mate":
        return MatePanel()
    if desktop == "cinnamon":
        return CinnamonPanel()
    if desktop == "lxqt":
        return SettingsOnlyPanel(
            "LXQt Panel", ["lxqt-config-panel"],
            "LXQt stores its panel in a file that only takes effect after a restart, "
            "so change it in the LXQt panel settings.")
    if desktop == "kde":
        return SettingsOnlyPanel(
            "Plasma Panel", ["systemsettings"],
            "Plasma panels are changed through Plasma's own edit mode, "
            "so use its panel settings.")
    if desktop == "gnome":
        return SettingsOnlyPanel(
            "GNOME Top Bar", ["gnome-control-center"],
            "GNOME's top bar cannot be hidden or moved without a shell extension.")
    if desktop == "budgie":
        return SettingsOnlyPanel(
            "Budgie Panel", ["budgie-desktop-settings"],
            "Budgie panels are changed through Budgie Desktop Settings.")
    return Panel()
