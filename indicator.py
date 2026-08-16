#!/usr/bin/env python3
import gi
import signal
import sys

gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3

def main():
    indicator = AyatanaAppIndicator3.Indicator.new(
        "theia_dictate-indicator",
        "media-record", # Icono de grabación rojo estándar de Ubuntu
        AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
    
    menu = Gtk.Menu()
    item = Gtk.MenuItem(label="🔴 TheIA Dictate escuchando...")
    menu.append(item)
    menu.show_all()
    indicator.set_menu(menu)
    
    Gtk.main()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    main()
