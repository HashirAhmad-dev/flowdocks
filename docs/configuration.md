# Configuration

Preferences live in `$XDG_CONFIG_HOME/flowdocks/settings.json`, which is
`~/.config/flowdocks/settings.json` unless you have set `XDG_CONFIG_HOME`.

Everything can be set from the Preferences dialog; the file is documented here
because it is plain JSON and occasionally handy to edit or copy between machines.
Writes are atomic, so an interrupted save cannot leave a half-written file, and a
read-only home is tolerated rather than fatal.

## Keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `theme` | string | `"midnight"` | `midnight`, `graphite` or `aurora` |
| `icon_size` | int 24–128 | `52` | Resting icon size in pixels |
| `magnification` | float 1.0–3.0 | `1.55` | Hover zoom factor |
| `opacity` | int 20–100 | `92` | Surface opacity, as a percentage |
| `screen` | int | `0` | Monitor index |
| `pinned` | list of strings | picked on first run | Desktop-file IDs, in dock order |
| `layer` | string | `"normal"` | `normal`, `above` or `below` |
| `move_mode` | string | `"edge"` | `edge` snaps to a screen edge, `free` floats |
| `position` | string | `"bottom"` | Edge used by `move_mode: edge` |
| `alignment` | float 0–1 | `0.5` | Position along that edge |
| `orientation` | string | `"horizontal"` | `horizontal` or `vertical`, free mode only |
| `free_x` | float 0–1 | `0.5` | Horizontal placement in free mode |
| `free_y` | float 0–1 | `0.82` | Vertical placement in free mode |
| `lock_position` | bool | `false` | Prevents dragging the dock |
| `auto_hide` | bool | `false` | Hide shortly after the pointer leaves |
| `edge_action` | string | `"reveal"` | `reveal`, `toggle` or `off` |
| `shortcut_enabled` | bool | `true` | Whether to grab the global shortcut |
| `shortcut` | string | `"Ctrl+Alt+A"` | One modified key combination |

Every value is validated on load. Anything missing, out of range or of the wrong
type falls back to its default, so a corrupt or hand-edited file cannot stop the
dock from starting. Restart FlowDocks after editing the file by hand — the
running dock rewrites it from memory when you change something.

## Upgrading from the previous name

The project was formerly published under a different name. On first run, if
`~/.config/flowdocks/settings.json` does not exist, FlowDocks reads
`~/.config/nexus-dock/settings.json` once and carries over your settings and
pinned applications. The old file is left in place as a fallback and is never
modified.

## Autostart

Enabling "Start automatically when I sign in" writes
`$XDG_CONFIG_HOME/autostart/flowdocks.desktop`. It records the interpreter and
entry point in use at the time, so after moving a source checkout, disable and
re-enable it. Disabling removes the file.

## Single instance and the control socket

One dock runs per display. The lock and the control socket live in
`$XDG_RUNTIME_DIR`, keyed by a hash of the display name, so several displays can
each have their own dock. The socket is created with user-only access and accepts
a fixed set of short commands.

If the socket cannot be created — an unwritable runtime directory, or an
`XDG_RUNTIME_DIR` long enough to overflow the operating system's ~107-byte socket
path limit — the dock still starts and prints one line saying `--toggle` and
`--preferences` are unavailable. Everything else keeps working.

## What FlowDocks never does

No services, no network requests, no telemetry, no elevated privileges. It does
not reserve desktop work area, and it does not edit your window-manager
configuration. Panel changes happen only when you ask for them; see
[Desktop panel control](desktop-panel.md).
