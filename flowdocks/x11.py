"""Dependency-free X11 helpers for the Qt GUI thread.

GlobalShortcut(parent=None) exposes activated(), register(sequence) -> str,
unregister() and close(). Pass a single QKeySequence PortableText combination,
for example "Ctrl+Alt+A"; Meta means Super. An empty result means success;
otherwise the result explains the failure and any previous binding remains.
unregister() permits later registration; close() is terminal and idempotent.
QObject destruction and application shutdown also close the private display.
Keycodes/modifier mappings are resolved at registration time; register again
after changing the keyboard layout. No root key-event subscription is made.

set_window_layer(window_id, layer) -> None accepts normal/above/below. It uses
NORMAL type, SKIP_TASKBAR and SKIP_PAGER, preserves other states, and never sets
sticky/all-desktops. Call after show and when changing layers. Mapped windows
require an EWMH-compliant WM; its state changes are asynchronous.

Both helpers are disabled outside Qt's xcb platform (including Wayland and
offscreen), or without libX11/a display. Call all public methods on the GUI
thread. Xlib error traps are serialized, synchronous and limited to our own
display; errors from other displays are forwarded to the previous handler.
XSetErrorHandler is inherently process-global, so unrelated native code must
not concurrently replace it. No Qt event processing occurs inside a trap.
"""

from contextlib import contextmanager
import ctypes as C
from functools import lru_cache
import threading
import time

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence


_Display = C.c_void_p
_XID = C.c_ulong
_KEY_PRESS, _KEY_RELEASE, _CLIENT_MESSAGE = 2, 3, 33
_SHIFT, _LOCK, _CONTROL, _ALT, _SUPER = 1, 2, 4, 8, 64
_ERROR_LOCK = threading.RLock()


class _KeyEvent(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", _Display), ("window", _XID), ("root", _XID),
        ("subwindow", _XID), ("time", C.c_ulong),
        ("x", C.c_int), ("y", C.c_int), ("x_root", C.c_int), ("y_root", C.c_int),
        ("state", C.c_uint), ("keycode", C.c_uint), ("same_screen", C.c_int),
    ]


class _ClientData(C.Union):
    _fields_ = [("b", C.c_char * 20), ("s", C.c_short * 10), ("l", C.c_long * 5)]


class _ClientMessage(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", _Display), ("window", _XID), ("message_type", _XID),
        ("format", C.c_int), ("data", _ClientData),
    ]


class _Event(C.Union):
    _fields_ = [("type", C.c_int), ("key", _KeyEvent),
                ("client", _ClientMessage), ("pad", C.c_long * 24)]


class _ErrorEvent(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("display", _Display), ("resourceid", _XID),
        ("serial", C.c_ulong), ("error_code", C.c_ubyte),
        ("request_code", C.c_ubyte), ("minor_code", C.c_ubyte),
    ]


class _ModifierMap(C.Structure):
    _fields_ = [("max_keypermod", C.c_int), ("modifiermap", C.POINTER(C.c_ubyte))]


class _WindowAttributes(C.Structure):
    _fields_ = [
        ("x", C.c_int), ("y", C.c_int), ("width", C.c_int), ("height", C.c_int),
        ("border_width", C.c_int), ("depth", C.c_int), ("visual", C.c_void_p),
        ("root", _XID), ("class_", C.c_int), ("bit_gravity", C.c_int),
        ("win_gravity", C.c_int), ("backing_store", C.c_int),
        ("backing_planes", C.c_ulong), ("backing_pixel", C.c_ulong),
        ("save_under", C.c_int), ("colormap", _XID), ("map_installed", C.c_int),
        ("map_state", C.c_int), ("all_event_masks", C.c_long),
        ("your_event_mask", C.c_long), ("do_not_propagate_mask", C.c_long),
        ("override_redirect", C.c_int), ("screen", C.c_void_p),
    ]


_ErrorHandler = C.CFUNCTYPE(C.c_int, _Display, C.POINTER(_ErrorEvent))


