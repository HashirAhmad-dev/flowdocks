# Usage

## The dock itself

| Action | Result |
| --- | --- |
| Click an application | Focus its most recent window, or launch it if none is open |
| Shift-click | Always launch another instance |
| Right-click an icon | Launch, pin or unpin, or jump to one specific window |
| Click the diamond | Open the application picker |
| Click the sliders | Open Preferences |
| Click the clock | Open the dock menu |
| Right-click the surface | The same menu, with panel, edge and layer submenus |

Dots beneath an icon show how many windows that application has open, up to
three. Applications that are running but not pinned appear next to your pinned
ones for as long as they are open.

When the screen is too narrow for everything, the dock stops adding icons rather
than running off the edge; the rest stay reachable in the application picker.

## Pinning and arranging

- **Pin** by dragging an application from the picker onto the dock, or from your
  desktop, file manager or applications menu. An insertion marker previews where
  it will land. Dropping never moves or copies the source file.
- A dropped launcher is matched to the installed application by path, then
  desktop-file ID, then its `Exec` line, so a copy or symlink of an installed
  entry pins correctly. A launcher for something that is not installed is refused.
- **Pin a folder, drive or file** by dragging it from a file manager onto the
  dock, or with **Pin a folder...** / **Pin a file...** in the dock menu. Clicking
  a folder or drive opens it in the file manager (the desktop's own where there
  is one, otherwise any installed one); clicking a file opens it in its default
  application, and its right-click menu also offers **Open containing folder**. A
  pinned drive disappears from the dock while it is unmounted and comes back when
  it is mounted again.
- **Reorder** by dragging a pinned icon along the dock.
- **Unpin** by dragging an icon off the dock, or through the right-click menu.

## Separators

A separator is a divider that sits in the dock like an icon, for grouping
applications. Add one with **Add separator** from the dock menu, or **Insert
separator here** from an icon's menu to place it before that icon.

Drag a separator to move it, exactly like an icon. Remove it by dragging it off
the dock, or with **Remove separator** from its own right-click menu. Separators
do not magnify on hover, and several can be used in one dock — each is tracked by
its position, so removing one never removes a different one.

## Multiple docks

FlowDocks runs up to **five docks at once**. Each is fully independent: its own
pinned applications and separators, screen edge or free position, orientation,
theme, icon size, opacity, auto-hide and screen layer.

Use the **Docks** submenu in any dock's right-click menu:

- **Add another dock** — a new dock appears on the first free screen edge. The
  entry greys out once five exist.
- **Remove this dock** — removes the dock you opened the menu from. The last
  remaining dock cannot be removed; quit FlowDocks instead.

What the docks share is the global shortcut, the tray icon, and the `--toggle`
and `--preferences` commands, all of which act on every dock at once. Preferences
opened from a dock edits that dock, except the shortcut, which is shared.

## Placing the dock

Drag the clock, the small grip on the dock's surface, or any empty part of the
surface. "Lock dock position" in Preferences prevents this without affecting icon
editing.

**Edge positioning** (the default) snaps the dock to the nearest edge of whichever
monitor the pointer is on, keeping its alignment along that edge.

**Free positioning** leaves the dock wherever you drop it, including flush against
any screen edge. A free dock can be rotated between a horizontal bar and a
vertical column with the Orientation setting. Because it has no edge to hide
into, auto-hide and the edge hotspot are disabled in free positioning — use the
shortcut, tray icon or menu instead.

An edge-docked dock always takes its orientation from the edge it is on.

## Showing and hiding

| Method | Behaviour |
| --- | --- |
| `Ctrl+Alt+A` | Toggles the dock. Configurable or disabled in Preferences |
| Edge hotspot | A three-pixel strip along the dock's own span, after a 250 ms hover |
| Tray icon | Show/hide, applications, preferences and quit |
| `flowdocks --toggle` | Toggles the running dock, or starts it if absent |

The hotspot can be set to reveal, toggle, or disabled. Leave the edge before it
will trigger again. Auto-hide is configured separately and hides the dock shortly
after the pointer leaves.

Revealing by any route temporarily raises the dock so it can be reached even in
the Below layer; it returns to its configured layer about 650 ms after the
pointer leaves. A dock you hid deliberately stays hidden until you show it again,
and a hidden dock never intercepts clicks.

`Ctrl+Alt+D` is deliberately *not* the default: XFCE binds it to Show Desktop.
If your chosen combination is already taken, FlowDocks says so rather than
stealing it from the other application — pick another, or bind
`flowdocks --toggle` in your desktop's own keyboard settings.

## Commands

```bash
flowdocks               # Start, or reveal the dock already running on this display
flowdocks --toggle      # Show or hide the running dock, starting it if absent
flowdocks --preferences # Open preferences in the running dock
flowdocks --smoke-test  # Render briefly and exit, without saving settings
```

These talk to the running dock over a per-user, per-display socket and never
start a second copy. The desktop menu entry exposes Toggle and Preferences as
right-click actions.

## Screen layers

| Layer | Behaviour |
| --- | --- |
| Normal (default) | Other windows can cover the dock, like any ordinary window |
| Above windows | The dock stays on top |
| Below windows | The dock sits behind everything, revealed on demand |

Final stacking is up to your window manager; FlowDocks sets the EWMH hints and
does not reserve desktop work area or edit your window-manager configuration.
