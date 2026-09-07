"""Exercise the Debian build in an isolated copy, never the real dist directory."""

import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    "main.py",
    "flowdocks/__init__.py",
    "flowdocks/app.py",
    "flowdocks/backend.py",
    "flowdocks/panel.py",
    "flowdocks/ui.py",
    "flowdocks/x11.py",
)


@unittest.skipUnless(shutil.which("dpkg-deb"), "Debian packaging tools required")
class PackageTests(unittest.TestCase):
    def test_build_payload_and_metadata(self):
        with tempfile.TemporaryDirectory(prefix="nexus-package-test-") as temporary:
            project = Path(temporary) / "project with spaces"
            for name in (*RUNTIME, "README.md", "scripts/build-deb.sh"):
                target = project / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
            shutil.copytree(ROOT / "packaging", project / "packaging")
            for name in (".env", "flowdocks/secret.py", "flowdocks/__pycache__/cache.pyc"):
                target = project / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("must not be packaged\n")
            subprocess.run(
                ["sh", str(project / "scripts/build-deb.sh")],
                cwd=temporary, check=True, capture_output=True, text=True,
            )
            package = project / "dist/flowdocks_0.6.0_all.deb"
            for field, expected in (
                ("Package", "flowdocks"),
                ("Version", "0.6.0"),
                ("Architecture", "all"),
                ("Maintainer", "PrismoVector <info@prismovector.com>"),
                ("Depends", "python3 (>= 3.10), python3-pyqt6 (>= 6.4), libx11-6, x11-utils, xdotool, libglib2.0-bin"),
                ("Recommends", "papirus-icon-theme"),
            ):
                result = subprocess.check_output(["dpkg-deb", "-f", str(package), field], text=True)
                self.assertEqual(result.strip(), expected)
            checksum = package.with_suffix(".deb.sha256").read_text()
            self.assertEqual(checksum, f"{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n")
            payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                files = {}
                for member in archive.getmembers():
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    if member.isdir():
                        self.assertEqual(member.mode, 0o755)
                        continue
                    self.assertTrue(member.isfile())
                    name = member.name.removeprefix("./")
                    files[name] = archive.extractfile(member).read()
                    self.assertEqual(member.mode, 0o755 if name == "usr/bin/flowdocks" else 0o644)
                expected = {f"usr/share/flowdocks/{name}" for name in RUNTIME}
                expected.update((
                    "usr/bin/flowdocks",
                    "usr/share/applications/flowdocks.desktop",
                    "usr/share/icons/hicolor/scalable/apps/flowdocks.svg",
                    "usr/share/doc/flowdocks/README.md",
                    "usr/share/doc/flowdocks/changelog.gz",
                    "usr/share/doc/flowdocks/copyright",
                    "usr/share/man/man1/flowdocks.1.gz",
                ))
                self.assertEqual(set(files), expected)
                for name in RUNTIME:
                    self.assertEqual(files[f"usr/share/flowdocks/{name}"], (project / name).read_bytes())
                self.assertEqual(files["usr/bin/flowdocks"], b'#!/bin/sh\nexec /usr/bin/python3 /usr/share/flowdocks/main.py "$@"\n')
                self.assertEqual(
                    gzip.decompress(files["usr/share/doc/flowdocks/changelog.gz"]),
                    (project / "packaging/changelog").read_bytes(),
                )
                desktop = files["usr/share/applications/flowdocks.desktop"].decode()
                self.assertIn("Exec=flowdocks --toggle\n", desktop)
                self.assertIn("Exec=flowdocks --preferences\n", desktop)
                self.assertEqual(
                    gzip.decompress(files["usr/share/man/man1/flowdocks.1.gz"]),
                    (project / "packaging/flowdocks.1").read_bytes(),
                )

            unpacked = Path(temporary) / "unpacked"
            subprocess.run(["dpkg-deb", "--extract", str(package), str(unpacked)], check=True)
            environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "DISPLAY", "WAYLAND_DISPLAY"):
                environment.pop(name, None)
            for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
                directory = Path(temporary) / name.lower()
                directory.mkdir(mode=0o700)
                environment[name] = str(directory)
            environment.update(
                QT_QPA_PLATFORM="offscreen", QT_QPA_PLATFORMTHEME="", QT_STYLE_OVERRIDE="Fusion",
                PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
            )
            for arguments in (("--help",), ("--smoke-test",), ("--preferences", "--smoke-test")):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        ["/usr/bin/python3", str(unpacked / "usr/share/flowdocks/main.py"), *arguments],
                        cwd=temporary, env=environment, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if arguments == ("--help",):
                        self.assertIn("--toggle", result.stdout)
                        self.assertIn("--preferences", result.stdout)


if __name__ == "__main__":
    unittest.main()