@lru_cache(maxsize=1)
def _xlib():
    try:
        lib = C.CDLL("libX11.so.6")
        signatures = {
            "XOpenDisplay": (_Display, [C.c_char_p]),
            "XCloseDisplay": (C.c_int, [_Display]),
            "XDefaultRootWindow": (_XID, [_Display]),
            "XStringToKeysym": (_XID, [C.c_char_p]),
            "XKeysymToKeycode": (C.c_ubyte, [_Display, _XID]),
            "XkbKeycodeToKeysym": (_XID, [_Display, C.c_ubyte, C.c_int, C.c_int]),
            "XGetModifierMapping": (C.POINTER(_ModifierMap), [_Display]),
            "XFreeModifiermap": (C.c_int, [C.POINTER(_ModifierMap)]),
            "XGrabKey": (C.c_int, [_Display, C.c_int, C.c_uint, _XID,
                                  C.c_int, C.c_int, C.c_int]),
            "XUngrabKey": (C.c_int, [_Display, C.c_int, C.c_uint, _XID]),
            "XUngrabKeyboard": (C.c_int, [_Display, C.c_ulong]),
            "XSync": (C.c_int, [_Display, C.c_int]),
            "XPending": (C.c_int, [_Display]),
            "XNextEvent": (C.c_int, [_Display, C.POINTER(_Event)]),
            "XSetErrorHandler": (C.c_void_p, [C.c_void_p]),
            "XInternAtom": (_XID, [_Display, C.c_char_p, C.c_int]),
            "XChangeProperty": (C.c_int, [_Display, _XID, _XID, _XID, C.c_int,
                                         C.c_int, C.POINTER(C.c_ubyte), C.c_int]),
            "XGetWindowProperty": (C.c_int, [_Display, _XID, _XID, C.c_long,
                                            C.c_long, C.c_int, _XID,
                                            C.POINTER(_XID), C.POINTER(C.c_int),
                                            C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                                            C.POINTER(C.POINTER(C.c_ubyte))]),
            "XGetWindowAttributes": (C.c_int, [_Display, _XID,
                                             C.POINTER(_WindowAttributes)]),
            "XSendEvent": (C.c_int, [_Display, _XID, C.c_int, C.c_long, C.POINTER(_Event)]),
            "XFree": (C.c_int, [C.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(lib, name)
            function.restype, function.argtypes = result, arguments
        if hasattr(lib, "XkbSetDetectableAutoRepeat"):
            lib.XkbSetDetectableAutoRepeat.restype = C.c_int
            lib.XkbSetDetectableAutoRepeat.argtypes = [_Display, C.c_int, C.POINTER(C.c_int)]
        return lib
    except (OSError, AttributeError):
        return None


def _on_xcb():
    app = QGuiApplication.instance()
    return (isinstance(app, QGuiApplication) and app.platformName() == "xcb"
            and QThread.currentThread() == app.thread())


@contextmanager
def _errors(lib, display):
    # Only private displays enter this scope, with all earlier requests synced.
    with _ERROR_LOCK:
        errors = []
        previous = None

        @_ErrorHandler
        def handler(other_display, event):
            if other_display == display:
                errors.append(event.contents.error_code)
                return 0
            return _ErrorHandler(previous)(other_display, event) if previous else 0

        previous = lib.XSetErrorHandler(C.cast(handler, C.c_void_p))
        try:
            yield errors
        finally:
            try:
                lib.XSync(display, False)
            finally:
                lib.XSetErrorHandler(previous)


class _Connection:
    def __init__(self, lib):
        self.lib = lib
        self.display = lib.XOpenDisplay(None)

    def close(self, *_):
        if self.display:
            display, self.display = self.display, None
            self.lib.XCloseDisplay(display)

    def __del__(self):
        self.close()


def _parse_sequence(sequence):
    if not isinstance(sequence, str) or not sequence.strip():
        raise ValueError("Enter a shortcut such as Ctrl+Alt+A.")
    parsed = QKeySequence(sequence, QKeySequence.SequenceFormat.PortableText)
    if parsed.count() != 1 or parsed[0].key() == Qt.Key.Key_unknown:
        raise ValueError("Use one valid shortcut combination, such as Ctrl+Alt+A.")
    combo = parsed[0]
    modifiers = combo.keyboardModifiers().value
    allowed = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
               | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.MetaModifier).value
    if not modifiers or modifiers & ~allowed:
        raise ValueError("A shortcut must use Ctrl, Alt, Shift or Meta (Super); no keypad modifiers.")
    mask = 0
    for qt_modifier, x_modifier in (
        (Qt.KeyboardModifier.ControlModifier, _CONTROL),
        (Qt.KeyboardModifier.AltModifier, _ALT),
        (Qt.KeyboardModifier.ShiftModifier, _SHIFT),
        (Qt.KeyboardModifier.MetaModifier, _SUPER),
    ):
        if modifiers & qt_modifier.value:
            mask |= x_modifier
    key = combo.key()
    if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
               Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock, Qt.Key.Key_ScrollLock):
        raise ValueError("Choose a non-modifier key for the shortcut.")
    name = QKeySequence(key.value).toString(QKeySequence.SequenceFormat.PortableText)
    name = {"Esc": "Escape", "Space": "space", "Backspace": "BackSpace",
            "Enter": "KP_Enter", "Ins": "Insert", "Del": "Delete", "PgUp": "Prior",
            "PgDown": "Next", "Backtab": "ISO_Left_Tab"}.get(name, name)
    if len(name) == 1:
        name = name.lower()
    if not name:
        raise ValueError("This shortcut key is not supported.")
    return name, mask


class GlobalShortcut(QObject):
    activated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connection = None
        self._grabs = set()
        self._root = 0
        self._closed = False
        self._down = False
        self._release = None
        self._detectable_repeat = False
        # XSync can buffer events without leaving the fd readable. Poll XPending,
        # not just the socket, and never select arbitrary root keyboard events.
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._poll)

    def register(self, sequence: str) -> str:
        """Replace the binding atomically on success; leave it intact on failure."""
        if self._closed:
            return "This global shortcut has been closed."
        if not _on_xcb() or QThread.currentThread() != self.thread():
            return "Global shortcuts require Qt's X11 (xcb) platform and GUI thread."
        try:
            name, modifiers = _parse_sequence(sequence)
        except ValueError as error:
            return str(error)
        lib = _xlib()
        if lib is None:
            return "Global shortcuts are unavailable: libX11 could not be loaded."
        if self._connection is None:
            connection = _Connection(lib)
            if not connection.display:
                return "Global shortcuts are unavailable: could not open the X11 display."
            self._connection = connection
            self.destroyed.connect(connection.close)
            QGuiApplication.instance().aboutToQuit.connect(connection.close)
            self._root = lib.XDefaultRootWindow(connection.display)
            if hasattr(lib, "XkbSetDetectableAutoRepeat"):
                supported = C.c_int()
                self._detectable_repeat = bool(lib.XkbSetDetectableAutoRepeat(
                    connection.display, True, C.byref(supported)) and supported.value)
        display = self._connection.display
        if not display:
            return "The X11 display has been closed."
        keysym = lib.XStringToKeysym(name.encode("utf-8"))
        if not keysym and len(name) == 1:
            keysym = lib.XStringToKeysym(f"U{ord(name):04X}".encode("ascii"))
        keycode = lib.XKeysymToKeycode(display, keysym) if keysym else 0
        if not keycode:
            return f"The key {name!r} is not available in the current X11 keyboard layout."
        if lib.XkbKeycodeToKeysym(display, keycode, 0, 0) != keysym:
            if lib.XkbKeycodeToKeysym(display, keycode, 0, 1) == keysym:
                modifiers |= _SHIFT
            else:
                return f"The key {name!r} is not available in the primary keyboard layout."

        locks = {_LOCK}
        mapping = lib.XGetModifierMapping(display)
        if not mapping:
            return "Could not read the X11 modifier mapping."
        try:
            lock_codes = {lib.XKeysymToKeycode(display, lib.XStringToKeysym(name))
                          for name in (b"Num_Lock", b"Caps_Lock")}
            lock_codes.discard(0)
            width = mapping.contents.max_keypermod
            for index in range(8):
                if any(mapping.contents.modifiermap[index * width + slot] in lock_codes
                       for slot in range(width)):
                    locks.add(1 << index)
        finally:
            lib.XFreeModifiermap(mapping)
        variants = {modifiers}
        for lock in locks:
            variants |= {mask | lock for mask in variants}
        desired = {(keycode, mask) for mask in variants}
        added = desired - self._grabs
        with _errors(lib, display) as errors:
            for code, mask in added:
                lib.XGrabKey(display, code, mask, self._root, False, 1, 1)
        if errors:
            for code, mask in added:
                lib.XUngrabKey(display, code, mask, self._root)
            lib.XSync(display, False)
            if 10 in errors:  # BadAccess: another client owns at least one variant.
                return f"The shortcut {sequence!r} is already in use by another application."
            return f"X11 could not register the shortcut (error {errors[0]})."
        if desired != self._grabs:
            for code, mask in self._grabs - desired:
                lib.XUngrabKey(display, code, mask, self._root)
            lib.XUngrabKeyboard(display, 0)
            lib.XSync(display, True)
            self._down, self._release = False, None
            self._grabs = desired
        self._timer.start()
        return ""

    def _poll(self):
        connection = self._connection
        if not connection or not connection.display:
            return
        lib, display = connection.lib, connection.display
        event = _Event()
        while connection.display and self._grabs and lib.XPending(display):
            lib.XNextEvent(display, C.byref(event))
            if event.type not in (_KEY_PRESS, _KEY_RELEASE) or event.key.send_event:
                continue
            key = event.key
            if key.keycode != next(iter(self._grabs))[0]:
                continue
            if event.type == _KEY_RELEASE:
                if self._detectable_repeat:
                    self._down = False
                else:
                    # Legacy repeat is Release/Press with identical timestamps.
                    # Defer release across polls in case the pair spans reads.
                    self._release = (key.time, time.monotonic() + 0.03)
            else:
                if self._release is not None:
                    repeat = self._release[0] == key.time
                    self._release = None
                    if repeat:
                        continue
                    self._down = False
                if not self._down and (key.keycode, key.state & 0xff) in self._grabs:
                    self._down = True
                    self.activated.emit()
        if self._release is not None and time.monotonic() >= self._release[1]:
            self._release, self._down = None, False

    def unregister(self) -> None:
        """Release all variants and any active grab; safe to call repeatedly."""
        self._timer.stop()
        connection = self._connection
        if connection and connection.display:
            for code, mask in self._grabs:
                connection.lib.XUngrabKey(connection.display, code, mask, self._root)
            connection.lib.XUngrabKeyboard(connection.display, 0)
            connection.lib.XSync(connection.display, True)
        self._grabs.clear()
        self._down, self._release = False, None

    def close(self) -> None:
        """Release X resources permanently; also performed on QObject deletion."""
        self.unregister()
        if self._connection:
            self._connection.close()
        self._closed = True


