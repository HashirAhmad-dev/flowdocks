"""Application lifecycle and per-display single-instance guard."""

import argparse
import hashlib
import os
import sys
import time

from PyQt6.QtCore import QLockFile, QStandardPaths, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from .backend import SettingsStore
from .ui import DockManager


def main(argv=None):
    parser = argparse.ArgumentParser(description="FlowDocks: an application dock for Linux / X11")
    parser.add_argument("--smoke-test", action="store_true", help="Render briefly, then exit without saving settings")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--toggle", action="store_true", help="Toggle the running dock, or start it if not running")
    commands.add_argument("--preferences", action="store_true", help="Open preferences in the running dock")
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("FlowDocks")
    app.setApplicationDisplayName("FlowDocks")
    app.setOrganizationName("FlowDocks")
    app.setQuitOnLastWindowClosed(False)
    if not QIcon.themeName():
        QIcon.setThemeName("Papirus-Dark")
    QIcon.setFallbackThemeName("hicolor")

    display = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY", "headless")
    key = hashlib.sha256(display.encode()).hexdigest()[:12]
    runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    lock = QLockFile(f"{runtime}/flowdocks-{key}.lock")
    socket_name = f"{runtime}/flowdocks-{key}.socket"
    command = b"preferences\n" if args.preferences else b"toggle\n" if args.toggle else b"show\n"
    if not args.smoke_test and not lock.tryLock(100):
        socket = QLocalSocket()
        # The owner takes its lock before constructing the UI and control server.
        deadline = time.monotonic() + 3
        connected = False
        while time.monotonic() < deadline:
            socket.abort()
            socket.connectToServer(socket_name)
            if socket.waitForConnected(100):
                connected = True
                break
            time.sleep(0.05)
        if connected:
            socket.write(command)
            socket.flush()
            if socket.bytesToWrite():
                socket.waitForBytesWritten(1500)
            socket.waitForReadyRead(1500)
            acknowledged = bytes(socket.readAll()) == b"ok\n"
            socket.disconnectFromServer()
            if acknowledged:
                return 0
        print("The running dock did not respond. Quit the older instance and restart FlowDocks.", file=sys.stderr)
        return 1

    manager = DockManager(SettingsStore(), smoke_test=args.smoke_test)
    server = QLocalServer(app)
    clients = set()

    def receive(client):
        if not client.canReadLine():
            if client.bytesAvailable() > 64:
                client.disconnectFromServer()
            return
        message = bytes(client.readLine(64)).strip()
        if message == b"toggle":
            manager.toggle_all()
        elif message == b"preferences":
            manager.open_preferences()
        elif message == b"show":
            manager.reveal_all()
        else:
            client.disconnectFromServer()
            return
        client.write(b"ok\n")
        client.flush()
        client.disconnectFromServer()

    def accept_connections():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            clients.add(client)
            client.readyRead.connect(lambda client=client: receive(client))
            client.disconnected.connect(lambda client=client: clients.discard(client))
            client.disconnected.connect(client.deleteLater)
            timeout = QTimer(client)
            timeout.setSingleShot(True)
            timeout.timeout.connect(client.disconnectFromServer)
            timeout.start(3000)
            receive(client)

    if not args.smoke_test:
        QLocalServer.removeServer(socket_name)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if server.listen(socket_name):
            server.newConnection.connect(accept_connections)
        else:
            # A very long or unwritable runtime path only costs the control socket;
            # the dock itself still works through the mouse, tray and shortcut.
            print(f"FlowDocks: --toggle and --preferences are unavailable "
                  f"({server.errorString()}).", file=sys.stderr)
    for dock in manager.docks:
        dock.show()
    if args.preferences:
        QTimer.singleShot(0, manager.open_preferences)
    if args.smoke_test:
        QTimer.singleShot(1200, app.quit)
    result = app.exec()
    server.close()
    manager.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
