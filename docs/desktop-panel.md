# Desktop panel control

FlowDocks sits alongside your desktop's own panel rather than replacing it, and
can drive that panel so you do not have to leave the dock to rearrange your
desktop. Right-click the dock, or open Preferences, for **Hide/Show panel**,
**Move panel** to any edge, and **Panel settings**.

Nothing happens on start-up. The panel is read when you open the menu, and
written only when you pick an action.

## Support by desktop

| Desktop | Hide / show | Move | Settings | Driven through |
| --- | --- | --- | --- | --- |
| XFCE — Kali, Xubuntu, Debian XFCE | yes | yes | yes | `xfconf-query` |
| MATE | yes | yes | no | GSettings |
| Cinnamon | yes | yes | yes | GSettings |
| LXQt | no | no | yes | `lxqt-config-panel` |
| KDE Plasma | no | no | yes | System Settings |
| GNOME | no | no | yes | Settings |
| Budgie | no | no | yes | Budgie Desktop Settings |

Actions a desktop cannot support are greyed out with the reason shown, never
silently ignored. GNOME's top bar cannot be hidden or moved without a shell
extension, and LXQt only reads its panel file at start-up, so those offer their
settings dialog instead.

The desktop is detected from `XDG_CURRENT_DESKTOP`, falling back to
`DESKTOP_SESSION`. Budgie and Cinnamon both also advertise GNOME, so they are
matched first.

## What FlowDocks writes

Only the current user's own configuration, through the desktop's supported tool.
Never as root, and never a configuration file edited behind the tool's back.

**XFCE** — for each panel listed in `/panels`:

| Setting | Property |
| --- | --- |
| Hide / show | `/panels/panel-N/autohide-behavior` — `2` to hide, `0` to show |
| Move | `/panels/panel-N/mode` and `/panels/panel-N/position` |

The snap values were confirmed against a live `xfce4-panel`:

| Edge | `mode` | `position` |
| --- | --- | --- |
| Top | `0` (horizontal) | `p=6;x=0;y=0` |
| Bottom | `0` (horizontal) | `p=8;x=0;y=0` |
| Left | `1` (vertical) | `p=5;x=0;y=0` |
| Right | `1` (vertical) | `p=2;x=0;y=0` |

The `x` and `y` fields are ignored once a panel is snapped to an edge. On a fresh
XFCE install `autohide-behavior` does not exist yet, so FlowDocks creates it with
`xfconf-query -n` and updates it with `-s` afterwards.

**MATE** — `auto-hide` and `orientation` on each
`org.mate.panel.toplevel:/org/mate/panel/toplevels/<name>/`.

**Cinnamon** — the `panels-autohide` and `panels-enabled` string lists on
`org.cinnamon`, rewriting only the field that changes.

## Verification

XFCE support is verified end to end. The opt-in test suite starts a private
Xvfb, D-Bus session and `xfce4-panel` with a disposable configuration, moves that
panel to all four edges, hides and shows it, and checks the resulting window
geometry each time:

```bash
FLOWDOCKS_TEST_PANEL=1 python3 -m unittest discover -s tests -p test_panel.py -v
```

The other desktops use their documented settings keys and are covered by unit
tests with mocked commands, but have not been verified on a live session of each
desktop. If you run one of them and something misbehaves, please open an issue —
see [CONTRIBUTING.md](../CONTRIBUTING.md) for how to add or correct a controller.

## If the panel overlaps the dock

Hide the panel from the dock menu, move one of the two to another edge, or put
FlowDocks in free positioning and place it wherever you like.