def _atom_list(lib, display, window, property_atom):
    actual, format_ = _XID(), C.c_int()
    count, remaining = C.c_ulong(), C.c_ulong()
    data = C.POINTER(C.c_ubyte)()
    result = lib.XGetWindowProperty(display, window, property_atom, 0, 65536, False, 4,
                                    C.byref(actual), C.byref(format_), C.byref(count),
                                    C.byref(remaining), C.byref(data))
    try:
        if result or remaining.value or (actual.value and (actual.value != 4 or format_.value != 32)):
            return None
        return list(C.cast(data, C.POINTER(_XID))[:count.value]) if data else []
    finally:
        if data:
            lib.XFree(data)


def _replace_atoms(lib, display, window, property_atom, atoms):
    # Xlib's format=32 API uses native unsigned longs, including on LP64.
    values = (_XID * len(atoms))(*atoms)
    lib.XChangeProperty(display, window, property_atom, 4, 32, 0,
                        C.cast(values, C.POINTER(C.c_ubyte)), len(atoms))


def set_window_layer(window_id: int, layer: str) -> None:
    """Best-effort EWMH layering; invalid IDs/layers and unavailable X11 are no-ops."""
    if (layer not in ("normal", "above", "below") or not isinstance(window_id, int)
            or isinstance(window_id, bool) or not 0 < window_id <= 0xffffffff or not _on_xcb()):
        return
    lib = _xlib()
    if lib is None:
        return
    connection = _Connection(lib)
    display = connection.display
    if not display:
        return
    try:
        with _errors(lib, display):
            attributes = _WindowAttributes()
            if not lib.XGetWindowAttributes(display, window_id, C.byref(attributes)):
                return
            atoms = {name: lib.XInternAtom(display, name.encode("ascii"), False) for name in (
                "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_STATE",
                "_NET_WM_STATE_ABOVE", "_NET_WM_STATE_BELOW",
                "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER",
            )}
            _replace_atoms(lib, display, window_id, atoms["_NET_WM_WINDOW_TYPE"],
                           [atoms["_NET_WM_WINDOW_TYPE_NORMAL"]])
            above, below = atoms["_NET_WM_STATE_ABOVE"], atoms["_NET_WM_STATE_BELOW"]
            skips = [atoms["_NET_WM_STATE_SKIP_TASKBAR"], atoms["_NET_WM_STATE_SKIP_PAGER"]]
            wanted = {"normal": [], "above": [above], "below": [below]}[layer]
            state_atom = atoms["_NET_WM_STATE"]
            if attributes.map_state == 0:  # Withdrawn: seed state before the WM manages it.
                states = _atom_list(lib, display, window_id, state_atom)
                if states is not None:
                    states = [state for state in states if state not in (above, below)]
                    _replace_atoms(lib, display, window_id, state_atom,
                                   list(dict.fromkeys(states + skips + wanted)))
            else:
                # Never overwrite a mapped window's WM-owned state property.
                removed = [state for state in (above, below) if state not in wanted]
                for action, states in ((0, removed), (1, skips), (1, wanted)):
                    if not states:
                        continue
                    event = _Event()
                    event.client.type = _CLIENT_MESSAGE
                    event.client.window = window_id
                    event.client.message_type = state_atom
                    event.client.format = 32
                    event.client.data.l[:] = [action, states[0], states[1] if len(states) > 1 else 0, 1, 0]
                    lib.XSendEvent(display, attributes.root, False, (1 << 19) | (1 << 20), C.byref(event))
    finally:
        connection.close()
