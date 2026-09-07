# Changelog

All notable changes to FlowDocks are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html). The packaged Debian
changelog at `packaging/changelog` carries the same history.

## [Unreleased]

### Changed
- Relicensed from MIT to a proprietary all-rights-reserved licence. The source
  remains published to be read, but no right to use, copy, modify or
  redistribute it is granted. Installing and running an official release is
  permitted for personal use.

## [0.5.0] - 2026-09-07

### Added
- Separators. A pinned slot can hold a divider instead of an application, so
  icons can be grouped the way Nexus Dock does. Add one from the dock menu, drag
  it to reposition it, and drag it off the dock or use its menu to remove it.
  Separators are addressed by position, so several identical ones stay distinct.
- Up to five docks at once. Each keeps its own pinned apps, edge, orientation,
  theme, size and layer, and is added or removed from the Docks submenu. The
  global shortcut and the `--toggle` and `--preferences` commands act on all of
  them together.

### Changed
- The settings file now holds a `docks` list with the shortcut beside it. A file
  written by an earlier version is read as a single dock and its shortcut lifted
  out, so nothing is lost on upgrade.

### Fixed
- A dock discarded at runtime now stops its animation timer before deletion,
  rather than ticking against a half-torn-down widget.

## [0.4.0] - 2026-09-07

### Added
- Horizontal and vertical orientation for a free-positioned dock, so it can be
  rotated into a column. A docked dock still follows the edge it is attached to.

### Changed
- Free positioning now places the visible shelf rather than its padded window,
  and moves that padding to whichever side has room. The dock sits flush against
  a screen edge instead of stopping a tooltip's height short of it.
- Preferences draws its own chevron on every drop-down and restyles the popup
  lists, replacing Qt's raised arrow.
- The project is licensed under MIT with a named maintainer, so the package now
  passes `lintian` with no findings.

## [0.3.0] - 2026-09-07

### Added
- Free positioning: drag the dock anywhere on screen instead of only snapping to
  an edge. Auto-hide and the edge hotspot are disabled in that mode, leaving the
  shortcut, tray and menu to hide it.
- Control of the desktop's own panel from the dock menu and preferences — hide or
  show it, move it to any edge, and open its settings. XFCE is driven through
  `xfconf`; MATE and Cinnamon through their GSettings keys; LXQt, KDE, GNOME and
  Budgie offer their settings dialog only. Unsupported actions are greyed out and
  explained rather than failing silently.

### Changed
- Renamed the project to FlowDocks. The package, command, icon, manual page and
  configuration directory all moved; settings and pinned apps are read once from
  the previous configuration directory so nothing is lost.

### Fixed
- A launcher dropped from the desktop, file manager or applications menu is now
  matched to the installed application by path, desktop-file ID and finally its
  `Exec` line. A copy or symlink of an installed entry previously failed to pin.

## [0.2.0] - 2026-09-07

Released under the project's former name.

### Added
- Above and Below screen layers alongside the default normal layer.
- A global show/hide shortcut, configurable or disabled, plus `--toggle` and
  `--preferences` commands that reach the running dock over a local socket.
- A configurable screen-edge hotspot: reveal, toggle or disabled.
- Dragging icons from the application picker onto the dock, reordering pinned
  icons, and dragging an icon off the dock to unpin it.
- Dragging the dock itself to any screen edge on any monitor, with an optional
  position lock.
- A no-root, architecture-independent Debian package with desktop actions, a
  scalable icon and a manual page.

### Changed
- The dock defaults to the normal window layer, so other windows can cover it
  rather than it being permanently on top.

### Fixed
- The dock starts even when its control socket cannot bind, losing only
  `--toggle` and `--preferences`.
- `gio launch` no longer blocks on pipes inherited by the launched application.

## [0.1.0] - 2026-09-07

Initial version: a translucent animated dock with application discovery,
pinned shortcuts, running-window indicators, preferences and optional autostart.

[0.5.0]: https://github.com/HashirAhmad-dev/flowdocks/releases/tag/v0.5.0
[0.4.0]: https://github.com/HashirAhmad-dev/flowdocks/releases/tag/v0.4.0
[0.3.0]: https://github.com/HashirAhmad-dev/flowdocks/releases/tag/v0.3.0
[0.2.0]: https://github.com/HashirAhmad-dev/flowdocks/releases/tag/v0.2.0
[0.1.0]: https://github.com/HashirAhmad-dev/flowdocks/releases/tag/v0.1.0
