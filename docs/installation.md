# Installation

FlowDocks runs on the distribution's own Python and Qt. There is nothing to
compile, no virtual environment is required, and it never needs root to run.

## From the release package

Download `flowdocks_<version>_all.deb` from the
[releases page](https://github.com/HashirAhmad-dev/flowdocks/releases), then:

```bash
sudo apt install ./flowdocks_0.5.0_all.deb
flowdocks
```

`apt` pulls in the dependencies. The package installs:

| Path | Contents |
| --- | --- |
| `/usr/bin/flowdocks` | Launcher script |
| `/usr/share/flowdocks/` | Application code |
| `/usr/share/applications/flowdocks.desktop` | Menu entry, with Toggle and Preferences actions |
| `/usr/share/icons/hicolor/scalable/apps/flowdocks.svg` | Icon |
| `/usr/share/man/man1/flowdocks.1.gz` | Manual page |
| `/usr/share/doc/flowdocks/` | README, changelog, copyright |

Installing enables no autostart and changes no desktop panel. Verify the download
first if you like:

```bash
sha256sum --check flowdocks_0.5.0_all.deb.sha256
```

Uninstall with `sudo apt remove flowdocks`. Your settings stay in your home
directory; remove `~/.config/flowdocks/` to clear them.

## From source

Running from source is for PrismoVector and its licensees; the published source
carries no right to use or redistribute it. See [LICENSE](../LICENSE). To simply
use FlowDocks, install the release package above.

```bash
sudo apt install python3-pyqt6 x11-utils xdotool libglib2.0-bin papirus-icon-theme
git clone https://github.com/HashirAhmad-dev/flowdocks.git
cd flowdocks
python3 main.py
```

Or against a virtual environment instead of the distribution's Qt:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Do not use `sudo pip` or `--break-system-packages` on a Debian-based system.

## Building the package yourself

```bash
./scripts/build-deb.sh
```

The build needs no root. It copies an explicit list of runtime files — never a
virtual environment, cache or working tree — into a temporary staging directory
and produces `dist/flowdocks_<version>_all.deb` plus a SHA-256 checksum.

## Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.10 or newer | Kali Rolling and current Debian/Ubuntu all satisfy this |
| PyQt6 6.4 or newer | `python3-pyqt6`. A Qt 6.4 runtime is supported and CI-verified |
| X11 session | The supported target; see [Troubleshooting](troubleshooting.md) for Wayland |
| `x11-utils`, `xdotool` | Running-window indicators and window activation |
| `libglib2.0-bin` | `gio launch`, which honours desktop-entry field codes |
| `papirus-icon-theme` | Recommended, not required — improves icon coverage |

On a minimal system Qt's xcb platform plugin may also need `libxcb-cursor0` and
`libxkbcommon-x11-0`.

## Autostart

Autostart is off by default and is enabled explicitly in Preferences. It writes
`~/.config/autostart/flowdocks.desktop` pointing at the interpreter that was
running at the time, so if you move a source checkout, disable and re-enable it.
