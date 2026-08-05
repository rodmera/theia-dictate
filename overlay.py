#!/usr/bin/env python3
"""Overlay visual en vivo (estado de grabación) para SuperDictate.

Complementa al tray (`indicator.py`) con un estado de texto simple y robusto:
  - mode "overlay": notifica decíclic para mostrar el estado mientras graba.
  - mode "tray": delega al indicator existente (por defecto).

Sin dependencias GTK obligatorias de terceros: si no hay gi/ayatana, se usa
notify-send como fallback de estado.
"""

import argparse
import os
import signal
import subprocess
import sys

PIDS_FILE = "/tmp/super-dictate-overlay.pid"


def _notify(msg, icon="media-record", expire=10000):
    subprocess.run(["notify-send", "-t", str(expire), "-i", icon, "SuperDictate", msg])


def _write_self_pid():
    try:
        with open(PIDS_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="tray", choices=["tray", "overlay"])
    ap.add_argument("--msg", default="Escuchando...")
    args = ap.parse_args()

    _write_self_pid()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    if args.mode == "overlay":
        # Overlay ligero: estado visible mientras el proceso de grabación corre.
        _notify(args.msg, "media-record", 60000)
        # Mantener vivo hasta que lo maten (pkill en stop_and_transcribe)
        import time
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    # Modo tray: reusar indicator.py si está disponible; si no, un notify.
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import Gtk, AyatanaAppIndicator3

        indicator = AyatanaAppIndicator3.Indicator.new(
            "superdictate-overlay",
            "media-record",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="🔴 SuperDictate escuchando...")
        menu.append(item)
        menu.show_all()
        indicator.set_menu(menu)
        Gtk.main()
    except Exception:
        _notify(args.msg, "media-record", 60000)
        import time
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
