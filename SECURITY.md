# Security Policy

## Supported versions

The latest release receives fixes. Older tags do not.

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/HashirAhmad-dev/flowdocks/security/advisories/new),
or by email to hashirahmad8055@gmail.com. Do not open a public issue for a
vulnerability. Expect an acknowledgement within a few days.

Please include the version, your desktop and session type, and the smallest
reproduction you have.

## Trust model

FlowDocks runs entirely as the desktop user. It never asks for root, installs no
service, opens no network connection and sends no telemetry.

It touches these things on the user's behalf:

- **Reads** desktop entries from the XDG application directories.
- **Launches** applications through `gio launch`, or by executing the entry's
  `Exec` line directly as an argument list. No shell is inserted. A desktop entry
  that itself invokes a shell still does so, exactly as in any other launcher.
- **Writes** its own settings to `$XDG_CONFIG_HOME/flowdocks/settings.json`, and,
  only when the user enables autostart, `$XDG_CONFIG_HOME/autostart/`.
- **Changes** the user's own panel configuration through that desktop's supported
  tool (`xfconf-query`, `gsettings`), and only when the user picks a panel action.
- **Grabs** one global key combination on X11, only when enabled. A combination
  already owned by another application is reported, never stolen.
- **Reads** window titles and classes through `xprop`, and activates windows
  through `xdotool`, to show running-application indicators.

Deliberate limits:

- A launcher dropped on the dock must resolve to an application already installed
  on the system. An arbitrary downloaded `.desktop` file is refused, so a drop
  cannot turn an untrusted file into a dock shortcut.
- Settings are validated on load. A corrupt or hostile settings file falls back
  to defaults rather than being trusted.
- The control socket accepts a fixed set of short commands from the user's own
  runtime directory, and is created with user-only access.
