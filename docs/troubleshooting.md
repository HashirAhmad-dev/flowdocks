# Troubleshooting

## The dock looks opaque instead of translucent

Enable compositing: **Settings → Window Manager Tweaks → Compositor**. The
surface is painted glass, not live background blur, so it will not sample what is
behind it either way.

## The global shortcut does nothing

Another application already owns that combination; FlowDocks reports this rather
than stealing it. On XFCE, `Ctrl+Alt+D` is Show Desktop, which is why the default
is `Ctrl+Alt+A`. Check what is bound:

```bash
xfconf-query -c xfce4-keyboard-shortcuts -l -v | grep -i "alt"
```

Pick a free combination in Preferences, or turn the built-in shortcut off and
bind `flowdocks --toggle` in **Settings → Keyboard → Application Shortcuts**.

Key grabs are resolved against the keyboard layout in use when they are
registered, so re-apply the shortcut after changing layouts.

## An application will not pin when I drag it

A dropped launcher must resolve to an application that is actually installed —
matched by path, desktop-file ID, then `Exec` line. Copies and symlinks of
installed entries work. A `.desktop` file for something not installed is refused
on purpose, so a downloaded file cannot become a dock shortcut.

To pin a custom command, install its entry in `~/.local/share/applications/`,
then choose **Refresh applications** from the dock menu.

## An application shows no running indicator

Window matching uses the desktop-file ID, the executable name and
`StartupWMClass`. Applications with an unusual window class may not match. Check
what the window actually reports:

```bash
xprop WM_CLASS   # then click the window
```

If it differs from the entry, add a matching `StartupWMClass=` line to a copy of
the entry in `~/.local/share/applications/` and refresh.

## `--toggle` says the dock did not respond

The running dock could not be reached over its control socket. Usually it is not
actually running — start it with `flowdocks`. If the dock printed
`--toggle and --preferences are unavailable` at start-up, its socket could not be
created; see [Configuration](configuration.md#single-instance-and-the-control-socket).

## Panel actions are greyed out

Your desktop does not support that action from the command line. See
[Desktop panel control](desktop-panel.md) for the support table and the reason
shown for each desktop.

## The dock will not sit where I want it

In edge positioning the dock snaps to the nearest screen edge. Switch Positioning
to **Free** to place it anywhere, including flush against an edge, and use
Orientation to rotate it into a column. If it will not move at all, check that
"Lock dock position" is off.

## Running headless or over SSH

The default test suite uses Qt's offscreen platform and needs no display. If a
headless run aborts with `Gtk-WARNING ... cannot open display`, it is the GTK
platform-theme plugin, not FlowDocks. Clear it:

```bash
QT_QPA_PLATFORMTHEME= QT_QPA_PLATFORM=offscreen python3 main.py --smoke-test
```

## Wayland

X11 is the supported target. On Wayland, application discovery and launching
work, but Qt cannot universally position top-level windows, and there is no
portable way to take a global key grab, read the window list or set stacking
hints. Positioning, the edge hotspot, the global shortcut, screen layers and
running-window indicators therefore depend on the compositor and are generally
unavailable. Use an X11 session for the complete experience.

## Reporting a problem

Run `flowdocks` from a terminal, reproduce the problem, and include anything it
prints along with your distribution, desktop and session type:

```bash
echo "$XDG_CURRENT_DESKTOP $XDG_SESSION_TYPE"
```
