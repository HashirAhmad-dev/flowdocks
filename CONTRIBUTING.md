# Contributing to FlowDocks

FlowDocks is proprietary software, not open source. The source is published to
be read, and this document exists for PrismoVector and anyone it has authorised
to work on the project.

**Before contributing:** unsolicited pull requests are not accepted. If you
would like to contribute, contact info@prismovector.com first. Any contribution
that is accepted must be assigned to PrismoVector, which holds the copyright in
the whole work. Nothing here grants a right to use, copy, modify or redistribute
the software — see [LICENSE](LICENSE).

Bug reports and feature requests are welcome from anyone, through
[issues](https://github.com/HashirAhmad-dev/flowdocks/issues).

## Development setup

FlowDocks runs on the distribution's own Python and Qt; there is nothing to
compile and no virtual environment is required.

```bash
sudo apt install python3-pyqt6 x11-utils xdotool libglib2.0-bin
git clone https://github.com/HashirAhmad-dev/flowdocks.git
cd flowdocks
python3 main.py
```

To develop against a virtual environment instead of the system Qt:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Never use `sudo pip` or `--break-system-packages` on a Debian-based system.

## Project layout

| Path | Responsibility |
| --- | --- |
| `flowdocks/app.py` | Process lifecycle, single-instance lock, control socket |
| `flowdocks/backend.py` | Desktop-entry discovery, safe launching, settings |
| `flowdocks/panel.py` | Control of the desktop's own panel, per desktop |
| `flowdocks/ui.py` | The painted dock, dialogs and interaction |
| `flowdocks/x11.py` | Global shortcut grabs and EWMH window layers |
| `packaging/` | Debian control files, icon, manual page, changelog |
| `scripts/build-deb.sh` | No-root package build |
| `tests/` | Unit, offscreen UI and isolated integration tests |

Two boundaries are worth preserving:

- **`backend.py` and `panel.py` import no Qt.** They are plain Python so they can
  be tested without a display, and so the UI never blocks on desktop I/O.
- **Nothing runs through a shell.** Applications, `xfconf-query`, `xprop` and
  `xdotool` are all invoked as argument lists with timeouts. Desktop entries may
  themselves ask for a shell; FlowDocks never adds one.

## Tests

The default suite needs no display — the UI tests use Qt's offscreen platform.

```bash
python3 -m unittest discover -s tests -v
```

Four opt-in suites exercise real integration. Each starts its own Xvfb, D-Bus
session and window manager, and never touches the invoking desktop:

```bash
FLOWDOCKS_TEST_GIO=1 \
FLOWDOCKS_TEST_X11=1 \
FLOWDOCKS_TEST_PANEL=1 \
FLOWDOCKS_TEST_APP=1 \
  python3 -m unittest discover -s tests -v
```

| Variable | What it adds |
| --- | --- |
| `FLOWDOCKS_TEST_GIO` | A real `gio launch` probe, using a temporary `sleep` entry |
| `FLOWDOCKS_TEST_X11` | Real passive key grabs and EWMH layer changes under Xvfb |
| `FLOWDOCKS_TEST_PANEL` | Moves and hides a private `xfce4-panel` |
| `FLOWDOCKS_TEST_APP` | Drives the whole application, including pointer drags |

They need `xvfb`, `xauth`, `xfwm4`, `xfce4-panel`, `dbus-daemon`, `x11-utils`
and `xdotool`.

## What a change should come with

- A test that fails without the change. Prefer the offscreen UI tests for
  interaction, and the opt-in suites when real window-manager behaviour matters.
- No new runtime dependency. The dock deliberately relies only on PyQt6 and the
  X11 command-line tools already present on a Debian desktop.
- Comments that say *why*, not *what*. The existing code explains the
  non-obvious: sun_path limits, xfconf snap values, Xlib error-handler scope.
- A `packaging/changelog` entry when the change is user-visible, and a matching
  update to `README.md` or the manual page if behaviour changed.
- `lintian dist/flowdocks_*.deb` clean after `./scripts/build-deb.sh`.

## Adding support for another desktop's panel

`flowdocks/panel.py` maps a desktop to a controller. To add one, subclass
`Panel`, set the `can_hide` / `can_move` / `can_settings` flags honestly, and
return a readable message from any method rather than raising. If an action
cannot be done reliably, leave its flag `False` and set `reason` — the menu
greys it out and shows the explanation. Verify against a real panel before
claiming support, and say in the pull request how you verified it.
